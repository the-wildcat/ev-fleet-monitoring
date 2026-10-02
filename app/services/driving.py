"""Driver behaviour analysis from telemetry.

Events (one per reading at most, per type):
  * harsh braking       acceleration <= HARSH_BRAKE_MPS2   (about -0.35 g)
  * harsh acceleration  acceleration >= HARSH_ACCEL_MPS2   (about +0.3 g)
  * speeding            speed > SPEED_LIMIT_KMH while driving

Score = 100 - sum(weight x events per 100 km), floored at 0. Normalising by distance means a
driver who covers more kilometres isn't penalised for driving more.

Energy impact compares the energy a driver actually used (from battery-charge drops while
driving) with what the vehicle's rated efficiency predicts for the same distance. Comparing
against each vehicle's own rating, rather than a fleet average, keeps a big SUV's driver from
looking wasteful next to a small hatchback's.

All counting is done in SQL so it scales to millions of readings.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from datetime import datetime, timedelta

from flask import current_app
from sqlalchemy import and_, case, func

from app.extensions import db
from app.models import Telemetry, User, Vehicle
from app.utils import utcnow

GOOD_SCORE = 85
FAIR_SCORE = 70


def score_band(score: float | None) -> str:
    if score is None:
        return "Not enough driving"
    if score >= GOOD_SCORE:
        return "Good"
    if score >= FAIR_SCORE:
        return "Fair"
    return "Needs coaching"


@dataclass
class DriverStats:
    driver_id: int
    name: str
    email: str
    distance_km: float = 0.0
    harsh_brake: int = 0
    harsh_accel: int = 0
    speeding: int = 0
    energy_kwh: float = 0.0
    expected_kwh: float = 0.0
    vehicles: set[str] = field(default_factory=set)
    score: float | None = None

    @property
    def events(self) -> int:
        return self.harsh_brake + self.harsh_accel + self.speeding

    def per_100km(self, count: int) -> float | None:
        return count / self.distance_km * 100 if self.distance_km > 0 else None

    @property
    def kwh_per_100km(self) -> float | None:
        return self.per_100km(self.energy_kwh)

    @property
    def energy_vs_rated_pct(self) -> float | None:
        """+12.0 means 12% more energy than the vehicle's rating for this distance."""
        if self.expected_kwh <= 0:
            return None
        return (self.energy_kwh - self.expected_kwh) / self.expected_kwh * 100

    @property
    def extra_cost_per_100km_inr(self) -> float | None:
        if self.distance_km <= 0:
            return None
        tariff = current_app.config["ENERGY_TARIFF_INR_PER_KWH"]
        return (self.energy_kwh - self.expected_kwh) / self.distance_km * 100 * tariff

    @property
    def band(self) -> str:
        return score_band(self.score)


def _event_columns():
    cfg = current_app.config
    driving = Telemetry.is_charging.is_(False)
    return (
        func.sum(case((Telemetry.acceleration_mps2 <= cfg["HARSH_BRAKE_MPS2"], 1), else_=0)),
        func.sum(case((Telemetry.acceleration_mps2 >= cfg["HARSH_ACCEL_MPS2"], 1), else_=0)),
        func.sum(case((and_(driving, Telemetry.speed_kmh > cfg["SPEED_LIMIT_KMH"]), 1), else_=0)),
    )


def _soc_drop_subquery(since: datetime, until: datetime):
    """Each reading with the previous reading's charge for the same vehicle (window LAG)."""
    order = Telemetry.recorded_at
    prev_soc = func.lag(Telemetry.soc_pct).over(partition_by=Telemetry.vehicle_id, order_by=order)
    prev_charging = func.lag(Telemetry.is_charging).over(
        partition_by=Telemetry.vehicle_id, order_by=order
    )
    return (
        db.select(
            Telemetry.driver_id,
            Telemetry.vehicle_id,
            Telemetry.soc_pct,
            Telemetry.is_charging,
            prev_soc.label("prev_soc"),
            prev_charging.label("prev_charging"),
        )
        .where(Telemetry.recorded_at >= since, Telemetry.recorded_at < until)
        .subquery()
    )


