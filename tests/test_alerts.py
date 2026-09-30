from app.extensions import db
from app.models import Alert, AlertSeverity, AlertType, Telemetry
from app.services.alerts import evaluate_battery_health, evaluate_telemetry
from app.utils import utcnow

URL = "/api/v1/telemetry"


def _reading(vehicle, soc=50.0, charging=False, temp=30.0):
    return Telemetry(
        vehicle_id=vehicle.id,
        recorded_at=utcnow(),
        lat=28.6,
        lon=77.2,
        speed_kmh=30,
        soc_pct=soc,
        is_charging=charging,
        battery_temp_c=temp,
    )


def _alerts(vehicle, alert_type=None, open_only=True):
    stmt = db.select(Alert).filter_by(vehicle_id=vehicle.id)
    if alert_type:
        stmt = stmt.filter_by(alert_type=alert_type)
    if open_only:
        stmt = stmt.filter_by(resolved_at=None)
    return list(db.session.execute(stmt).scalars())


def test_low_battery_raises_once_and_escalates(app, make_vehicle):
    vehicle, _ = make_vehicle()
    evaluate_telemetry(vehicle, _reading(vehicle, soc=18))
    evaluate_telemetry(vehicle, _reading(vehicle, soc=17))  # still low: same alert, no duplicate
    alerts = _alerts(vehicle, AlertType.LOW_BATTERY)
    assert len(alerts) == 1 and alerts[0].severity == AlertSeverity.WARNING
    assert "17%" in alerts[0].message

    alerts[0].acknowledged_at = utcnow()
    evaluate_telemetry(vehicle, _reading(vehicle, soc=8))
    assert alerts[0].severity == AlertSeverity.CRITICAL
    assert alerts[0].acknowledged_at is None  # escalation needs fresh attention


def test_low_battery_resolves_with_hysteresis_or_charging(app, make_vehicle):
    vehicle, _ = make_vehicle()
    evaluate_telemetry(vehicle, _reading(vehicle, soc=15))
    evaluate_telemetry(vehicle, _reading(vehicle, soc=22))  # above 20 but below 25: stays open
    assert len(_alerts(vehicle, AlertType.LOW_BATTERY)) == 1
    evaluate_telemetry(vehicle, _reading(vehicle, soc=26))
    assert _alerts(vehicle, AlertType.LOW_BATTERY) == []

    evaluate_telemetry(vehicle, _reading(vehicle, soc=12))
    evaluate_telemetry(vehicle, _reading(vehicle, soc=12, charging=True))  # plugged in
    assert _alerts(vehicle, AlertType.LOW_BATTERY) == []
    assert len(_alerts(vehicle, AlertType.LOW_BATTERY, open_only=False)) == 2  # history kept


def test_no_low_battery_alert_while_charging(app, make_vehicle):
    vehicle, _ = make_vehicle()
    evaluate_telemetry(vehicle, _reading(vehicle, soc=5, charging=True))
    assert _alerts(vehicle) == []


def test_overheat_alert_levels_and_resolution(app, make_vehicle):
    vehicle, _ = make_vehicle()
    evaluate_telemetry(vehicle, _reading(vehicle, temp=47))
    (alert,) = _alerts(vehicle, AlertType.BATTERY_OVERHEAT)
    assert alert.severity == AlertSeverity.WARNING
    evaluate_telemetry(vehicle, _reading(vehicle, temp=57))
    assert alert.severity == AlertSeverity.CRITICAL
    evaluate_telemetry(vehicle, _reading(vehicle, temp=43))  # within 3 °C of limit: stays
    assert alert.resolved_at is None
    evaluate_telemetry(vehicle, _reading(vehicle, temp=40))
    assert alert.resolved_at is not None


def test_battery_degradation_bands(app, make_vehicle):
    vehicle, _ = make_vehicle()
    evaluate_battery_health(vehicle, 80)
    assert _alerts(vehicle) == []
    evaluate_battery_health(vehicle, 70)
    (alert,) = _alerts(vehicle, AlertType.BATTERY_DEGRADED)
    assert alert.severity == AlertSeverity.WARNING and "inspection" in alert.message
    evaluate_battery_health(vehicle, 45)
    assert alert.severity == AlertSeverity.CRITICAL and "Replace" in alert.message
    evaluate_battery_health(vehicle, 90)  # e.g. after a battery replacement
    assert alert.resolved_at is not None


def test_telemetry_api_creates_alerts(client, make_vehicle):
    vehicle, key = make_vehicle()
    reading = {"lat": 28.6, "lon": 77.2, "speed_kmh": 30, "soc_pct": 9, "battery_temp_c": 50}
    assert client.post(URL, json=reading, headers={"X-API-Key": key}).status_code == 201
    types = {a.alert_type for a in _alerts(vehicle)}
    assert types == {AlertType.LOW_BATTERY, AlertType.BATTERY_OVERHEAT}


def test_out_of_order_reading_does_not_trigger_alerts(client, make_vehicle):
    from datetime import timedelta

    vehicle, key = make_vehicle()
    now = utcnow()
    fresh = {
        "lat": 28.6,
        "lon": 77.2,
        "speed_kmh": 30,
        "soc_pct": 60,
        "recorded_at": now.isoformat() + "Z",
    }
    stale = {**fresh, "soc_pct": 5, "recorded_at": (now - timedelta(minutes=10)).isoformat() + "Z"}
    client.post(URL, json=fresh, headers={"X-API-Key": key})
    client.post(URL, json=stale, headers={"X-API-Key": key})
    assert _alerts(vehicle) == []  # an old reading says nothing about the battery now


def test_dashboard_shows_alerts(client, manager, make_vehicle):
    vehicle, _ = make_vehicle()
    evaluate_telemetry(vehicle, _reading(vehicle, soc=5))
    db.session.commit()
    page = client.get("/").data
    assert b"Active alerts" in page and b"Low battery" in page
