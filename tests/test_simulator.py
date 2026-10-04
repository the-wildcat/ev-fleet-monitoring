import random
from datetime import timedelta

import pytest

from app.extensions import db
from app.models import Telemetry, VehicleStatus
from app.services.simulator import (
    CHARGE_TARGET_PCT,
    CITY_LOOPS,
    FleetSimulator,
    VehicleSim,
    aggressiveness_for,
    haversine_km,
    new_sim_for,
)
from app.services.telemetry import parse_reading, prune_old_telemetry
from app.utils import utcnow


def _sim(vehicle, soc=80.0, aggr=1.0, seed=1):
    return VehicleSim(
        points=CITY_LOOPS["Delhi"],
        segment=0,
        soc_pct=soc,
        odometer_km=0.0,
        aggressiveness=aggr,
        rng=random.Random(seed),
    )


def test_haversine_delhi_to_mumbai():
    assert 1140 < haversine_km((28.6139, 77.2090), (19.0760, 72.8777)) < 1160


def test_driving_moves_vehicle_and_drains_battery(make_vehicle):
    vehicle, _ = make_vehicle()
    sim = _sim(vehicle)
    start = sim.position
    readings = [sim.tick(60, vehicle) for _ in range(30)]

    assert sim.odometer_km > 5
    assert readings[-1]["soc_pct"] < 80
    assert haversine_km(start, sim.position) > 0.1
    # Every reading the simulator emits must pass the same validation as device data.
    assert all(not parse_reading(r)[1] for r in readings)


def test_low_battery_triggers_charging_until_target(make_vehicle):
    vehicle, _ = make_vehicle()
    sim = _sim(vehicle, soc=20.5)
    for _ in range(50):
        sim.tick(60, vehicle)
        if sim.charging:
            break
    assert sim.charging

    reading = sim.tick(60, vehicle)
    assert reading["is_charging"] and reading["speed_kmh"] == 0 and reading["power_kw"] < 0
    for _ in range(500):
        if not sim.charging:
            break
        sim.tick(60, vehicle)
    assert not sim.charging and sim.soc_pct >= CHARGE_TARGET_PCT


def test_aggressive_drivers_have_more_harsh_events_and_use_more_energy(make_vehicle):
    vehicle, _ = make_vehicle(battery_capacity_kwh=500)  # big battery: no charging stops

    def run(aggr):
        sim = _sim(vehicle, soc=100, aggr=aggr, seed=42)
        readings = [sim.tick(60, vehicle) for _ in range(400)]
        harsh = sum(abs(r["acceleration_mps2"]) >= 2.8 for r in readings)
        kwh_per_km = (100 - sim.soc_pct) / 100 * 500 / sim.odometer_km
        return harsh, kwh_per_km

    calm_harsh, calm_energy = run(0.6)
    wild_harsh, wild_energy = run(1.8)
    assert wild_harsh > calm_harsh * 1.5
    assert wild_energy > calm_energy


def test_aggressiveness_is_stable_per_vehicle():
    assert aggressiveness_for(3) == aggressiveness_for(3)
    assert 0.6 <= aggressiveness_for(3) <= 1.8


def test_fleet_step_records_only_active_simulated_vehicles(app, make_vehicle):
    active, _ = make_vehicle(plate="DL01AB0001")
    make_vehicle(plate="DL01AB0002", simulated=False)
    make_vehicle(plate="DL01AB0003", status=VehicleStatus.INACTIVE)

    sim = FleetSimulator(app, seed=7)
    assert sim.step(dt_s=60) == 1
    assert sim.step(dt_s=60) == 1
    rows = db.session.query(Telemetry).all()
    assert len(rows) == 2 and {r.vehicle_id for r in rows} == {active.id}
    assert active.last_seen_at is not None and active.last_lat is not None


def test_simulator_resumes_from_last_known_position_and_charge(make_vehicle):
    vehicle, _ = make_vehicle()
    points = new_sim_for(vehicle, random.Random(0)).points  # this vehicle's city road loop
    last = points[len(points) // 2]
    vehicle.last_lat, vehicle.last_lon, vehicle.last_soc_pct = last[0], last[1], 55.0

    state = new_sim_for(vehicle, random.Random(0))
    # Resumes on the road right where it was last seen, not back at the start of the loop.
    assert haversine_km(state.points[state.segment], last) < 0.05
    assert state.segment > 0
    assert state.soc_pct == 55.0


def test_simulator_follows_real_roads(app, make_vehicle):
    from app.services.simulator import loop_for_city

    for city, waypoints in CITY_LOOPS.items():
        road = loop_for_city(city)
        assert len(road) > 10 * len(waypoints)  # dense road geometry, not straight lines
        # consecutive road points are close together (no long straight "teleport" segments)
        gaps = [haversine_km(a, b) for a, b in zip(road, road[1:], strict=False)]
        assert max(gaps) < 2.0


def test_event_rate_is_independent_of_tick_length(make_vehicle):
    vehicle, _ = make_vehicle(battery_capacity_kwh=5000)

    def harsh_per_100km(dt):
        sim = _sim(vehicle, soc=100, aggr=1.5, seed=11)
        readings = [sim.tick(dt, vehicle) for _ in range(int(3000 * 60 / dt))]
        events = sum(abs(r["acceleration_mps2"]) >= 3.0 for r in readings)
        return events / sim.odometer_km * 100

    assert harsh_per_100km(15) == pytest.approx(harsh_per_100km(60), rel=0.3)


def test_prune_old_telemetry(app, make_vehicle):
    vehicle, _ = make_vehicle()
    now = utcnow()
    for days in (10, 1):
        db.session.add(
            Telemetry(
                vehicle_id=vehicle.id,
                recorded_at=now - timedelta(days=days),
                lat=1,
                lon=1,
                speed_kmh=0,
                soc_pct=50,
            )
        )
    db.session.commit()
    assert prune_old_telemetry(7) == 1
    assert db.session.query(Telemetry).count() == 1


def test_cli_seed_and_simulate_once(app, make_user):
    make_user()
    runner = app.test_cli_runner()
    assert "Added 3 demo vehicles" in runner.invoke(args=["seed-vehicles", "--count", "3"]).output
    assert "Recorded 3 readings" in runner.invoke(args=["simulate", "--once"]).output
    assert "Removed 0 readings" in runner.invoke(args=["prune-telemetry"]).output
