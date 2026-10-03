"""Energy consumption and operating-cost analysis.

Daily roll-up
-------------
Telemetry is turned into one DailyEnergy row per vehicle per local (IST) day:
  * energy used     = battery-charge drops between consecutive readings while driving
  * energy charged  = charge rises while plugged in; grid energy = charged / charging efficiency
  * distance        = odometer increases between consecutive readings
  * energy cost     = energy used / charging efficiency x tariff: the cost of the energy the
                      vehicle *consumed*, the basis for cost per km (stable no matter when the
                      vehicle charges, as in fleet cost accounting)
  * charging cost   = grid energy x tariff: what was actually spent on charging that day
The tariff in force is stored with each day.
Consecutive readings are compared across midnight, so nothing is lost at day boundaries.
Raw telemetry is pruned after TELEMETRY_RETENTION_DAYS; the daily rows keep the history.

Analysis
--------
`analyse()` combines daily rows with service-record costs for a period: totals, cost per km,
consumption vs each vehicle's rating, savings vs a petrol car and CO2 avoided.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from datetime import UTC, date, datetime, time, timedelta
from zoneinfo import ZoneInfo

import numpy as np
import pandas as pd
from flask import current_app

from app.extensions import db
from app.models import DailyEnergy, ServiceRecord, Telemetry, Vehicle
from app.services.settings import get_setting
from app.utils import utcnow

MAX_ODOMETER_STEP_KM = 200  # larger jumps between readings are resets/glitches, not driving


def _tz() -> ZoneInfo:
    return ZoneInfo(current_app.config["APP_TIMEZONE"])


def local_today() -> date:
    return datetime.now(_tz()).date()


def utc_bounds(first: date, last: date) -> tuple[datetime, datetime]:
    """Naive-UTC [start of `first`, start of the day after `last`) in local time."""
    tz = _tz()
    start = datetime.combine(first, time.min, tz).astimezone(UTC).replace(tzinfo=None)
    end = datetime.combine(last + timedelta(days=1), time.min, tz).astimezone(UTC)
    return start, end.replace(tzinfo=None)


def rollup(first: date, last: date) -> int:
    """(Re)compute DailyEnergy rows for local days first..last. Returns rows written."""
    start, end = utc_bounds(first, last)
    # Include the last reading before the window so the first reading has a predecessor.
    rows = db.session.execute(
        db.select(
            Telemetry.vehicle_id,
            Telemetry.recorded_at,
            Telemetry.soc_pct,
            Telemetry.is_charging,
            Telemetry.odometer_km,
        )
        .where(Telemetry.recorded_at >= start - timedelta(hours=6), Telemetry.recorded_at < end)
        .order_by(Telemetry.vehicle_id, Telemetry.recorded_at)
    ).all()
    if not rows:
        return 0

    df = pd.DataFrame(rows, columns=["vehicle_id", "at", "soc", "charging", "odo"])
    grouped = df.groupby("vehicle_id")
    df["prev_soc"] = grouped["soc"].shift()
    df["prev_charging"] = grouped["charging"].shift()
    df["odo_step"] = grouped["odo"].diff()
    df = df[df["at"] >= start].copy()
    if df.empty:
        return 0

    driving = ~df["charging"].astype(bool) & ~df["prev_charging"].fillna(True).astype(bool)
    df["used_pct"] = np.where(driving & (df.prev_soc > df.soc), df.prev_soc - df.soc, 0.0)
    df["added_pct"] = np.where(
        df["charging"].astype(bool) & (df.soc > df.prev_soc), df.soc - df.prev_soc, 0.0
    )
    step = df["odo_step"].fillna(0)
    df["km"] = np.where((step > 0) & (step <= MAX_ODOMETER_STEP_KM), step, 0.0)
    tz = _tz()
    df["day"] = pd.to_datetime(df["at"]).dt.tz_localize("UTC").dt.tz_convert(tz).dt.date

    daily = (
        df.groupby(["vehicle_id", "day"])
        .agg(
            km=("km", "sum"),
            used=("used_pct", "sum"),
            added=("added_pct", "sum"),
            readings=("soc", "size"),
        )
        .reset_index()
    )
    capacity = dict(
        db.session.execute(
            db.select(Vehicle.id, Vehicle.battery_capacity_kwh).where(
                Vehicle.id.in_(daily.vehicle_id.unique().tolist())
            )
        ).all()
    )
    existing = {
        (r.vehicle_id, r.day): r
        for r in db.session.execute(
            db.select(DailyEnergy).where(DailyEnergy.day >= first, DailyEnergy.day <= last)
        ).scalars()
    }
    tariff = get_setting("energy_tariff_inr_per_kwh")
    efficiency = get_setting("charging_efficiency_pct") / 100

    written = 0
    for r in daily.itertuples(index=False):
        if r.vehicle_id not in capacity:
            continue
        cap = capacity[r.vehicle_id]
        row = existing.get((r.vehicle_id, r.day))
        if row is None:
            # A day's tariff is fixed when it is first rolled up; later tariff changes
            # don't rewrite history.
            row = DailyEnergy(vehicle_id=int(r.vehicle_id), day=r.day, tariff_inr_per_kwh=tariff)
            db.session.add(row)
        charged = r.added / 100 * cap
        row.distance_km = round(float(r.km), 3)
        row.energy_used_kwh = round(float(r.used / 100 * cap), 4)
        row.battery_charged_kwh = round(float(charged), 4)
        row.grid_energy_kwh = round(float(charged / efficiency), 4)
        row.energy_cost_inr = round(row.energy_used_kwh / efficiency * row.tariff_inr_per_kwh, 2)
        row.charging_cost_inr = round(row.grid_energy_kwh * row.tariff_inr_per_kwh, 2)
        row.readings = int(r.readings)
        written += 1
    db.session.commit()
    return written


def rollup_recent() -> int:
    """Refresh yesterday (now complete) and today (in progress)."""
    today = local_today()
    return rollup(today - timedelta(days=1), today)


# --- analysis ---------------------------------------------------------------------------------


@dataclass
class VehicleEnergy:
    vehicle: Vehicle
    distance_km: float = 0.0
    used_kwh: float = 0.0
    grid_kwh: float = 0.0
    energy_cost: float = 0.0
    charging_cost: float = 0.0
    maintenance_cost: float = 0.0

    @property
    def total_cost(self) -> float:
        return self.energy_cost + self.maintenance_cost

    @property
    def cost_per_km(self) -> float | None:
        return self.total_cost / self.distance_km if self.distance_km > 0 else None

    @property
    def kwh_per_100km(self) -> float | None:
        return self.used_kwh / self.distance_km * 100 if self.distance_km > 0 else None

    @property
    def rated_kwh_per_100km(self) -> float:
        return 100 / self.vehicle.efficiency_km_per_kwh

    @property
    def vs_rated_pct(self) -> float | None:
        actual = self.kwh_per_100km
        if actual is None:
            return None
        return (actual - self.rated_kwh_per_100km) / self.rated_kwh_per_100km * 100


@dataclass
class EnergyReport:
    first: date
    last: date
    vehicles: list[VehicleEnergy]
    daily: list[dict] = field(default_factory=list)
    settings: dict = field(default_factory=dict)

    def _sum(self, attr: str) -> float:
        return sum(getattr(v, attr) for v in self.vehicles)

    @property
    def distance_km(self) -> float:
        return self._sum("distance_km")

    @property
    def used_kwh(self) -> float:
        return self._sum("used_kwh")

    @property
    def grid_kwh(self) -> float:
        return self._sum("grid_kwh")

    @property
    def energy_cost(self) -> float:
        return self._sum("energy_cost")

    @property
    def charging_cost(self) -> float:
        return self._sum("charging_cost")

    @property
    def maintenance_cost(self) -> float:
        return self._sum("maintenance_cost")

    @property
    def total_cost(self) -> float:
        return self.energy_cost + self.maintenance_cost

    @property
    def cost_per_km(self) -> float | None:
        return self.total_cost / self.distance_km if self.distance_km > 0 else None

    @property
    def energy_cost_per_km(self) -> float | None:
        return self.energy_cost / self.distance_km if self.distance_km > 0 else None

    @property
    def kwh_per_100km(self) -> float | None:
        return self.used_kwh / self.distance_km * 100 if self.distance_km > 0 else None

    @property
    def petrol_litres(self) -> float:
        return self.distance_km / self.settings["ice_km_per_l"]

    @property
    def petrol_cost(self) -> float:
        return self.petrol_litres * self.settings["petrol_price_inr_per_l"]

    @property
    def savings_vs_petrol(self) -> float:
        """Fuel saved: petrol cost for the same distance minus electricity cost."""
        return self.petrol_cost - self.energy_cost

    @property
    def co2_ev_kg(self) -> float:
        """Grid emissions for the energy consumed (incl. charging losses)."""
        consumed_from_grid = self.used_kwh / (self.settings["charging_efficiency_pct"] / 100)
        return consumed_from_grid * current_app.config["GRID_EMISSION_KG_PER_KWH"]

    @property
    def co2_avoided_kg(self) -> float:
        petrol_co2 = self.petrol_litres * current_app.config["PETROL_EMISSION_KG_PER_L"]
        return petrol_co2 - self.co2_ev_kg


def _empty_day() -> dict[str, float]:
    return {
        "km": 0.0,
        "used": 0.0,
        "grid": 0.0,
        "energy_cost": 0.0,
        "charging_cost": 0.0,
        "maintenance_cost": 0.0,
    }


def analyse(vehicles: list[Vehicle], first: date, last: date) -> EnergyReport:
    ids = [v.id for v in vehicles]
    per_vehicle = {v.id: VehicleEnergy(v) for v in vehicles}
    daily: dict[date, dict] = {}

    rows = (
        db.session.execute(
            db.select(DailyEnergy).where(
                DailyEnergy.vehicle_id.in_(ids), DailyEnergy.day >= first, DailyEnergy.day <= last
            )
        ).scalars()
        if ids
        else []
    )
    for r in rows:
        ve = per_vehicle[r.vehicle_id]
        ve.distance_km += r.distance_km
        ve.used_kwh += r.energy_used_kwh
        ve.grid_kwh += r.grid_energy_kwh
        ve.energy_cost += r.energy_cost_inr
        ve.charging_cost += r.charging_cost_inr
        d = daily.setdefault(r.day, _empty_day())
        d["km"] += r.distance_km
        d["used"] += r.energy_used_kwh
        d["grid"] += r.grid_energy_kwh
        d["energy_cost"] += r.energy_cost_inr
        d["charging_cost"] += r.charging_cost_inr

    services = (
        db.session.execute(
            db.select(ServiceRecord).where(
                ServiceRecord.vehicle_id.in_(ids),
                ServiceRecord.service_date >= first,
                ServiceRecord.service_date <= last,
                ServiceRecord.cost_inr.is_not(None),
            )
        ).scalars()
        if ids
        else []
    )
    for s in services:
        per_vehicle[s.vehicle_id].maintenance_cost += s.cost_inr
        d = daily.setdefault(s.service_date, _empty_day())
        d["maintenance_cost"] += s.cost_inr

    series = []
    day = first
    while day <= last:  # every day in the range, so charts show gaps as zero
        d = daily.get(day) or _empty_day()
        series.append({"day": day.isoformat(), **{k: round(v, 2) for k, v in d.items()}})
        day += timedelta(days=1)

    return EnergyReport(
        first=first,
        last=last,
        vehicles=sorted(per_vehicle.values(), key=lambda v: -v.total_cost),
        daily=series,
        settings={
            "ice_km_per_l": get_setting("ice_km_per_l"),
            "petrol_price_inr_per_l": get_setting("petrol_price_inr_per_l"),
            "energy_tariff_inr_per_kwh": get_setting("energy_tariff_inr_per_kwh"),
            "charging_efficiency_pct": get_setting("charging_efficiency_pct"),
        },
    )


def refresh_today_if_stale(max_age_seconds: int = 120) -> None:
    """Keep today's numbers fresh between hourly roll-ups (throttled)."""
    last = current_app.extensions.get("energy_rollup_at")
    now = utcnow()
    if last is None or (now - last).total_seconds() > max_age_seconds:
        today = local_today()
        rollup(today, today)
        current_app.extensions["energy_rollup_at"] = now
