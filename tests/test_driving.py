from datetime import timedelta

import pytest

from app.extensions import db
from app.models import Telemetry
from app.services.driving import (
    DriverStats,
    compute_score,
    daily_trend,
    driver_stats,
    recent_events,
    score_band,
)
from app.services.telemetry import parse_reading, record_reading
from app.utils import utcnow


def drive(vehicle, driver, km=100.0, minutes=100, soc_start=90.0, soc_used=None, events=()):
    """Insert one reading per minute covering `km`, ending now.

    events: list of (minute_index, field, value) to inject, e.g. (10, "acceleration_mps2", -5).
    soc_used defaults to exactly the vehicle's rated consumption for the distance.
    """
    if soc_used is None:
        soc_used = km / vehicle.efficiency_km_per_kwh / vehicle.battery_capacity_kwh * 100
    start = utcnow() - timedelta(minutes=minutes)
    overrides = {}
    for i, field, value in events:
        overrides.setdefault(i, {})[field] = value
    for i in range(minutes + 1):
        reading = {
            "vehicle_id": vehicle.id,
            "driver_id": driver.id if driver else None,
            "recorded_at": start + timedelta(minutes=i),
            "lat": 28.6,
            "lon": 77.2,
            "speed_kmh": 60.0,
            "soc_pct": soc_start - soc_used * i / minutes,
            "is_charging": False,
            "odometer_km": 1000 + km * i / minutes,
            "acceleration_mps2": 0.2,
        }
        reading.update(overrides.get(i, {}))
        db.session.add(Telemetry(**reading))
    db.session.commit()


def window():
    return utcnow() - timedelta(days=1), utcnow() + timedelta(minutes=1)


def test_clean_driver_scores_100_with_rated_energy(app, make_user, make_vehicle):
    driver = make_user()
    vehicle, _ = make_vehicle(driver=driver)
    drive(vehicle, driver, km=100)
    (st,) = driver_stats(*window())
    assert st.distance_km == pytest.approx(100)
    assert st.events == 0 and st.score == 100 and st.band == "Good"
    assert st.energy_kwh == pytest.approx(100 / 7.0, rel=0.01)  # 40.5 kWh Nexon at 7 km/kWh
    assert st.energy_vs_rated_pct == pytest.approx(0, abs=1)


def test_events_are_counted_per_100km_and_weighted(app, make_user, make_vehicle):
    driver = make_user()
    vehicle, _ = make_vehicle(driver=driver)
    events = [
        (10, "acceleration_mps2", -4.0),  # harsh brake
        (20, "acceleration_mps2", -6.0),  # harsh brake
        (30, "acceleration_mps2", 3.5),  # harsh accel
        (40, "speed_kmh", 95.0),  # speeding
        (50, "acceleration_mps2", -3.0),  # not harsh (threshold -3.5)
        (60, "speed_kmh", 79.0),  # under the limit
    ]
    drive(vehicle, driver, km=200, minutes=200, events=events)
    (st,) = driver_stats(*window())
    assert (st.harsh_brake, st.harsh_accel, st.speeding) == (2, 1, 1)
    # per 100 km: 1 brake, 0.5 accel, 0.5 speeding -> 100 - (4 + 1.5 + 1)
    assert st.score == 93.5


def test_speeding_ignored_while_charging(app, make_user, make_vehicle):
    driver = make_user()
    vehicle, _ = make_vehicle(driver=driver)
    drive(vehicle, driver, events=[(5, "speed_kmh", 120.0), (5, "is_charging", True)])
    (st,) = driver_stats(*window())
    assert st.speeding == 0


def test_aggressive_driving_shows_extra_energy_and_cost(app, make_user, make_vehicle):
    calm, wild = make_user(email="calm@example.com"), make_user(email="wild@example.com")
    v1, _ = make_vehicle(plate="DL01AB0001", driver=calm)
    v2, _ = make_vehicle(plate="DL01AB0002", driver=wild)
    rated_soc = 100 / 7.0 / 40.5 * 100
    drive(v1, calm, km=100, soc_used=rated_soc * 0.9)
    drive(v2, wild, km=100, soc_used=rated_soc * 1.2)
    by_email = {s.email: s for s in driver_stats(*window())}
    assert by_email["calm@example.com"].energy_vs_rated_pct == pytest.approx(-10, abs=1)
    assert by_email["wild@example.com"].energy_vs_rated_pct == pytest.approx(20, abs=1)
    # 20% of 14.3 kWh/100 km = 2.86 kWh extra x ₹10
    assert by_email["wild@example.com"].extra_cost_per_100km_inr == pytest.approx(28.6, abs=0.5)


