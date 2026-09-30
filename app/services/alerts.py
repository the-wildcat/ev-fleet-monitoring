"""Rule-based alert engine.

Rules are evaluated whenever a vehicle's state changes (each new telemetry reading, each
saved battery check). An alert stays open while its condition holds, is updated in place
(including severity changes) and is resolved automatically once the condition clears.
Clearing thresholds sit a little past the raising thresholds (hysteresis) so a value that
hovers around a limit doesn't open and close alerts repeatedly.
"""

from __future__ import annotations

from flask import current_app
from sqlalchemy import case

from app.extensions import db
from app.models import Alert, AlertSeverity, AlertType, Telemetry, Vehicle
from app.services.battery_model import GOOD_SOH, REPLACE_SOH
from app.utils import utcnow

# Severity is stored as text, so sort explicitly: critical before warning.
CRITICAL_FIRST = case((Alert.severity == AlertSeverity.CRITICAL, 0), else_=1)


def open_alerts_for(
    vehicle_ids: list[int], types: tuple[AlertType, ...] | None = None, limit: int | None = None
) -> list[Alert]:
    """Open alerts for these vehicles, most severe and newest first."""
    if not vehicle_ids:
        return []
    stmt = db.select(Alert).where(Alert.vehicle_id.in_(vehicle_ids), Alert.resolved_at.is_(None))
    if types:
        stmt = stmt.where(Alert.alert_type.in_(types))
    stmt = stmt.order_by(CRITICAL_FIRST, Alert.created_at.desc()).limit(limit)
    return list(db.session.execute(stmt).scalars())


def _open_alert(vehicle_id: int, alert_type: AlertType) -> Alert | None:
    return db.session.execute(
        db.select(Alert).filter_by(vehicle_id=vehicle_id, alert_type=alert_type, resolved_at=None)
    ).scalar_one_or_none()


def raise_alert(
    vehicle: Vehicle, alert_type: AlertType, severity: AlertSeverity, message: str
) -> Alert:
    """Open an alert, or refresh the existing open one of the same type."""
    alert = _open_alert(vehicle.id, alert_type)
    now = utcnow()
    if alert is None:
        alert = Alert(
            vehicle_id=vehicle.id,
            alert_type=alert_type,
            severity=severity,
            message=message,
            created_at=now,
            last_seen_at=now,
        )
        db.session.add(alert)
        db.session.flush()  # make it visible to later lookups in this transaction
        current_app.logger.info(
            "Alert raised: %s %s (%s)", vehicle.plate_number, alert_type, severity
        )
    else:
        if severity != alert.severity:
            # Escalation needs fresh attention, so clear any earlier acknowledgement.
            if severity == AlertSeverity.CRITICAL:
                alert.acknowledged_at = None
                alert.acknowledged_by_id = None
            alert.severity = severity
        alert.message = message
        alert.last_seen_at = now
    return alert


def resolve_alert(vehicle: Vehicle, alert_type: AlertType) -> bool:
    alert = _open_alert(vehicle.id, alert_type)
    if alert is None:
        return False
    alert.resolved_at = utcnow()
    current_app.logger.info("Alert resolved: %s %s", vehicle.plate_number, alert_type)
    return True


def evaluate_telemetry(vehicle: Vehicle, reading: Telemetry) -> None:
    """Battery charge and temperature rules for the vehicle's newest reading."""
    cfg = current_app.config

    # Low battery: only while not plugged in.
    soc = reading.soc_pct
    if not reading.is_charging and soc < cfg["ALERT_LOW_SOC_PCT"]:
        severity = (
            AlertSeverity.CRITICAL if soc < cfg["ALERT_CRITICAL_SOC_PCT"] else AlertSeverity.WARNING
        )
        range_km = vehicle.battery_capacity_kwh * soc / 100 * vehicle.efficiency_km_per_kwh
        raise_alert(
            vehicle,
            AlertType.LOW_BATTERY,
            severity,
            f"Battery at {soc:.0f}% (about {range_km:.0f} km left). Plan a charging stop.",
        )
    elif reading.is_charging or soc >= cfg["ALERT_LOW_SOC_PCT"] + 5:
        resolve_alert(vehicle, AlertType.LOW_BATTERY)

    # Overheating.
    temp = reading.battery_temp_c
    if temp is not None:
        if temp >= cfg["ALERT_OVERHEAT_C"]:
            severity = (
                AlertSeverity.CRITICAL
                if temp >= cfg["ALERT_CRITICAL_OVERHEAT_C"]
                else AlertSeverity.WARNING
            )
            raise_alert(
                vehicle,
                AlertType.BATTERY_OVERHEAT,
                severity,
                f"Battery temperature {temp:.1f} °C. Reduce load and let the pack cool.",
            )
        elif temp <= cfg["ALERT_OVERHEAT_C"] - 3:
            resolve_alert(vehicle, AlertType.BATTERY_OVERHEAT)


def evaluate_battery_health(vehicle: Vehicle, soh_pct: float) -> None:
    """Battery wear rule, run when a battery check is saved."""
    if soh_pct < GOOD_SOH:
        severity = AlertSeverity.CRITICAL if soh_pct < REPLACE_SOH else AlertSeverity.WARNING
        advice = (
            "Replace the battery." if soh_pct < REPLACE_SOH else "Schedule a battery inspection."
        )
        raise_alert(
            vehicle,
            AlertType.BATTERY_DEGRADED,
            severity,
            f"Battery health is {soh_pct:.0f}% of original capacity. {advice}",
        )
    else:
        resolve_alert(vehicle, AlertType.BATTERY_DEGRADED)
