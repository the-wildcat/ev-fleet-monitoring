"""Customisable reports: definitions and the data behind them.

Each report type declares its columns and a builder that returns rows (dicts keyed by column)
plus summary figures. Builders reuse the same calculations as the app's pages (energy
analysis, driver scoring, alerts), so a report always matches what users see on screen.
Exporting to CSV / Excel / PDF lives in app/services/exporters.py.
"""

from __future__ import annotations

from collections.abc import Callable
from dataclasses import dataclass, field
from datetime import date

from flask_login import current_user
from sqlalchemy import func

from app.extensions import db
from app.models import Alert, BatteryCheck, DailyEnergy, ServiceRecord, Vehicle
from app.services.driving import driver_stats
from app.services.energy import analyse, utc_bounds

MAX_DAYS = 366
MAX_ROWS = 50_000


@dataclass(frozen=True)
class Column:
    key: str
    label: str
    # text | int | float1 | float2 | inr (whole rupees) | inr2 (rupees and paise) | pct | date
    # | datetime
    kind: str = "text"


@dataclass
class ReportData:
    rows: list[dict]
    summary: list[tuple[str, str]] = field(default_factory=list)  # (label, formatted value)
    note: str | None = None


@dataclass(frozen=True)
class ReportDef:
    key: str
    title: str
    description: str
    columns: tuple[Column, ...]
    build: Callable[[list[Vehicle], date, date], ReportData]
    uses_vehicle_filter: bool = True

    def column(self, key: str) -> Column:
        return next(c for c in self.columns if c.key == key)


def _inr(value: float) -> str:
    return f"₹{value:,.0f}"


# --- builders ---------------------------------------------------------------------------------


def _fleet_summary(vehicles: list[Vehicle], first: date, last: date) -> ReportData:
    report = analyse(vehicles, first, last)
    start, end = utc_bounds(first, last)
    ids = [v.id for v in vehicles]
    alert_counts = dict(
        db.session.execute(
            db.select(Alert.vehicle_id, func.count())
            .where(Alert.vehicle_id.in_(ids), Alert.created_at >= start, Alert.created_at < end)
            .group_by(Alert.vehicle_id)
        ).all()
    )
    checks = db.session.execute(
        db.select(BatteryCheck)
        .where(BatteryCheck.vehicle_id.in_(ids), BatteryCheck.created_at < end)
        .order_by(BatteryCheck.created_at)
    ).scalars()
    latest_soh = {c.vehicle_id: c for c in checks}  # ascending order: the newest one wins

    rows = []
    for ve in sorted(report.vehicles, key=lambda v: v.vehicle.plate_number):
        v = ve.vehicle
        check = latest_soh.get(v.id)
        rows.append(
            {
                "plate": v.plate_number,
                "vehicle": v.display_name,
                "driver": v.driver.name if v.driver else "",
                "status": v.status.label,
                "distance_km": ve.distance_km,
                "energy_kwh": ve.used_kwh,
                "kwh_per_100km": ve.kwh_per_100km,
                "vs_rated_pct": ve.vs_rated_pct,
                "energy_cost": ve.energy_cost,
                "maintenance_cost": ve.maintenance_cost,
                "total_cost": ve.total_cost,
                "cost_per_km": ve.cost_per_km,
                "alerts": alert_counts.get(v.id, 0),
                "battery_soh": check.predicted_soh_pct if check else None,
                "odometer_km": v.odometer_km,
            }
        )
    return ReportData(
        rows=rows,
        summary=[
            ("Vehicles", str(len(rows))),
            ("Distance", f"{report.distance_km:,.0f} km"),
            ("Energy used", f"{report.used_kwh:,.0f} kWh"),
            ("Operating cost", _inr(report.total_cost)),
            ("Cost per km", f"₹{report.cost_per_km:,.2f}" if report.cost_per_km else "—"),
            ("Saved vs petrol", _inr(report.savings_vs_petrol)),
            ("CO₂ avoided", f"{report.co2_avoided_kg:,.0f} kg"),
        ],
    )


