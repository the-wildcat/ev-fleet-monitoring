import pytest

from app.extensions import db
from app.models import Alert, AlertType, BatteryCheck
from app.services.battery_model import BatteryModel, status_for, validate_features

URL = "/api/v1/predict/battery"
# Real NASA cells (data/battery_cycles_nasa.csv) with their measured state of health.
FRESH = {  # B0006, cycle 20: measured 98.98%
    "cycle_count": 20,
    "ambient_temperature_c": 24,
    "discharge_current_a": 2.01,
    "avg_voltage_v": 3.5595,
    "max_temperature_c": 37.54,
    "internal_resistance_mohm": 139.78,
}
WORN = {  # B0006, cycle 160: measured 59.53%
    "cycle_count": 160,
    "ambient_temperature_c": 24,
    "discharge_current_a": 2.011,
    "avg_voltage_v": 3.3542,
    "max_temperature_c": 41.24,
    "internal_resistance_mohm": 182.32,
}
MIDLIFE = {  # B0007, cycle 100: measured 78.52%
    "cycle_count": 100,
    "ambient_temperature_c": 24,
    "discharge_current_a": 1.99,
    "avg_voltage_v": 3.5164,
    "max_temperature_c": 41.02,
    "internal_resistance_mohm": 131.55,
}


@pytest.mark.parametrize(
    ("soh", "status"),
    [(100, "Good"), (80, "Good"), (79.9, "Fair"), (70, "Fair"), (69.9, "Needs Replacement")],
)
def test_status_bands(soh, status):
    assert status_for(soh) == status


def test_validate_features():
    clean, errors = validate_features(FRESH)
    assert not errors and clean["cycle_count"] == 20.0
    _, errors = validate_features({**FRESH, "avg_voltage_v": 9, "cycle_count": "many"})
    assert set(errors) == {"avg_voltage_v", "cycle_count"}
    _, errors = validate_features({})
    assert len(errors) == 6


def test_api_predicts_real_cells(client):
    resp = client.post(URL, json={"inputs": [FRESH, MIDLIFE, WORN]})
    assert resp.status_code == 200
    data = resp.get_json()
    fresh, mid, worn = data["predictions"]
    # Close to the measured values (these rows were in the training data, so tight bounds).
    assert fresh["status"] == "Good" and abs(fresh["soh_pct"] - 98.98) < 5
    assert mid["status"] == "Fair" and abs(mid["soh_pct"] - 78.52) < 5
    assert worn["status"] == "Needs Replacement" and abs(worn["soh_pct"] - 59.53) < 5
    assert data["model"]["name"] == "random_forest" and data["model"]["cv_mae"] > 0


def test_model_metadata_documents_real_data(app):
    meta = app.extensions["battery_model"].metadata
    assert "NASA" in meta["dataset_source"]
    assert meta["training_batteries"] >= 30
    assert "GroupKFold" in meta["cv"]  # evaluated on unseen batteries
    best = meta["metrics"][meta["model"]]
    assert best["mae"] < meta["metrics"]["baseline_mean"]["mae"]


def test_api_single_input(client):
    resp = client.post(URL, json=FRESH)
    assert len(resp.get_json()["predictions"]) == 1


@pytest.mark.parametrize("body", [{"cycle_count": 10}, {"inputs": []}, {"inputs": "x"}, [FRESH]])
def test_api_rejects_bad_input(client, body):
    resp = client.post(URL, json=body)
    assert resp.status_code == 400 and resp.is_json


def test_api_503_when_model_missing(app, client, tmp_path):
    app.extensions["battery_model"] = BatteryModel(tmp_path / "missing.joblib")
    resp = client.post(URL, json=FRESH)
    assert resp.status_code == 503
    assert "train_battery_model" in resp.get_json()["message"]


def test_battery_page_prediction_only(client, make_user, login):
    make_user()
    login()
    resp = client.post("/battery/", data={**FRESH, "vehicle_id": "0"})
    assert resp.status_code == 200 and b"Predicted health" in resp.data
    assert db.session.query(BatteryCheck).count() == 0


def test_manager_saves_check_and_wear_alert_is_raised(client, manager, make_vehicle):
    vehicle, _ = make_vehicle()
    resp = client.post("/battery/", data={**WORN, "vehicle_id": str(vehicle.id)})
    assert resp.status_code == 302
    check = db.session.execute(db.select(BatteryCheck)).scalar_one()
    assert check.vehicle_id == vehicle.id and check.status == "Needs Replacement"
    assert check.avg_voltage_v == 3.3542

    page = client.get("/battery/").data
    assert b"Battery wear" in page and b"Needs Replacement" in page
    detail = client.get(f"/vehicles/{vehicle.id}").data
    assert b"Battery health history" in detail and b"Battery wear" in detail

    alert = db.session.execute(db.select(Alert)).scalar_one()
    assert alert.alert_type == AlertType.BATTERY_DEGRADED and alert.severity.value == "critical"


def test_driver_cannot_save_checks(client, make_user, login, make_vehicle):
    driver = make_user()
    vehicle, _ = make_vehicle(driver=driver)
    login()
    resp = client.post("/battery/", data={**FRESH, "vehicle_id": str(vehicle.id)})
    # The vehicle option isn't offered to drivers, so the form rejects it; nothing is saved.
    assert resp.status_code == 200
    assert db.session.query(BatteryCheck).count() == 0


def test_battery_page_validates_ranges(client, make_user, login):
    make_user()
    login()
    resp = client.post("/battery/", data={**FRESH, "avg_voltage_v": "12", "vehicle_id": "0"})
    assert b"Predicted health" not in resp.data
    assert b"between 2.0 and 4.5" in resp.data
