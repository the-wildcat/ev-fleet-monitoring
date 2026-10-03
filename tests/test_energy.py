from datetime import UTC, date, datetime, time, timedelta
from zoneinfo import ZoneInfo

import pytest

from app.extensions import db
from app.models import DailyEnergy, ServiceRecord, ServiceType, Telemetry
from app.services.energy import analyse, local_today, rollup
from app.services.settings import get_setting, reset_setting, set_setting

IST = ZoneInfo("Asia/Kolkata")


def at(day: date, hh: int, mm: int = 0) -> datetime:
    """Local IST wall-clock time -> the naive UTC the database stores."""
    return datetime.combine(day, time(hh, mm), IST).astimezone(UTC).replace(tzinfo=None)


def reading(vehicle, when, soc, odo, charging=False):
    db.session.add(
        Telemetry(
            vehicle_id=vehicle.id,
            recorded_at=when,
            lat=28.6,
            lon=77.2,
            speed_kmh=0 if charging else 40,
            soc_pct=soc,
            is_charging=charging,
            odometer_km=odo,
        )
    )


def _day(vehicle, day) -> DailyEnergy:
    return db.session.execute(
        db.select(DailyEnergy).filter_by(vehicle_id=vehicle.id, day=day)
    ).scalar_one()


@pytest.fixture
def yesterday(app):
    return local_today() - timedelta(days=1)


def test_rollup_energy_charging_distance_and_cost(app, make_vehicle, yesterday):
    vehicle, _ = make_vehicle()  # 40.5 kWh
    # Drive 70 km using 20% (8.1 kWh), then charge +40% (16.2 kWh into the battery).
    reading(vehicle, at(yesterday, 9), 80, 1000)
    reading(vehicle, at(yesterday, 10), 70, 1035)
    reading(vehicle, at(yesterday, 11), 60, 1070)
    reading(vehicle, at(yesterday, 12), 60, 1070, charging=True)
    reading(vehicle, at(yesterday, 13), 100, 1070, charging=True)
    db.session.commit()

    assert rollup(yesterday, yesterday) == 1
    row = _day(vehicle, yesterday)
    assert row.distance_km == pytest.approx(70)
    assert row.energy_used_kwh == pytest.approx(8.1)
    assert row.battery_charged_kwh == pytest.approx(16.2)
    assert row.grid_energy_kwh == pytest.approx(18.0)  # 16.2 / 90% charging efficiency
    assert row.charging_cost_inr == pytest.approx(180.0)  # 18 kWh bought x ₹10
    assert row.energy_cost_inr == pytest.approx(90.0)  # 8.1 kWh used / 90% x ₹10
    assert row.readings == 5


def test_days_split_at_local_midnight(app, make_vehicle, yesterday):
    vehicle, _ = make_vehicle()
    today = yesterday + timedelta(days=1)
    reading(vehicle, at(yesterday, 23, 30), 80, 100)
    reading(vehicle, at(yesterday, 23, 59), 78, 120)
    reading(vehicle, at(today, 0, 30), 75, 150)  # 00:30 IST is still "yesterday" in UTC
    db.session.commit()
    rollup(yesterday, today)
    assert _day(vehicle, yesterday).distance_km == pytest.approx(20)
    # The step across midnight belongs to the new day, nothing is lost.
    assert _day(vehicle, today).distance_km == pytest.approx(30)


def test_rollup_is_idempotent_and_ignores_odometer_glitches(app, make_vehicle, yesterday):
    vehicle, _ = make_vehicle()
    reading(vehicle, at(yesterday, 9), 80, 1000)
    reading(vehicle, at(yesterday, 10), 79, 1010)
    reading(vehicle, at(yesterday, 11), 78, 99999)  # glitch: +98,989 km
    reading(vehicle, at(yesterday, 12), 77, 100009)
    db.session.commit()
    rollup(yesterday, yesterday)
    rollup(yesterday, yesterday)
    assert db.session.query(DailyEnergy).count() == 1
    assert _day(vehicle, yesterday).distance_km == pytest.approx(20)


