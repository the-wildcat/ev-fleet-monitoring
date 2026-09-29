from datetime import timedelta

import pytest

from app.extensions import db
from app.models import Telemetry, VehicleStatus
from app.utils import utcnow

URL = "/api/v1/telemetry"
READING = {
    "lat": 28.6139,
    "lon": 77.2090,
    "speed_kmh": 42.5,
    "soc_pct": 76.2,
    "is_charging": False,
    "battery_temp_c": 31.4,
    "odometer_km": 15234.7,
}


def _iso(dt):
    return dt.isoformat() + "Z"


def test_single_reading_is_stored_and_updates_vehicle(client, make_vehicle):
    vehicle, key = make_vehicle()
    resp = client.post(URL, json=READING, headers={"X-API-Key": key})
    assert resp.status_code == 201
    assert resp.get_json() == {"accepted": 1, "vehicle_id": vehicle.id}

    assert db.session.query(Telemetry).count() == 1
    assert vehicle.last_soc_pct == 76.2 and vehicle.last_lat == 28.6139
    assert vehicle.odometer_km == 15234.7 and vehicle.last_seen_at is not None


def test_batch_of_readings(client, make_vehicle):
    vehicle, key = make_vehicle()
    now = utcnow()
    readings = [
        {**READING, "soc_pct": 70.0, "recorded_at": _iso(now - timedelta(seconds=10))},
        {**READING, "soc_pct": 69.0, "recorded_at": _iso(now)},
    ]
    resp = client.post(URL, json={"readings": readings}, headers={"X-API-Key": key})
    assert resp.status_code == 201 and resp.get_json()["accepted"] == 2
    assert vehicle.last_soc_pct == 69.0


def test_out_of_order_reading_does_not_overwrite_latest_state(client, make_vehicle):
    vehicle, key = make_vehicle()
    now = utcnow()
    client.post(
        URL, json={**READING, "soc_pct": 60.0, "recorded_at": _iso(now)}, headers={"X-API-Key": key}
    )
    late = {**READING, "soc_pct": 90.0, "recorded_at": _iso(now - timedelta(minutes=5))}
    client.post(URL, json=late, headers={"X-API-Key": key})
    assert vehicle.last_soc_pct == 60.0  # older reading stored but doesn't change "now"
    assert db.session.query(Telemetry).count() == 2


@pytest.mark.parametrize("headers", [{}, {"X-API-Key": "evk_wrong"}, {"X-API-Key": ""}])
def test_missing_or_bad_key_is_rejected(client, make_vehicle, headers):
    make_vehicle()
    resp = client.post(URL, json=READING, headers=headers)
    assert resp.status_code == 401
    assert resp.get_json()["error"] == "Unauthorized"
    assert db.session.query(Telemetry).count() == 0


def test_inactive_vehicle_is_rejected(client, make_vehicle):
    _, key = make_vehicle(status=VehicleStatus.INACTIVE)
    assert client.post(URL, json=READING, headers={"X-API-Key": key}).status_code == 403


@pytest.mark.parametrize(
    ("change", "field"),
    [
        ({"lat": 123}, "lat"),
        ({"soc_pct": 120}, "soc_pct"),
        ({"speed_kmh": -5}, "speed_kmh"),
        ({"speed_kmh": "fast"}, "speed_kmh"),
        ({"soc_pct": True}, "soc_pct"),  # booleans are not numbers
        ({"is_charging": "yes"}, "is_charging"),
        ({"recorded_at": "yesterday"}, "recorded_at"),
    ],
)
def test_invalid_values_are_rejected_with_field_errors(client, make_vehicle, change, field):
    _, key = make_vehicle()
    resp = client.post(URL, json={**READING, **change}, headers={"X-API-Key": key})
    assert resp.status_code == 400
    assert field in resp.get_json()["details"][0]["errors"]


def test_missing_required_field(client, make_vehicle):
    _, key = make_vehicle()
    reading = {k: v for k, v in READING.items() if k != "soc_pct"}
    resp = client.post(URL, json=reading, headers={"X-API-Key": key})
    assert resp.get_json()["details"][0]["errors"]["soc_pct"] == "This field is required."


def test_future_and_too_old_timestamps_rejected(client, make_vehicle):
    _, key = make_vehicle()
    for when in (utcnow() + timedelta(hours=1), utcnow() - timedelta(days=40)):
        resp = client.post(
            URL, json={**READING, "recorded_at": _iso(when)}, headers={"X-API-Key": key}
        )
        assert resp.status_code == 400


def test_timezone_offsets_are_converted_to_utc(client, make_vehicle):
    vehicle, key = make_vehicle()
    ist = (utcnow() + timedelta(hours=5, minutes=30)).isoformat() + "+05:30"
    client.post(URL, json={**READING, "recorded_at": ist}, headers={"X-API-Key": key})
    assert abs((vehicle.last_seen_at - utcnow()).total_seconds()) < 5


def test_batch_is_all_or_nothing(client, make_vehicle):
    _, key = make_vehicle()
    readings = [READING, {**READING, "soc_pct": 500}]
    resp = client.post(URL, json={"readings": readings}, headers={"X-API-Key": key})
    assert resp.status_code == 400
    assert resp.get_json()["details"][0]["index"] == 1
    assert db.session.query(Telemetry).count() == 0


@pytest.mark.parametrize(
    "body",
    [
        "not json",
        [READING],  # a list, not an object
        {"readings": []},
        {"readings": "nope"},
        {"readings": [READING] * 101},
    ],
)
def test_malformed_bodies_rejected(client, make_vehicle, body):
    _, key = make_vehicle()
    if isinstance(body, str):
        resp = client.post(URL, data=body, headers={"X-API-Key": key, "Content-Type": "text/plain"})
    else:
        resp = client.post(URL, json=body, headers={"X-API-Key": key})
    assert resp.status_code == 400
    assert resp.is_json


def test_api_does_not_require_csrf(app, make_vehicle):
    app.config["WTF_CSRF_ENABLED"] = True  # as in production
    _, key = make_vehicle()
    resp = app.test_client().post(URL, json=READING, headers={"X-API-Key": key})
    assert resp.status_code == 201