def _energy_daily(vehicles: list[Vehicle], first: date, last: date) -> ReportData:
    by_id = {v.id: v for v in vehicles}
    rows = []
    for d in db.session.execute(
        db.select(DailyEnergy)
        .where(
            DailyEnergy.vehicle_id.in_(list(by_id)),
            DailyEnergy.day >= first,
            DailyEnergy.day <= last,
        )
        .order_by(DailyEnergy.day, DailyEnergy.vehicle_id)
    ).scalars():
        v = by_id[d.vehicle_id]
        rows.append(
            {
                "day": d.day,
                "plate": v.plate_number,
                "distance_km": d.distance_km,
                "energy_kwh": d.energy_used_kwh,
                "kwh_per_100km": d.energy_used_kwh / d.distance_km * 100 if d.distance_km else None,
                "grid_kwh": d.grid_energy_kwh,
                "energy_cost": d.energy_cost_inr,
                "charging_cost": d.charging_cost_inr,
                "tariff": d.tariff_inr_per_kwh,
            }
        )
    return ReportData(
        rows=rows,
        summary=[
            ("Vehicle-days", str(len(rows))),
            ("Distance", f"{sum(r['distance_km'] for r in rows):,.0f} km"),
            ("Energy used", f"{sum(r['energy_kwh'] for r in rows):,.1f} kWh"),
            ("Energy cost", _inr(sum(r["energy_cost"] for r in rows))),
            ("Charging spend", _inr(sum(r["charging_cost"] for r in rows))),
        ],
    )


def _driver_behaviour(vehicles: list[Vehicle], first: date, last: date) -> ReportData:
    start, end = utc_bounds(first, last)
    # Drivers only ever see their own driving; managers see every driver.
    ids = None if current_user.is_manager else [current_user.id]
    rows = [
        {
            "driver": s.name,
            "email": s.email,
            "vehicles": ", ".join(sorted(s.vehicles)),
            "distance_km": s.distance_km,
            "harsh_brake": s.harsh_brake,
            "harsh_accel": s.harsh_accel,
            "speeding": s.speeding,
            "events_per_100km": s.per_100km(s.events),
            "score": s.score,
            "band": s.band,
            "energy_vs_rated_pct": s.energy_vs_rated_pct,
            "extra_cost_per_100km": s.extra_cost_per_100km_inr,
        }
        for s in driver_stats(start, end, ids)
    ]
    scored = [r["score"] for r in rows if r["score"] is not None]
    return ReportData(
        rows=rows,
        summary=[
            ("Drivers", str(len(rows))),
            ("Average score", f"{sum(scored) / len(scored):.0f}" if scored else "—"),
            ("Needing coaching", str(sum(1 for s in scored if s < 70))),
        ],
        note="Covers all of each driver's driving in the period (vehicle filter not applied).",
    )


def _alerts(vehicles: list[Vehicle], first: date, last: date) -> ReportData:
    start, end = utc_bounds(first, last)
    by_id = {v.id: v for v in vehicles}
    rows = []
    for a in db.session.execute(
        db.select(Alert)
        .where(Alert.vehicle_id.in_(list(by_id)), Alert.created_at >= start, Alert.created_at < end)
        .order_by(Alert.created_at)
    ).scalars():
        if a.resolved_at is None:
            resolution = "Open"
        elif a.resolved_by:
            resolution = f"Resolved by {a.resolved_by.name}"
        else:
            resolution = "Cleared automatically"
        rows.append(
            {
                "raised": a.created_at,
                "plate": by_id[a.vehicle_id].plate_number,
                "type": a.alert_type.label,
                "category": a.alert_type.category.title(),
                "severity": a.severity.value.title(),
                "message": a.message,
                "acknowledged_by": a.acknowledged_by.name if a.acknowledged_by else "",
                "status": resolution,
                "resolved": a.resolved_at,
                "note": a.resolution_note or "",
            }
        )
    return ReportData(
        rows=rows,
        summary=[
            ("Alerts", str(len(rows))),
            ("Critical", str(sum(r["severity"] == "Critical" for r in rows))),
            ("Still open", str(sum(r["status"] == "Open" for r in rows))),
        ],
    )