def test_tariff_is_fixed_per_day(app, make_vehicle, yesterday):
    vehicle, _ = make_vehicle()
    reading(vehicle, at(yesterday, 9), 50, 0, charging=True)
    reading(vehicle, at(yesterday, 10), 60, 0, charging=True)
    db.session.commit()
    rollup(yesterday, yesterday)
    set_setting("energy_tariff_inr_per_kwh", 20, None)
    rollup(yesterday, yesterday)  # recomputed, but keeps the tariff of that day
    row = _day(vehicle, yesterday)
    assert row.tariff_inr_per_kwh == 10 and row.charging_cost_inr == pytest.approx(45.0)


def test_analysis_totals_savings_and_co2(app, make_vehicle, yesterday):
    vehicle, _ = make_vehicle()
    db.session.add(
        DailyEnergy(
            vehicle_id=vehicle.id,
            day=yesterday,
            distance_km=150,
            energy_used_kwh=21.0,
            battery_charged_kwh=18,
            grid_energy_kwh=20.0,
            energy_cost_inr=200.0,
            charging_cost_inr=200.0,
            tariff_inr_per_kwh=10,
        )
    )
    db.session.add(
        ServiceRecord(
            vehicle_id=vehicle.id,
            service_type=ServiceType.ROUTINE,
            service_date=yesterday,
            cost_inr=1300,
        )
    )
    db.session.commit()

    r = analyse([vehicle], yesterday - timedelta(days=6), yesterday)
    assert len(r.daily) == 7 and r.daily[-1]["energy_cost"] == 200
    assert r.total_cost == 1500 and r.cost_per_km == pytest.approx(10.0)
    assert r.energy_cost_per_km == pytest.approx(200 / 150)
    assert r.kwh_per_100km == pytest.approx(14.0)
    # Petrol: 150 km / 15 km/L = 10 L x ₹105 = ₹1,050 -> saved ₹850
    assert r.petrol_cost == pytest.approx(1050) and r.savings_vs_petrol == pytest.approx(850)
    # CO2: petrol 10 L x 2.31 = 23.1 kg; EV 21 kWh used / 90% x 0.72 = 16.8 kg -> 6.3 kg avoided
    assert r.co2_avoided_kg == pytest.approx(6.3)
    assert r.charging_cost == 200
    (v,) = r.vehicles
    assert v.vs_rated_pct == pytest.approx((14.0 - 100 / 7.0) / (100 / 7.0) * 100)


def test_analysis_handles_no_data(app, make_vehicle):
    vehicle, _ = make_vehicle()
    r = analyse([vehicle], local_today() - timedelta(days=6), local_today())
    assert r.total_cost == 0 and r.cost_per_km is None and r.kwh_per_100km is None
    assert analyse([], local_today(), local_today()).vehicles == []


# --- page -------------------------------------------------------------------------------------


def test_energy_page_for_manager_and_filter(client, manager, make_vehicle, yesterday):
    v1, _ = make_vehicle(plate="DL01AB0001")
    v2, _ = make_vehicle(plate="DL01AB0002")
    for v, cost in ((v1, 100.0), (v2, 300.0)):
        db.session.add(
            DailyEnergy(
                vehicle_id=v.id,
                day=yesterday,
                distance_km=50,
                energy_used_kwh=7,
                grid_energy_kwh=8,
                energy_cost_inr=cost,
                tariff_inr_per_kwh=10,
            )
        )
    db.session.commit()
    page = client.get("/analytics/?days=7").data
    assert b"DL01AB0001" in page and b"DL01AB0002" in page and "₹400".encode() in page
    page = client.get(f"/analytics/?days=7&vehicle_id={v1.id}").data
    assert "₹100".encode() in page and "₹400".encode() not in page


def test_energy_page_driver_sees_own_vehicles_only(client, make_user, login, make_vehicle):
    driver = make_user()
    make_vehicle(plate="DL01AB0001", driver=driver)
    other, _ = make_vehicle(plate="DL01AB0002")
    login()
    page = client.get(f"/analytics/?vehicle_id={other.id}").data
    assert b"DL01AB0001" in page and b"DL01AB0002" not in page  # filter ignored