def test_short_distance_is_not_scored(app, make_user, make_vehicle):
    driver = make_user()
    vehicle, _ = make_vehicle(driver=driver)
    drive(vehicle, driver, km=2, minutes=5)
    (st,) = driver_stats(*window())
    assert st.score is None and st.band == "Not enough driving"


def test_readings_are_credited_to_the_driver_assigned_at_the_time(app, make_user, make_vehicle):
    first, second = make_user(email="a@example.com"), make_user(email="b@example.com")
    vehicle, _ = make_vehicle(driver=first)
    base = {"lat": 28.6, "lon": 77.2, "speed_kmh": 40, "soc_pct": 80}
    record_reading(vehicle, parse_reading({**base, "odometer_km": 10})[0])
    vehicle.driver_id = second.id  # reassigned
    record_reading(vehicle, parse_reading({**base, "odometer_km": 20})[0])
    db.session.commit()
    owners = [r.driver_id for r in db.session.query(Telemetry).order_by(Telemetry.id)]
    assert owners == [first.id, second.id]


def test_unassigned_readings_are_not_scored(app, make_vehicle):
    vehicle, _ = make_vehicle()
    drive(vehicle, None, km=100)
    assert driver_stats(*window()) == []


def test_recent_events_and_daily_trend(app, make_user, make_vehicle):
    driver = make_user()
    vehicle, _ = make_vehicle(driver=driver)
    drive(
        vehicle, driver, km=100, events=[(10, "acceleration_mps2", -5.0), (20, "speed_kmh", 99.0)]
    )
    events = recent_events(driver.id, utcnow() - timedelta(days=1))
    assert [e["kind"] for e in events] == ["Speeding", "Harsh braking"]  # newest first
    assert events[1]["detail"] == "-5.0 m/s²" and events[0]["plate"] == vehicle.plate_number
    trend = daily_trend(driver.id, *window())
    assert sum(d["distance_km"] for d in trend) == pytest.approx(100, abs=1)
    assert sum(d["events"] for d in trend) == 2


@pytest.mark.parametrize(
    ("score", "band"),
    [
        (None, "Not enough driving"),
        (90, "Good"),
        (85, "Good"),
        (70, "Fair"),
        (69.9, "Needs coaching"),
    ],
)
def test_score_bands(score, band):
    assert score_band(score) == band


def test_score_floor_is_zero(app):
    st = DriverStats(1, "x", "x", distance_km=10, harsh_brake=50)
    assert compute_score(st) == 0


# --- pages ------------------------------------------------------------------------------------


def test_leaderboard_for_managers(client, manager, make_user, make_vehicle):
    driver = make_user(name="Ravi Kumar")
    vehicle, _ = make_vehicle(driver=driver)
    make_user(email="idle@example.com", name="Idle Ida")
    drive(vehicle, driver, km=100, events=[(10, "acceleration_mps2", -5.0)])
    page = client.get("/drivers/?days=7").data
    assert b"Ravi Kumar" in page and b"96" in page  # 100 - 4 x 1 brake per 100 km
    assert b"Idle Ida" in page  # listed as not driving


def test_driver_sees_only_own_scorecard(client, make_user, login, make_vehicle):
    me = make_user()
    other = make_user(email="other@example.com")
    vehicle, _ = make_vehicle(driver=me)
    drive(vehicle, me, km=50)
    login()
    resp = client.get("/drivers/")
    assert resp.status_code == 302 and f"/drivers/{me.id}" in resp.headers["Location"]
    assert b"Your driving" in client.get(f"/drivers/{me.id}").data
    assert client.get(f"/drivers/{other.id}").status_code == 404


def test_scorecard_renders_for_all_periods(client, manager, make_user, make_vehicle):
    driver = make_user()
    vehicle, _ = make_vehicle(driver=driver)
    drive(vehicle, driver, km=100, events=[(10, "speed_kmh", 99.0)])
    for days in (1, 7, 30, 999):  # unknown periods fall back to 7 days
        resp = client.get(f"/drivers/{driver.id}?days={days}")
        assert resp.status_code == 200 and b"Coaching tip" in resp.data
