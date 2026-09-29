from datetime import date, timedelta

import pytest

from app.extensions import db
from app.models import ServiceRecord, Telemetry, Vehicle, VehicleStatus
from app.utils import utcnow


def _vehicle_form(**overrides):
    data = {
        "make": "Tata",
        "model": "Nexon EV",
        "year": "2024",
        "plate_number": "wb 12 ad 3456",
        "vin": "",
        "battery_capacity_kwh": "40.5",
        "efficiency_km_per_kwh": "7.0",
        "status": "active",
        "driver_id": "0",
        "simulated": "y",
    }
    data.update(overrides)
    return data


def _vehicle(plate):
    return db.session.execute(db.select(Vehicle).filter_by(plate_number=plate)).scalar_one_or_none()


# --- registration -----------------------------------------------------------------------------


def test_manager_registers_vehicle_and_sees_key_once(client, manager):
    resp = client.post("/vehicles/new", data=_vehicle_form())
    assert resp.status_code == 200
    assert b"Copy this key now" in resp.data

    vehicle = _vehicle("WB12AD3456")  # plate normalised (spaces removed, uppercase)
    assert vehicle is not None and vehicle.battery_capacity_kwh == 40.5
    key = resp.data.split(b'id="api-key" class="form-control font-monospace" value="')[1]
    key = key.split(b'"')[0].decode()
    assert key.startswith("evk_")
    assert vehicle.api_key_hash == Vehicle.hash_api_key(key)  # only the hash is stored
    assert key not in (vehicle.api_key_hash or "")


@pytest.mark.parametrize(
    "plate",
    ["DL3CAB1234", "MH01AB1234", "KA05M1234", "22BH1234AA", "ts-09-ea-1234"],
)
def test_valid_indian_plates_accepted(client, manager, plate):
    resp = client.post("/vehicles/new", data=_vehicle_form(plate_number=plate))
    assert b"Copy this key now" in resp.data


@pytest.mark.parametrize("plate", ["ABC", "1234WB12", "WB12AD34567", "XX"])
def test_invalid_plates_rejected(client, manager, plate):
    resp = client.post("/vehicles/new", data=_vehicle_form(plate_number=plate))
    assert b"valid Indian registration number" in resp.data
    assert db.session.query(Vehicle).count() == 0


def test_duplicate_plate_rejected(client, manager, make_vehicle):
    make_vehicle(plate="WB12AD3456")
    resp = client.post("/vehicles/new", data=_vehicle_form())
    assert b"already exists" in resp.data
    assert db.session.query(Vehicle).count() == 1


def test_invalid_vin_rejected(client, manager):
    resp = client.post("/vehicles/new", data=_vehicle_form(vin="MA1OI2345678901QZ"))
    assert b"never uses I, O or Q" in resp.data


def test_battery_capacity_range_validated(client, manager):
    resp = client.post("/vehicles/new", data=_vehicle_form(battery_capacity_kwh="1"))
    assert b"between 5" in resp.data


def test_driver_cannot_register_vehicle(client, make_user, login):
    make_user()
    login()
    assert client.get("/vehicles/new").status_code == 403
    assert client.post("/vehicles/new", data=_vehicle_form()).status_code == 403


def test_assign_driver_on_registration(client, manager, make_user):
    driver = make_user(email="ravi@example.com", name="Ravi")
    client.post("/vehicles/new", data=_vehicle_form(driver_id=str(driver.id)))
    assert _vehicle("WB12AD3456").driver_id == driver.id


# --- visibility -------------------------------------------------------------------------------


def test_driver_sees_only_assigned_vehicles(client, make_user, login, make_vehicle):
    driver = make_user()
    other = make_user(email="other@example.com")
    mine, _ = make_vehicle(plate="WB12AD0001", driver=driver)
    theirs, _ = make_vehicle(plate="WB12AD0002", driver=other)
    login()

    listing = client.get("/vehicles/").data
    assert b"WB12AD0001" in listing and b"WB12AD0002" not in listing
    assert client.get(f"/vehicles/{mine.id}").status_code == 200
    assert client.get(f"/vehicles/{theirs.id}").status_code == 404  # not 403: no ID probing
    assert client.get(f"/vehicles/{theirs.id}/telemetry.json").status_code == 404


def test_manager_sees_all_vehicles(client, manager, make_vehicle):
    make_vehicle(plate="WB12AD0001")
    make_vehicle(plate="WB12AD0002")
    listing = client.get("/vehicles/").data
    assert b"WB12AD0001" in listing and b"WB12AD0002" in listing