def _service_history(vehicles: list[Vehicle], first: date, last: date) -> ReportData:
    by_id = {v.id: v for v in vehicles}
    rows = [
        {
            "date": s.service_date,
            "plate": by_id[s.vehicle_id].plate_number,
            "type": s.service_type.label,
            "odometer_km": s.odometer_km,
            "cost": s.cost_inr,
            "description": s.description,
        }
        for s in db.session.execute(
            db.select(ServiceRecord)
            .where(
                ServiceRecord.vehicle_id.in_(list(by_id)),
                ServiceRecord.service_date >= first,
                ServiceRecord.service_date <= last,
            )
            .order_by(ServiceRecord.service_date)
        ).scalars()
    ]
    return ReportData(
        rows=rows,
        summary=[
            ("Service records", str(len(rows))),
            ("Total cost", _inr(sum(r["cost"] or 0 for r in rows))),
        ],
    )


# --- registry ---------------------------------------------------------------------------------

REPORTS: dict[str, ReportDef] = {
    r.key: r
    for r in [
        ReportDef(
            key="fleet_summary",
            title="Fleet summary",
            description="One row per vehicle: distance, energy, costs, alerts and battery health.",
            columns=(
                Column("plate", "Registration"),
                Column("vehicle", "Vehicle"),
                Column("driver", "Driver"),
                Column("status", "Status"),
                Column("distance_km", "Distance (km)", "float1"),
                Column("energy_kwh", "Energy used (kWh)", "float1"),
                Column("kwh_per_100km", "kWh/100 km", "float1"),
                Column("vs_rated_pct", "vs rated", "pct"),
                Column("energy_cost", "Energy cost", "inr"),
                Column("maintenance_cost", "Maintenance", "inr"),
                Column("total_cost", "Total cost", "inr"),
                Column("cost_per_km", "Cost/km", "inr2"),
                Column("alerts", "Alerts", "int"),
                Column("battery_soh", "Battery health (%)", "float1"),
                Column("odometer_km", "Odometer (km)", "int"),
            ),
            build=_fleet_summary,
        ),
        ReportDef(
            key="energy_daily",
            title="Energy & cost by day",
            description="Daily energy use, charging and cost per vehicle.",
            columns=(
                Column("day", "Date", "date"),
                Column("plate", "Registration"),
                Column("distance_km", "Distance (km)", "float1"),
                Column("energy_kwh", "Energy used (kWh)", "float2"),
                Column("kwh_per_100km", "kWh/100 km", "float1"),
                Column("grid_kwh", "Charged from grid (kWh)", "float2"),
                Column("energy_cost", "Energy cost", "inr"),
                Column("charging_cost", "Charging spend", "inr"),
                Column("tariff", "Tariff (₹/kWh)", "float2"),
            ),
            build=_energy_daily,
        ),
        ReportDef(
            key="driver_behaviour",
            title="Driver behaviour",
            description="Scores, harsh events and energy impact per driver.",
            columns=(
                Column("driver", "Driver"),
                Column("email", "Email"),
                Column("vehicles", "Vehicles"),
                Column("distance_km", "Distance (km)", "float1"),
                Column("harsh_brake", "Harsh braking", "int"),
                Column("harsh_accel", "Harsh acceleration", "int"),
                Column("speeding", "Speeding", "int"),
                Column("events_per_100km", "Events/100 km", "float1"),
                Column("score", "Score", "float1"),
                Column("band", "Rating"),
                Column("energy_vs_rated_pct", "Energy vs rated", "pct"),
                Column("extra_cost_per_100km", "Extra cost/100 km", "inr"),
            ),
            build=_driver_behaviour,
            uses_vehicle_filter=False,
        ),
        ReportDef(
            key="alerts",
            title="Alerts",
            description="Every alert raised in the period with its outcome.",
            columns=(
                Column("raised", "Raised", "datetime"),
                Column("plate", "Registration"),
                Column("type", "Alert"),
                Column("category", "Category"),
                Column("severity", "Severity"),
                Column("message", "Details"),
                Column("acknowledged_by", "Acknowledged by"),
                Column("status", "Status"),
                Column("resolved", "Resolved", "datetime"),
                Column("note", "Resolution note"),
            ),
            build=_alerts,
        ),
        ReportDef(
            key="service_history",
            title="Service history",
            description="Maintenance work and costs.",
            columns=(
                Column("date", "Date", "date"),
                Column("plate", "Registration"),
                Column("type", "Type"),
                Column("odometer_km", "Odometer (km)", "int"),
                Column("cost", "Cost", "inr"),
                Column("description", "Work done"),
            ),
            build=_service_history,
        ),
    ]
}
