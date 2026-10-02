from datetime import date, timedelta

from app.extensions import db
from app.models import (
    Alert,
    AlertSeverity,
    AlertType,
    BatteryCheck,
    ServiceRecord,
    ServiceType,
    Telemetry,
    VehicleStatus,
)
from app.services.maintenance import (
    check_battery_check_due,
    check_brakes,
    check_device_offline,
    check_service_due,
    run_maintenance_checks,
)
from app.utils import utcnow


def _open(vehicle, alert_type):
    return db.session.execute(
        db.select(Alert).filter_by(vehicle_id=vehicle.id, alert_type=alert_type, resolved_at=None)
    ).scalar_one_or_none()


def _service(vehicle, km, kind=ServiceType.ROUTINE, when=None):
    db.session.add(
        ServiceRecord(
            vehicle_id=vehicle.id,
            service_type=kind,
            service_date=when or date.today(),
            odometer_km=km,
        )
    )
    db.session.flush()


# --- service due ------------------------------------------------------------------------------


def test_service_not_due_for_new_vehicle(app, make_vehicle):
    vehicle, _ = make_vehicle(odometer_km=3_000)
    check_service_due(vehicle)
    assert _open(vehicle, AlertType.SERVICE_DUE) is None


def test_service_due_soon_then_overdue(app, make_vehicle):
    vehicle, _ = make_vehicle(odometer_km=9_600)
    check_service_due(vehicle)
    alert = _open(vehicle, AlertType.SERVICE_DUE)
    assert (
        alert.severity == AlertSeverity.WARNING and "9,600 km since registration" in alert.message
    )

    vehicle.odometer_km = 11_200
    check_service_due(vehicle)
    assert alert.severity == AlertSeverity.CRITICAL and "overdue" in alert.message


def test_service_interval_counts_from_last_routine_service(app, make_vehicle):
    vehicle, _ = make_vehicle(odometer_km=25_000)
    _service(vehicle, 20_000)
    _service(vehicle, 24_900, kind=ServiceType.TYRES)  # not a routine service
    check_service_due(vehicle)
    assert _open(vehicle, AlertType.SERVICE_DUE) is None  # 5,000 km since routine service


def test_service_due_by_time(app, make_vehicle):
    vehicle, _ = make_vehicle(odometer_km=2_000)
    _service(vehicle, 1_000, when=date.today() - timedelta(days=400))
    check_service_due(vehicle)
    assert "400 days since the last service" in _open(vehicle, AlertType.SERVICE_DUE).message


def test_adding_service_record_in_ui_resolves_alert(client, manager, make_vehicle):
    vehicle, _ = make_vehicle(odometer_km=11_500)
    check_service_due(vehicle)
    db.session.commit()
    assert _open(vehicle, AlertType.SERVICE_DUE) is not None
    client.post(
        f"/vehicles/{vehicle.id}/services",
        data={
            "service_date": date.today().isoformat(),
            "service_type": "routine",
            "odometer_km": "11500",
        },
    )
    assert _open(vehicle, AlertType.SERVICE_DUE) is None


# --- brakes / battery check / device ----------------------------------------------------------


def test_brake_inspection_threshold_and_hysteresis(app, make_vehicle):
    vehicle, _ = make_vehicle()
    check_brakes(vehicle, 19)
    assert _open(vehicle, AlertType.BRAKE_INSPECTION) is None
    check_brakes(vehicle, 25)
    assert "25 harsh-braking events" in _open(vehicle, AlertType.BRAKE_INSPECTION).message
    check_brakes(vehicle, 15)  # below threshold but above half: stays open
    assert _open(vehicle, AlertType.BRAKE_INSPECTION) is not None
    check_brakes(vehicle, 5)
    assert _open(vehicle, AlertType.BRAKE_INSPECTION) is None


def test_brake_service_clears_inspection(app, make_vehicle):
    vehicle, _ = make_vehicle()
    check_brakes(vehicle, 30)
    _service(vehicle, 1_000, kind=ServiceType.BRAKES)
    check_brakes(vehicle, 30)
    assert _open(vehicle, AlertType.BRAKE_INSPECTION) is None


def test_battery_check_due(app, make_vehicle):
    vehicle, _ = make_vehicle()
    vehicle.created_at = utcnow() - timedelta(days=200)
    check_battery_check_due(vehicle)
    assert "no battery check" in _open(vehicle, AlertType.BATTERY_CHECK_DUE).message

    db.session.add(
        BatteryCheck(
            vehicle_id=vehicle.id,
            cycle_count=100,
            ambient_temperature_c=24,
            discharge_current_a=2,
            avg_voltage_v=3.5,
            max_temperature_c=40,
            internal_resistance_mohm=150,
            predicted_soh_pct=85,
            status="Good",
            model_name="test",
        )
    )
    db.session.flush()
    check_battery_check_due(vehicle)
    assert _open(vehicle, AlertType.BATTERY_CHECK_DUE) is None


def test_device_offline_only_for_active_vehicles(app, make_vehicle):
    vehicle, _ = make_vehicle(last_seen_at=utcnow() - timedelta(hours=30))
    check_device_offline(vehicle)
    assert "No data for 30 hours" in _open(vehicle, AlertType.DEVICE_OFFLINE).message

    vehicle.status = VehicleStatus.IN_SERVICE  # e.g. at the workshop: silence is expected
    check_device_offline(vehicle)
    assert _open(vehicle, AlertType.DEVICE_OFFLINE) is None

    new, _ = make_vehicle(plate="DL01AB0002")  # never reported yet
    check_device_offline(new)
    assert _open(new, AlertType.DEVICE_OFFLINE) is None


def test_run_all_checks_counts_harsh_brakes_from_telemetry(app, make_vehicle):
    vehicle, _ = make_vehicle(odometer_km=100)
    now = utcnow()
    for i in range(22):
        db.session.add(
            Telemetry(
                vehicle_id=vehicle.id,
                recorded_at=now - timedelta(hours=i),
                lat=1,
                lon=1,
                speed_kmh=30,
                soc_pct=60,
                acceleration_mps2=-5.0,
            )
        )
    inactive, _ = make_vehicle(plate="DL01AB0009", status=VehicleStatus.INACTIVE)
    db.session.commit()
    assert run_maintenance_checks() == 1  # inactive vehicles are skipped
    assert _open(vehicle, AlertType.BRAKE_INSPECTION) is not None


def test_cli_check_maintenance(app, make_vehicle):
    make_vehicle(odometer_km=12_000)
    result = app.test_cli_runner().invoke(args=["check-maintenance"])
    assert "Checked 1 vehicles" in result.output
    assert db.session.query(Alert).filter_by(alert_type=AlertType.SERVICE_DUE).count() == 1
