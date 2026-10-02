"""Validating and storing telemetry readings.

Both the device API and the simulator go through `parse_reading` + `record_reading`, so
simulated data takes exactly the same path as data from a real vehicle.
"""

from datetime import UTC, datetime, timedelta

from sqlalchemy import delete

from app.extensions import db
from app.models import Telemetry, Vehicle
from app.services.alerts import evaluate_telemetry
from app.utils import utcnow

# field -> (required, min, max)
_NUMERIC_FIELDS: dict[str, tuple[bool, float, float]] = {
    "lat": (True, -90, 90),
    "lon": (True, -180, 180),
    "speed_kmh": (True, 0, 250),
    "soc_pct": (True, 0, 100),
    "battery_temp_c": (False, -40, 120),
    "odometer_km": (False, 0, 5_000_000),
    "acceleration_mps2": (False, -20, 20),
    "power_kw": (False, -500, 500),
}
_MAX_CLOCK_SKEW = timedelta(minutes=5)
_MAX_AGE = timedelta(days=30)


def parse_reading(raw: object) -> tuple[dict, dict[str, str]]:
    """Validate one raw reading. Returns (clean_data, errors); errors is empty when valid."""
    if not isinstance(raw, dict):
        return {}, {"_": "Each reading must be a JSON object."}

    clean: dict = {}
    errors: dict[str, str] = {}

    for field, (required, lo, hi) in _NUMERIC_FIELDS.items():
        value = raw.get(field)
        if value is None:
            if required:
                errors[field] = "This field is required."
            continue
        # bool is a subclass of int in Python; reject it so `true` isn't read as 1.
        if isinstance(value, bool) or not isinstance(value, int | float):
            errors[field] = "Must be a number."
        elif not lo <= value <= hi:
            errors[field] = f"Must be between {lo} and {hi}."
        else:
            clean[field] = float(value)

    is_charging = raw.get("is_charging", False)
    if not isinstance(is_charging, bool):
        errors["is_charging"] = "Must be true or false."
    else:
        clean["is_charging"] = is_charging

    recorded_at = raw.get("recorded_at")
    if recorded_at is None:
        clean["recorded_at"] = utcnow()
    else:
        parsed = _parse_timestamp(recorded_at)
        now = utcnow()
        if parsed is None:
            errors["recorded_at"] = "Must be an ISO 8601 timestamp, e.g. 2026-10-04T09:30:00Z."
        elif parsed > now + _MAX_CLOCK_SKEW:
            errors["recorded_at"] = "Timestamp is in the future."
        elif parsed < now - _MAX_AGE:
            errors["recorded_at"] = "Timestamp is more than 30 days old."
        else:
            clean["recorded_at"] = parsed

    return clean, errors


def _parse_timestamp(value: object) -> datetime | None:
    """Parse ISO 8601 into naive UTC (the storage convention); None if invalid."""
    if not isinstance(value, str):
        return None
    try:
        parsed = datetime.fromisoformat(value.replace("Z", "+00:00"))
    except ValueError:
        return None
    if parsed.tzinfo is not None:
        parsed = parsed.astimezone(UTC).replace(tzinfo=None)
    return parsed


def record_reading(vehicle: Vehicle, data: dict) -> Telemetry:
    """Store a validated reading and refresh the vehicle's latest state. Caller commits."""
    reading = Telemetry(vehicle_id=vehicle.id, driver_id=vehicle.driver_id, **data)
    db.session.add(reading)

    # Devices may send buffered readings late or out of order; only newer ones update
    # the vehicle's current state (and only those are checked against alert rules).
    is_latest = vehicle.last_seen_at is None or reading.recorded_at >= vehicle.last_seen_at
    if is_latest:
        vehicle.last_lat = reading.lat
        vehicle.last_lon = reading.lon
        vehicle.last_speed_kmh = reading.speed_kmh
        vehicle.last_soc_pct = reading.soc_pct
        vehicle.last_is_charging = reading.is_charging
        vehicle.last_battery_temp_c = reading.battery_temp_c
        vehicle.last_seen_at = reading.recorded_at
    if reading.odometer_km is not None:
        vehicle.odometer_km = max(vehicle.odometer_km or 0.0, reading.odometer_km)
    if is_latest:
        evaluate_telemetry(vehicle, reading)
    return reading


def prune_old_telemetry(days: int) -> int:
    """Delete readings older than `days`. Returns the number removed. Caller commits."""
    cutoff = utcnow() - timedelta(days=days)
    result = db.session.execute(delete(Telemetry).where(Telemetry.recorded_at < cutoff))
    return result.rowcount or 0