def test_page_refreshes_today_from_telemetry(client, manager, make_vehicle):
    vehicle, _ = make_vehicle()
    from app.utils import utcnow

    reading(vehicle, utcnow() - timedelta(minutes=2), 60, 500)
    reading(vehicle, utcnow() - timedelta(minutes=1), 59, 512)
    db.session.commit()
    client.get("/analytics/")
    assert _day(vehicle, local_today()).distance_km == pytest.approx(12)


# --- settings ---------------------------------------------------------------------------------


def test_settings_defaults_override_validation_and_reset(app):
    assert get_setting("energy_tariff_inr_per_kwh") == 10
    set_setting("energy_tariff_inr_per_kwh", 8.5, None)
    assert get_setting("energy_tariff_inr_per_kwh") == 8.5
    with pytest.raises(ValueError, match="between"):
        set_setting("charging_efficiency_pct", 150, None)
    reset_setting("energy_tariff_inr_per_kwh")
    assert get_setting("energy_tariff_inr_per_kwh") == 10


def _settings_form(**overrides):
    data = {
        "energy_tariff_inr_per_kwh": "12",
        "charging_efficiency_pct": "92",
        "petrol_price_inr_per_l": "102",
        "ice_km_per_l": "16",
        "speed_limit_kmh": "70",
    }
    data.update(overrides)
    return data


def test_admin_settings_page(client, make_user, login):
    make_user(email="admin@example.com", role="admin")
    login(email="admin@example.com")
    assert client.get("/admin/settings").status_code == 200
    resp = client.post("/admin/settings", data=_settings_form(), follow_redirects=True)
    assert b"Settings saved" in resp.data
    assert get_setting("speed_limit_kmh") == 70 and get_setting("ice_km_per_l") == 16

    resp = client.post("/admin/settings", data=_settings_form(charging_efficiency_pct="400"))
    assert b"between 50 and 100" in resp.data
    assert get_setting("charging_efficiency_pct") == 92  # nothing saved on error

    resp = client.post("/admin/settings", data=_settings_form(ice_km_per_l="abc"))
    assert b"Enter a number" in resp.data

    client.post("/admin/settings", data={"reset": "1"})
    assert get_setting("speed_limit_kmh") == 80


def test_settings_page_admin_only(client, manager):
    assert client.get("/admin/settings").status_code == 403


def test_speed_limit_setting_changes_driver_scoring(app, make_user, make_vehicle):
    from app.services.driving import driver_stats
    from app.utils import utcnow

    driver = make_user()
    vehicle, _ = make_vehicle(driver=driver)
    t = utcnow()
    for i, (speed, odo) in enumerate([(50, 0), (95, 10), (50, 20)]):
        db.session.add(
            Telemetry(
                vehicle_id=vehicle.id,
                driver_id=driver.id,
                recorded_at=t - timedelta(minutes=10 - i),
                lat=1,
                lon=1,
                speed_kmh=speed,
                soc_pct=80,
                odometer_km=odo,
            )
        )
    db.session.commit()
    window = (t - timedelta(hours=1), t + timedelta(minutes=1))
    assert driver_stats(*window)[0].speeding == 1
    set_setting("speed_limit_kmh", 100, None)
    assert driver_stats(*window)[0].speeding == 0


# --- jobs / CLI -------------------------------------------------------------------------------


def test_cli_and_background_rollup(app, make_vehicle):
    from app.services.jobs import run_once
    from app.utils import utcnow

    vehicle, _ = make_vehicle()
    reading(vehicle, utcnow() - timedelta(minutes=5), 70, 0)
    reading(vehicle, utcnow() - timedelta(minutes=4), 69, 5)
    db.session.commit()
    assert "Updated 1 vehicle-days" in app.test_cli_runner().invoke(args=["rollup-energy"]).output
    db.session.query(DailyEnergy).delete()
    db.session.commit()
    run_once(app, run_maintenance=False, run_rollup=True)
    assert db.session.query(DailyEnergy).count() == 1
