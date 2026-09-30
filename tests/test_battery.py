import pytest

from app.extensions import db
from app.models import AlertType, BatteryCheck
from app.services.battery_model import BatteryModel, status_for, validate_features

URL = "/api/v1/predict/battery"
# From the dataset (BATT-002): health 86.31%, status Good.
GOOD_BATTERY = {
    "capacity_mah": 3901.43,
    "cycle_count": 842,
    "voltage_v": 3.86,
    "temperature_c": 20.7,
    "internal_resistance_mohm": 170.5,
}
# BATT-001: health 55.89%, status Fair.
FAIR_BATTERY = {
    "capacity_mah": 2749.08,
    "cycle_count": 963,
    "voltage_v": 3.46,
    "temperature_c": 31.6,
    "internal_resistance_mohm": 160.3,
}


@pytest.mark.parametrize(
    ("soh", "status"),
    [(100, "Good"), (75, "Good"), (74.99, "Fair"), (50, "Fair"), (49.9, "Needs Replacement")],
)
def test_status_bands(soh, status):
    assert status_for(soh) == status


def test_validate_features():
    clean, errors = validate_features(GOOD_BATTERY)
    assert not errors and clean["cycle_count"] == 842.0
    _, errors = validate_features({**GOOD_BATTERY, "voltage_v": 9, "cycle_count": "many"})
    assert set(errors) == {"voltage_v", "cycle_count"}
    _, errors = validate_features({})
    assert len(errors) == 5


def test_api_predicts_known_rows(client):
    resp = client.post(URL, json={"inputs": [GOOD_BATTERY, FAIR_BATTERY]})
    assert resp.status_code == 200
    data = resp.get_json()
    good, fair = data["predictions"]
    assert good["status"] == "Good" and abs(good["soh_pct"] - 86.31) < 1
    assert fair["status"] == "Fair" and abs(fair["soh_pct"] - 55.89) < 1
    assert data["model"]["name"] and data["model"]["trained_at"]


def test_api_single_input(client):
    resp = client.post(URL, json=GOOD_BATTERY)
    assert len(resp.get_json()["predictions"]) == 1


@pytest.mark.parametrize(
    "body", [{"capacity_mah": 3000}, {"inputs": []}, {"inputs": "x"}, [GOOD_BATTERY]]
)
def test_api_rejects_bad_input(client, body):
    resp = client.post(URL, json=body)
    assert resp.status_code == 400 and resp.is_json


def test_api_503_when_model_missing(app, client, tmp_path):
    app.extensions["battery_model"] = BatteryModel(tmp_path / "missing.joblib")
    resp = client.post(URL, json=GOOD_BATTERY)
    assert resp.status_code == 503
    assert "train_battery_model" in resp.get_json()["message"]


def test_battery_page_prediction_only(client, make_user, login):
    make_user()
    login()
    resp = client.post("/battery/", data={**GOOD_BATTERY, "vehicle_id": "0"})
    assert resp.status_code == 200 and b"Predicted health" in resp.data
    assert db.session.query(BatteryCheck).count() == 0


def test_manager_saves_check_and_wear_alert_is_raised(client, manager, make_vehicle):
    vehicle, _ = make_vehicle()
    resp = client.post("/battery/", data={**FAIR_BATTERY, "vehicle_id": str(vehicle.id)})
    assert resp.status_code == 302
    check = db.session.execute(db.select(BatteryCheck)).scalar_one()
    assert check.vehicle_id == vehicle.id and check.status == "Fair"

    page = client.get("/battery/").data
    assert b"Battery wear" in page and b"Fair" in page
    detail = client.get(f"/vehicles/{vehicle.id}").data
    assert b"Battery health history" in detail and b"Battery wear" in detail

    from app.models import Alert

    alert = db.session.execute(db.select(Alert)).scalar_one()
    assert alert.alert_type == AlertType.BATTERY_DEGRADED


def test_driver_cannot_save_checks(client, make_user, login, make_vehicle):
    driver = make_user()
    vehicle, _ = make_vehicle(driver=driver)
    login()
    resp = client.post("/battery/", data={**GOOD_BATTERY, "vehicle_id": str(vehicle.id)})
    # The vehicle option isn't offered to drivers, so the form rejects it; nothing is saved.
    assert resp.status_code == 200
    assert db.session.query(BatteryCheck).count() == 0


def test_battery_page_validates_ranges(client, make_user, login):
    make_user()
    login()
    resp = client.post("/battery/", data={**GOOD_BATTERY, "voltage_v": "12", "vehicle_id": "0"})
    assert b"Predicted health" not in resp.data
    assert b"between 2.0 and 5.0" in resp.data
