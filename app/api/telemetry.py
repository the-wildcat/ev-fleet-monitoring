"""POST /api/v1/telemetry: vehicles (devices) push readings here.

Request:
    POST /api/v1/telemetry
    X-API-Key: evk_...
    Content-Type: application/json

    {"lat": 28.61, "lon": 77.21, "speed_kmh": 42.5, "soc_pct": 76.2, "is_charging": false,
     "battery_temp_c": 31.4, "odometer_km": 15234.7, "recorded_at": "2026-10-04T09:30:00Z"}

    or a batch (max 100): {"readings": [{...}, {...}]}

Responses: 201 {"accepted": n}, 400 validation errors, 401 bad/missing key, 403 inactive vehicle.
"""

from flask import abort, current_app, jsonify, request

from app.api import bp
from app.extensions import db
from app.models import Vehicle, VehicleStatus
from app.services.telemetry import parse_reading, record_reading


def _authenticated_vehicle() -> Vehicle:
    key = request.headers.get("X-API-Key", "").strip()
    vehicle = None
    if key:
        vehicle = db.session.execute(
            db.select(Vehicle).filter_by(api_key_hash=Vehicle.hash_api_key(key))
        ).scalar_one_or_none()
    if vehicle is None:
        abort(401, description="Missing or invalid X-API-Key header.")
    if vehicle.status == VehicleStatus.INACTIVE:
        abort(403, description="This vehicle is inactive and can't send telemetry.")
    return vehicle


@bp.post("/telemetry")
def ingest_telemetry():
    vehicle = _authenticated_vehicle()

    body = request.get_json(silent=True)
    if not isinstance(body, dict):
        abort(400, description="Request body must be a JSON object.")

    readings = body.get("readings", [body])
    max_batch = current_app.config["TELEMETRY_MAX_BATCH"]
    if not isinstance(readings, list) or not 1 <= len(readings) <= max_batch:
        abort(400, description=f"`readings` must be a list of 1 to {max_batch} readings.")

    parsed = [parse_reading(raw) for raw in readings]
    problems = [{"index": i, "errors": errs} for i, (_, errs) in enumerate(parsed) if errs]
    if problems:
        # All-or-nothing: a batch is either stored completely or not at all.
        return jsonify(error="Bad Request", message="Validation failed.", details=problems), 400

    for clean, _ in sorted(parsed, key=lambda p: p[0]["recorded_at"]):
        record_reading(vehicle, clean)
    db.session.commit()
    return jsonify(accepted=len(parsed), vehicle_id=vehicle.id), 201
