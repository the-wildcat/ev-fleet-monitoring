"""Rule-based maintenance alerts.

Rules (thresholds in config):
  * Service due      routine service every SERVICE_INTERVAL_KM or SERVICE_INTERVAL_DAYS,
                     warning when due (or within 500 km), critical when SERVICE_OVERDUE_MARGIN_KM
                     past the interval
  * Brake inspection BRAKE_EVENTS_PER_WEEK or more harsh-braking events in 7 days
  * Battery check    no battery health check for BATTERY_CHECK_INTERVAL_DAYS
  * Device offline   an active vehicle silent for DEVICE_OFFLINE_HOURS

Each rule raises or refreshes its alert while the condition holds and resolves it when it no
longer does (e.g. after a service record is added). `run_maintenance_checks` evaluates every
vehicle and is run periodically by the background jobs and by `flask check-maintenance`.
"""

from __future__ import annotations

from datetime import timedelta

from flask import current_app

from app.extensions import db
from app.models import (
    AlertSeverity,
    AlertType,
    BatteryCheck,
    ServiceRecord,
    ServiceType,
    Vehicle,
    VehicleStatus,
)
from app.services.alerts import raise_alert, resolve_alert
from app.services.driving import harsh_brakes_by_vehicle
from app.utils import utcnow

DUE_SOON_KM = 500


def _last_service(vehicle: Vehicle, service_type: ServiceType) -> ServiceRecord | None:
    return db.session.execute(
        db.select(ServiceRecord)
        .filter_by(vehicle_id=vehicle.id, service_type=service_type)
        .order_by(ServiceRecord.service_date.desc(), ServiceRecord.id.desc())
        .limit(1)
    ).scalar_one_or_none()


def check_service_due(vehicle: Vehicle) -> None:
    cfg = current_app.config
    last = _last_service(vehicle, ServiceType.ROUTINE)
    base_km = last.odometer_km if last and last.odometer_km is not None else 0.0
    base_day = last.service_date if last else vehicle.created_at.date()
    km_since = (vehicle.odometer_km or 0) - base_km
    days_since = (utcnow().date() - base_day).days
    interval_km, interval_days = cfg["SERVICE_INTERVAL_KM"], cfg["SERVICE_INTERVAL_DAYS"]
    since = "since the last service" if last else "since registration"

    if km_since >= interval_km + cfg["SERVICE_OVERDUE_MARGIN_KM"]:
        raise_alert(
            vehicle,
            AlertType.SERVICE_DUE,
            AlertSeverity.CRITICAL,
            f"Service overdue: {km_since:,.0f} km {since} (interval {interval_km:,} km).",
        )
    elif km_since >= interval_km - DUE_SOON_KM or days_since >= interval_days:
        reason = (
            f"{days_since} days {since}"
            if days_since >= interval_days
            else f"{km_since:,.0f} km {since}"
        )
        raise_alert(
            vehicle,
            AlertType.SERVICE_DUE,
            AlertSeverity.WARNING,
            f"Routine service due: {reason}. Book a service.",
        )
    else:
        resolve_alert(vehicle, AlertType.SERVICE_DUE)


def check_brakes(vehicle: Vehicle, harsh_brakes_7d: int) -> None:
    threshold = current_app.config["BRAKE_EVENTS_PER_WEEK"]
    last_brake_service = _last_service(vehicle, ServiceType.BRAKES)
    serviced_recently = last_brake_service and (
        utcnow().date() - last_brake_service.service_date
    ) <= timedelta(days=7)
    if harsh_brakes_7d >= threshold and not serviced_recently:
        raise_alert(
            vehicle,
            AlertType.BRAKE_INSPECTION,
            AlertSeverity.WARNING,
            f"{harsh_brakes_7d} harsh-braking events in the last 7 days. "
            "Inspect brake pads and discs, and review driving with the driver.",
        )
    elif harsh_brakes_7d < threshold / 2 or serviced_recently:
        resolve_alert(vehicle, AlertType.BRAKE_INSPECTION)


def check_battery_check_due(vehicle: Vehicle) -> None:
    interval = current_app.config["BATTERY_CHECK_INTERVAL_DAYS"]
    last = db.session.execute(
        db.select(BatteryCheck.created_at)
        .filter_by(vehicle_id=vehicle.id)
        .order_by(BatteryCheck.created_at.desc())
        .limit(1)
    ).scalar_one_or_none()
    since = last or vehicle.created_at
    days = (utcnow() - since).days
    if days >= interval:
        what = "last battery check" if last else "registration with no battery check"
        raise_alert(
            vehicle,
            AlertType.BATTERY_CHECK_DUE,
            AlertSeverity.WARNING,
            f"{days} days since {what}. Run a battery health check.",
        )
    else:
        resolve_alert(vehicle, AlertType.BATTERY_CHECK_DUE)


def check_device_offline(vehicle: Vehicle) -> None:
    hours = current_app.config["DEVICE_OFFLINE_HOURS"]
    if vehicle.status != VehicleStatus.ACTIVE or vehicle.last_seen_at is None:
        # Inactive vehicles are expected to be silent; brand-new ones haven't reported yet.
        resolve_alert(vehicle, AlertType.DEVICE_OFFLINE)
        return
    silent = utcnow() - vehicle.last_seen_at
    if silent >= timedelta(hours=hours):
        raise_alert(
            vehicle,
            AlertType.DEVICE_OFFLINE,
            AlertSeverity.WARNING,
            f"No data for {silent.total_seconds() / 3600:.0f} hours. "
            "Check the vehicle's telematics device and connectivity.",
        )
    else:
        resolve_alert(vehicle, AlertType.DEVICE_OFFLINE)


def evaluate_vehicle(vehicle: Vehicle, harsh_brakes_7d: int | None = None) -> None:
    if harsh_brakes_7d is None:
        harsh_brakes_7d = harsh_brakes_by_vehicle(utcnow() - timedelta(days=7)).get(vehicle.id, 0)
    check_service_due(vehicle)
    check_brakes(vehicle, harsh_brakes_7d)
    check_battery_check_due(vehicle)
    check_device_offline(vehicle)


def run_maintenance_checks() -> int:
    """Evaluate every vehicle that isn't decommissioned. Returns how many were checked."""
    brakes = harsh_brakes_by_vehicle(utcnow() - timedelta(days=7))
    vehicles = (
        db.session.execute(db.select(Vehicle).where(Vehicle.status != VehicleStatus.INACTIVE))
        .scalars()
        .all()
    )
    for vehicle in vehicles:
        evaluate_vehicle(vehicle, brakes.get(vehicle.id, 0))
    # Inactive vehicles shouldn't keep stale maintenance alerts open.
    for vehicle in db.session.execute(
        db.select(Vehicle).where(Vehicle.status == VehicleStatus.INACTIVE)
    ).scalars():
        for alert_type in (AlertType.DEVICE_OFFLINE, AlertType.BRAKE_INSPECTION):
            resolve_alert(vehicle, alert_type)
    db.session.commit()
    return len(vehicles)