def driver_stats(
    since: datetime, until: datetime, driver_ids: list[int] | None = None
) -> list[DriverStats]:
    """Behaviour and energy statistics per driver for readings in [since, until)."""
    harsh_brake, harsh_accel, speeding = _event_columns()
    in_period = and_(
        Telemetry.recorded_at >= since,
        Telemetry.recorded_at < until,
        Telemetry.driver_id.is_not(None),
    )
    if driver_ids is not None:
        in_period = and_(in_period, Telemetry.driver_id.in_(driver_ids))

    # Distance and events per (driver, vehicle): odometer span within the period.
    per_vehicle = db.session.execute(
        db.select(
            Telemetry.driver_id,
            Telemetry.vehicle_id,
            (func.max(Telemetry.odometer_km) - func.min(Telemetry.odometer_km)).label("km"),
            harsh_brake.label("harsh_brake"),
            harsh_accel.label("harsh_accel"),
            speeding.label("speeding"),
        )
        .where(in_period)
        .group_by(Telemetry.driver_id, Telemetry.vehicle_id)
    ).all()

    # Energy: sum of charge drops between consecutive driving readings, per (driver, vehicle).
    s = _soc_drop_subquery(since, until)
    drop = case(
        (
            and_(
                s.c.is_charging.is_(False),
                s.c.prev_charging.is_(False),
                s.c.prev_soc > s.c.soc_pct,
            ),
            s.c.prev_soc - s.c.soc_pct,
        ),
        else_=0,
    )
    energy_stmt = (
        db.select(s.c.driver_id, s.c.vehicle_id, func.sum(drop).label("soc_drop"))
        .where(s.c.driver_id.is_not(None))
        .group_by(s.c.driver_id, s.c.vehicle_id)
    )
    if driver_ids is not None:
        energy_stmt = energy_stmt.where(s.c.driver_id.in_(driver_ids))
    soc_drops = {(d, v): drop or 0.0 for d, v, drop in db.session.execute(energy_stmt).all()}

    vehicles = {
        v.id: v
        for v in db.session.execute(
            db.select(Vehicle).where(Vehicle.id.in_({row.vehicle_id for row in per_vehicle}))
        ).scalars()
    }
    users = {
        u.id: u
        for u in db.session.execute(
            db.select(User).where(User.id.in_({row.driver_id for row in per_vehicle}))
        ).scalars()
    }

    stats: dict[int, DriverStats] = {}
    for row in per_vehicle:
        user, vehicle = users.get(row.driver_id), vehicles.get(row.vehicle_id)
        if user is None or vehicle is None:
            continue
        st = stats.setdefault(user.id, DriverStats(user.id, user.name, user.email))
        km = float(row.km or 0)
        st.distance_km += km
        st.harsh_brake += int(row.harsh_brake or 0)
        st.harsh_accel += int(row.harsh_accel or 0)
        st.speeding += int(row.speeding or 0)
        st.energy_kwh += (
            soc_drops.get((user.id, vehicle.id), 0.0) / 100 * vehicle.battery_capacity_kwh
        )
        st.expected_kwh += km / vehicle.efficiency_km_per_kwh
        st.vehicles.add(vehicle.plate_number)

    for st in stats.values():
        st.score = compute_score(st)
    return sorted(stats.values(), key=lambda s: (s.score is None, -(s.score or 0)))


def compute_score(st: DriverStats) -> float | None:
    cfg = current_app.config
    if st.distance_km < cfg["MIN_SCORING_DISTANCE_KM"]:
        return None
    weights = cfg["SCORE_WEIGHTS"]
    penalty = sum(weights[name] * st.per_100km(getattr(st, name)) for name in weights)
    return round(max(0.0, 100.0 - penalty), 1)