def test_vehicle_pages_require_login(client, make_vehicle):
    vehicle, _ = make_vehicle()
    for url in ("/vehicles/", f"/vehicles/{vehicle.id}", "/monitoring/live"):
        assert client.get(url).status_code == 302


# --- edit / delete ----------------------------------------------------------------------------


def test_manager_edits_vehicle(client, manager, make_vehicle):
    vehicle, _ = make_vehicle()
    assert client.get(f"/vehicles/{vehicle.id}/edit").status_code == 200
    resp = client.post(
        f"/vehicles/{vehicle.id}/edit",
        data=_vehicle_form(model="Nexon EV Max", status="in_service", battery_capacity_kwh="40.5"),
    )
    assert resp.status_code == 302
    assert vehicle.model == "Nexon EV Max" and vehicle.status == VehicleStatus.IN_SERVICE


def test_edit_keeps_own_plate_without_duplicate_error(client, manager, make_vehicle):
    vehicle, _ = make_vehicle(plate="WB12AD3456")
    resp = client.post(f"/vehicles/{vehicle.id}/edit", data=_vehicle_form())
    assert resp.status_code == 302


def test_delete_requires_typed_plate_and_cascades(client, manager, make_vehicle):
    vehicle, _ = make_vehicle()
    db.session.add(
        Telemetry(
            vehicle_id=vehicle.id, recorded_at=utcnow(), lat=1, lon=1, speed_kmh=0, soc_pct=50
        )
    )
    db.session.add(
        ServiceRecord(vehicle_id=vehicle.id, service_date=date.today(), service_type="routine")
    )
    db.session.commit()

    client.post(f"/vehicles/{vehicle.id}/delete", data={"confirm_plate": "WRONG"})
    assert db.session.get(Vehicle, vehicle.id) is not None

    client.post(f"/vehicles/{vehicle.id}/delete", data={"confirm_plate": "wb12ad3456"})
    assert db.session.get(Vehicle, vehicle.id) is None
    assert db.session.query(Telemetry).count() == 0  # cascaded via foreign key
    assert db.session.query(ServiceRecord).count() == 0


# --- service history --------------------------------------------------------------------------


def test_add_and_delete_service_record(client, manager, make_vehicle):
    vehicle, _ = make_vehicle()
    resp = client.post(
        f"/vehicles/{vehicle.id}/services",
        data={
            "service_date": date.today().isoformat(),
            "service_type": "battery",
            "odometer_km": "15000",
            "cost_inr": "4500",
            "description": "Battery health check",
        },
    )
    assert resp.status_code == 302
    record = vehicle.service_records[0]
    assert record.cost_inr == 4500 and record.description == "Battery health check"
    assert b"Battery health check" in client.get(f"/vehicles/{vehicle.id}").data

    client.post(f"/vehicles/{vehicle.id}/services/{record.id}/delete")
    assert db.session.query(ServiceRecord).count() == 0


def test_service_date_cannot_be_in_future(client, manager, make_vehicle):
    vehicle, _ = make_vehicle()
    tomorrow = (date.today() + timedelta(days=1)).isoformat()
    resp = client.post(
        f"/vehicles/{vehicle.id}/services",
        data={"service_date": tomorrow, "service_type": "routine"},
    )
    assert resp.status_code == 400
    assert b"can&#39;t be in the future" in resp.data


# --- API key rotation -------------------------------------------------------------------------


def test_rotating_api_key_invalidates_old_key(client, manager, make_vehicle):
    vehicle, old_key = make_vehicle()
    resp = client.post(f"/vehicles/{vehicle.id}/api-key")
    assert b"previous key no longer works" in resp.data
    assert vehicle.api_key_hash != Vehicle.hash_api_key(old_key)


# --- telemetry history for charts -------------------------------------------------------------


def test_vehicle_telemetry_json_returns_recent_readings_in_order(client, manager, make_vehicle):
    vehicle, _ = make_vehicle()
    now = utcnow()
    for minutes_ago in (90, 30, 10):
        db.session.add(
            Telemetry(
                vehicle_id=vehicle.id,
                recorded_at=now - timedelta(minutes=minutes_ago),
                lat=1,
                lon=1,
                speed_kmh=minutes_ago,
                soc_pct=50,
            )
        )
    db.session.commit()
    readings = client.get(f"/vehicles/{vehicle.id}/telemetry.json").get_json()["readings"]
    assert [r["speed_kmh"] for r in readings] == [30, 10]  # 90-min-old reading excluded