def daily_trend(driver_id: int, since: datetime, until: datetime) -> list[dict]:
    """Per-day distance, events and score for one driver (for the trend chart)."""
    harsh_brake, harsh_accel, speeding = _event_columns()
    day = func.date(Telemetry.recorded_at).label("day")
    rows = db.session.execute(
        db.select(
            day,
            Telemetry.vehicle_id,
            (func.max(Telemetry.odometer_km) - func.min(Telemetry.odometer_km)).label("km"),
            harsh_brake.label("harsh_brake"),
            harsh_accel.label("harsh_accel"),
            speeding.label("speeding"),
        )
        .where(
            Telemetry.driver_id == driver_id,
            Telemetry.recorded_at >= since,
            Telemetry.recorded_at < until,
        )
        .group_by(day, Telemetry.vehicle_id)
        .order_by(day)
    ).all()

    by_day: dict[str, DriverStats] = {}
    for row in rows:
        key = str(row.day)
        st = by_day.setdefault(key, DriverStats(driver_id, "", ""))
        st.distance_km += float(row.km or 0)
        st.harsh_brake += int(row.harsh_brake or 0)
        st.harsh_accel += int(row.harsh_accel or 0)
        st.speeding += int(row.speeding or 0)
    return [
        {
            "day": key,
            "distance_km": round(st.distance_km, 1),
            "events": st.events,
            "score": compute_score(st),
        }
        for key, st in by_day.items()
    ]


def recent_events(driver_id: int, since: datetime, limit: int = 50) -> list[dict]:
    """The driver's latest harsh/speeding events with where they happened."""
    cfg = current_app.config
    rows = db.session.execute(
        db.select(Telemetry, Vehicle.plate_number)
        .join(Vehicle, Vehicle.id == Telemetry.vehicle_id)
        .where(
            Telemetry.driver_id == driver_id,
            Telemetry.recorded_at >= since,
            (Telemetry.acceleration_mps2 <= cfg["HARSH_BRAKE_MPS2"])
            | (Telemetry.acceleration_mps2 >= cfg["HARSH_ACCEL_MPS2"])
            | (Telemetry.is_charging.is_(False) & (Telemetry.speed_kmh > cfg["SPEED_LIMIT_KMH"])),
        )
        .order_by(Telemetry.recorded_at.desc())
        .limit(limit)
    ).all()
    events = []
    for reading, plate in rows:
        accel = reading.acceleration_mps2 or 0
        if accel <= cfg["HARSH_BRAKE_MPS2"]:
            kind, detail = "Harsh braking", f"{accel:.1f} m/s²"
        elif accel >= cfg["HARSH_ACCEL_MPS2"]:
            kind, detail = "Harsh acceleration", f"+{accel:.1f} m/s²"
        else:
            kind, detail = "Speeding", f"{reading.speed_kmh:.0f} km/h"
        events.append(
            {
                "at": reading.recorded_at,
                "kind": kind,
                "detail": detail,
                "speed_kmh": reading.speed_kmh,
                "lat": reading.lat,
                "lon": reading.lon,
                "plate": plate,
            }
        )
    return events


def harsh_brakes_by_vehicle(since: datetime) -> dict[int, int]:
    """Harsh-braking counts per vehicle since `since` (used by the brake-inspection rule)."""
    threshold = current_app.config["HARSH_BRAKE_MPS2"]
    rows = db.session.execute(
        db.select(Telemetry.vehicle_id, func.count())
        .where(Telemetry.recorded_at >= since, Telemetry.acceleration_mps2 <= threshold)
        .group_by(Telemetry.vehicle_id)
    ).all()
    return dict(rows)


def period_bounds(days: int) -> tuple[datetime, datetime]:
    """(start of the day `days - 1` days ago, now) in naive UTC: e.g. 7 = this week so far."""
    now = utcnow()
    start = (now - timedelta(days=days - 1)).replace(hour=0, minute=0, second=0, microsecond=0)
    return start, now
