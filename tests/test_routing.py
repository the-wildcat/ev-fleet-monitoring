from unittest.mock import MagicMock

import numpy as np
import pytest
import requests

from app.services import routing
from app.services.routing import (
    EVParams,
    Place,
    Route,
    RoutingError,
    Stations,
    choose_plan,
    estimated_route,
    geocode,
    load_stations,
    parse_latlon,
    plan_trip,
    road_routes,
)

GEO = {"base_url": "https://nominatim.test", "user_agent": "tests", "timeout": 1}

# A straight north-south test route: 1 degree of latitude is ~111 km.
A = Place(20.0, 78.0, "A")
B = Place(23.0, 78.0, "B")  # ~333 km north of A


def straight_route(origin=A, dest=B, n=300) -> Route:
    coords = [(origin.lat + (dest.lat - origin.lat) * t, origin.lon) for t in np.linspace(0, 1, n)]
    km = float(routing.haversine_matrix([origin.lat], [origin.lon], [dest.lat], [dest.lon])[0, 0])
    return Route(km, km / 60 * 60, coords, "osrm")  # 60 km/h


def stations_at(*lats, lon_offset=0.0, kw=60.0) -> Stations:
    n = len(lats)
    power = kw if isinstance(kw, list | tuple) else [kw] * n
    return Stations(
        names=np.array([f"Station {i + 1}" for i in range(n)]),
        operators=np.array(["Op"] * n),
        addresses=np.array([""] * n),
        cities=np.array(["Town"] * n),
        lat=np.array(lats, dtype=float),
        lon=np.full(n, 78.0 + lon_offset),
        power_kw=np.array(power, dtype=float),
        connector_types=np.array(["CCS2"] * n),
    )


# EV with 200 km of range at 100% (40 kWh x 5 km/kWh), accepting up to 50 kW.
def ev(soc, target=80.0, reserve=15.0, min_charger_kw=25.0) -> EVParams:
    return EVParams(
        capacity_kwh=40,
        efficiency_km_per_kwh=5,
        start_soc_pct=soc,
        reserve_pct=reserve,
        target_soc_pct=target,
        max_charge_kw=50,
        min_charger_kw=min_charger_kw,
    )


# --- station data -----------------------------------------------------------------------------


def test_load_stations_keeps_car_chargers_only(tmp_path):
    csv = tmp_path / "stations.csv"
    csv.write_text(
        "name,operator,city,address,lat,lon,car_compatible,max_car_kw,connector_types\n"
        "Fast,Tata Power,Delhi,Addr,28.6,77.2,True,60,CCS2\n"
        "Scooters,Ather,Delhi,Addr,28.7,77.3,False,,LEV (2/3-wheeler)\n"
        "Unknown power,BSES,Delhi,,28.5,77.1,True,,Type 2 AC\n"
        "Bad coords,X,Patna,,25.6,851.1,True,30,CCS2\n"
    )
    st = load_stations(csv)
    assert list(st.names) == ["Fast", "Unknown power"]
    assert list(st.power_kw) == [60.0, routing.UNKNOWN_POWER_KW]


def test_real_dataset_loads(app):
    st = load_stations(app.config["CHARGING_STATIONS_PATH"])
    assert len(st) > 3000
    assert ((st.lat > 6) & (st.lat < 37.5) & (st.lon > 68) & (st.lon < 98)).all()
    assert (st.power_kw > 0).all()


@pytest.mark.parametrize(
    ("raw", "expected"),
    [
        ("CCS", ("CCS2", True)),
        ("CCS-2 (IS-17017-2-3)", ("CCS2", True)),
        ("CHAdeMO", ("CHAdeMO", True)),
        ("Type-II AC", ("Type 2 AC", True)),
        ("Bharat AC", ("Bharat AC-001", True)),
        ("Bharat DC-001", ("Bharat DC-001", True)),
        ("LEV DC Charge Point\n(IS-17017-2-7)", ("LEV (2/3-wheeler)", False)),
        ("LEV AC Charge point", ("LEV (2/3-wheeler)", False)),
        ("something new", ("Other", False)),
    ],
)
def test_connector_normalisation(raw, expected):
    from scripts.build_charging_stations import normalise_connector

    assert normalise_connector(raw) == expected


# --- geocoding --------------------------------------------------------------------------------


@pytest.fixture(autouse=True)
def _clear_geocode_cache():
    routing._nominatim_search.cache_clear()


def test_parse_latlon():
    assert parse_latlon("28.6139, 77.2090") == Place(28.6139, 77.209, "28.61390, 77.20900")
    assert parse_latlon("Delhi") is None
    assert parse_latlon("123, 77") is None


def test_geocode_uses_nominatim_with_user_agent(monkeypatch):
    get = MagicMock()
    get.return_value.json.return_value = [
        {"lat": "26.91", "lon": "75.79", "display_name": "Jaipur"}
    ]
    monkeypatch.setattr(routing.requests, "get", get)
    place = geocode("Jaipur", **GEO)
    assert place == Place(26.91, 75.79, "Jaipur")
    _, kwargs = get.call_args
    assert kwargs["headers"]["User-Agent"] == "tests"
    assert kwargs["params"]["countrycodes"] == "in"


def test_geocode_coordinates_skip_network(monkeypatch):
    monkeypatch.setattr(routing.requests, "get", MagicMock(side_effect=AssertionError))
    assert geocode("26.9, 75.8", **GEO).lat == 26.9


def test_geocode_not_found_and_network_error(monkeypatch):
    get = MagicMock()
    get.return_value.json.return_value = []
    monkeypatch.setattr(routing.requests, "get", get)
    with pytest.raises(RoutingError, match="Couldn't find"):
        geocode("Atlantis", **GEO)

    monkeypatch.setattr(routing.requests, "get", MagicMock(side_effect=requests.ConnectionError))
    with pytest.raises(RoutingError, match="unreachable"):
        geocode("Somewhere", **GEO)

    with pytest.raises(RoutingError):
        geocode("   ", **GEO)


# --- OSRM routing -----------------------------------------------------------------------------


def _osrm_response(*routes):
    return {
        "code": "Ok",
        "routes": [
            {
                "distance": km * 1000,
                "duration": mins * 60,
                "geometry": {"coordinates": [[78.0, 20.0], [78.0, 21.0]]},
            }
            for km, mins in routes
        ],
    }


def test_road_routes_parses_alternatives(monkeypatch):
    get = MagicMock()
    get.return_value.json.return_value = _osrm_response((300, 200), (280, 210))
    monkeypatch.setattr(routing.requests, "get", get)
    routes = road_routes(A, B, base_url="https://osrm.test", timeout=1)
    assert [(r.distance_km, r.duration_min, r.source) for r in routes] == [
        (300, 200, "osrm"),
        (280, 210, "osrm"),
    ]
    assert routes[0].coords[0] == (20.0, 78.0)  # GeoJSON lon,lat converted to lat,lon


def test_road_routes_falls_back_to_estimate(monkeypatch):
    monkeypatch.setattr(routing.requests, "get", MagicMock(side_effect=requests.Timeout))
    (route,) = road_routes(A, B, base_url="https://osrm.test", timeout=1)
    assert route.source == "estimate"
    assert route.distance_km == pytest.approx(estimated_route(A, B).distance_km)


def test_road_routes_no_route(monkeypatch):
    get = MagicMock()
    get.return_value.json.return_value = {"code": "NoRoute", "routes": []}
    monkeypatch.setattr(routing.requests, "get", get)
    with pytest.raises(RoutingError, match="No drivable"):
        road_routes(A, B, base_url="https://osrm.test", timeout=1)


# --- charging-stop planning -------------------------------------------------------------------


def test_short_trip_needs_no_stops():
    route = straight_route(dest=Place(21.0, 78.0, "near"))  # ~111 km
    plan = plan_trip(A, route_end(route), route, ev(90), stations_at(20.5))
    assert plan.feasible and plan.stops == []
    assert plan.arrive_soc_pct == pytest.approx(90 - 111.2 / 200 * 100, abs=0.5)


def route_end(route):
    return Place(*route.coords[-1], "end")


def test_long_trip_stops_at_farthest_reachable_station():
    # Trip ~334 km; stations at ~56, ~111, ~167 and ~245 km.
    # Start 90% (reserve 15%): range 150 km -> farthest reachable is #2 at 111 km.
    # Each stop charges to 80%: range 130 km.
    #   from 111 km: #4 is 134 km away (too far), so #3 at 167 km;
    #   from 167 km: 167 km still to go (> 130), so #4 at 245 km;
    #   from 245 km: 89 km to go, arrive with 80 - 89/200*100 ≈ 35%.
    plan = plan_trip(A, B, straight_route(), ev(90), stations_at(20.5, 21.0, 21.5, 22.2))
    assert plan.feasible
    assert [s.name for s in plan.stops] == ["Station 2", "Station 3", "Station 4"]
    assert plan.arrive_soc_pct == pytest.approx(35.5, abs=1)
    first = plan.stops[0]
    assert first.route_km == pytest.approx(111, abs=2)
    assert first.arrive_soc_pct == pytest.approx(90 - 111.2 / 200 * 100, abs=1)
    assert first.depart_soc_pct == 80
    assert first.charge_min == pytest.approx(
        (80 - first.arrive_soc_pct) / 100 * 40 / 50 * 60, abs=1
    )
    assert plan.arrive_soc_pct >= 15  # never below the reserve
    assert plan.total_min > plan.route.duration_min  # charging time included


def test_slow_chargers_are_skipped_for_stops():
    # Station 2 (~111 km) is a 7.4 kW AC charger, so the planner stops at Station 1 (~56 km)
    # instead, the farthest *fast* charger in reach.
    stations = stations_at(20.5, 21.0, 21.5, 22.2, kw=[60, 7.4, 60, 60])
    plan = plan_trip(A, B, straight_route(), ev(90), stations)
    assert "Station 2" not in [s.name for s in plan.stops]
    assert all(s.station_kw >= 25 for s in plan.stops)
    assert len(plan.nearby) == 4  # but every car charger is still shown on the map


def test_charging_power_is_limited_by_station_and_car():
    stations = stations_at(21.0, 22.0, kw=[150, 30])  # ~111 and ~222 km
    plan = plan_trip(A, B, straight_route(), ev(90), stations)
    first, second = plan.stops
    assert (first.station_kw, first.charge_kw) == (150, 50)  # car accepts at most 50 kW
    assert (second.station_kw, second.charge_kw) == (30, 30)  # station is the bottleneck
    assert second.charge_min == pytest.approx(second.charge_kwh / 30 * 60, abs=1)


def test_infeasible_gap_is_explained():
    plan = plan_trip(A, B, straight_route(), ev(90), stations_at(20.2))  # nothing after km 22
    assert not plan.feasible
    assert "No charger of 25 kW or more within reach" in plan.problem


def test_below_reserve_must_charge_first():
    plan = plan_trip(A, B, straight_route(), ev(10), stations_at(21.0))
    assert not plan.feasible and "Charge before departing" in plan.problem


def test_stations_outside_corridor_are_ignored():
    far = stations_at(21.0, lon_offset=0.2)  # ~21 km east of the route
    plan = plan_trip(A, B, straight_route(), ev(90), far, corridor_km=5)
    assert plan.nearby == [] and not plan.feasible
    plan = plan_trip(A, B, straight_route(), ev(90), far, corridor_km=25)
    assert len(plan.nearby) == 1


def test_target_must_exceed_reserve():
    with pytest.raises(RoutingError):
        plan_trip(A, B, straight_route(), ev(90, target=10, reserve=15), stations_at(21.0))


def test_choose_plan_prefers_feasible_alternative():
    fast = straight_route()
    # The "slower" alternative goes 0.5 degrees east, where the stations are.
    slow_coords = [(lat, 78.5) for lat, _ in fast.coords]
    slow = Route(fast.distance_km, fast.duration_min + 30, slow_coords, "osrm")
    stations = stations_at(21.0, 21.6, 22.2, lon_offset=0.5)  # ~52 km from the fast route
    result = choose_plan(A, B, [fast, slow], ev(90), stations)
    assert result.best.route is slow and result.best.feasible
    assert result.alternatives[0].route is fast and not result.alternatives[0].feasible


def test_choose_plan_suggests_full_charge():
    # Stations at ~106 and ~211 km. Start 90% reaches the first. Charging to 60% there gives
    # only 90 km of range (the next station is 105 km on): impossible. Charging to 100% gives
    # 170 km: reach the second station, then 123 km to the destination.
    stations = stations_at(20.95, 21.9)
    result = choose_plan(A, B, [straight_route()], ev(90, target=60), stations)
    assert not result.best.feasible
    assert result.full_charge_suggestion is not None and result.full_charge_suggestion.feasible


# --- planner page -----------------------------------------------------------------------------


def fake_geocode(query, **_):
    places = {"start": A, "end": B}
    if query not in places:
        raise RoutingError("nope")
    return places[query]


@pytest.fixture
def mocked_services(monkeypatch):
    monkeypatch.setattr("app.routing.routes.geocode", fake_geocode)
    monkeypatch.setattr("app.routing.routes.road_routes", lambda *a, **kw: [straight_route()])
    monkeypatch.setattr(
        "app.routing.routes.load_stations", lambda path: stations_at(20.5, 21.0, 21.5, 22.0)
    )


def test_planner_page_with_vehicle(client, manager, make_vehicle, mocked_services):
    vehicle, _ = make_vehicle(battery_capacity_kwh=40, efficiency_km_per_kwh=5, last_soc_pct=90)
    assert client.get("/routes/plan").status_code == 200
    resp = client.post(
        "/routes/plan",
        data={
            "vehicle_id": vehicle.id,
            "origin": "start",
            "destination": "end",
            "reserve_pct": 15,
            "target_soc_pct": 80,
            "max_charge_kw": 50,
            "min_charger_kw": 25,
            "corridor_km": 10,
        },
    )
    assert resp.status_code == 200
    assert b"Charging stops" in resp.data and b"Station" in resp.data
    assert b"route-map" in resp.data


def test_planner_page_from_vehicle_location(client, manager, make_vehicle, mocked_services):
    vehicle, _ = make_vehicle(last_lat=20.0, last_lon=78.0, last_soc_pct=95)
    resp = client.post(
        "/routes/plan",
        data={
            "vehicle_id": vehicle.id,
            "use_vehicle_location": "y",
            "destination": "end",
            "reserve_pct": 15,
            "target_soc_pct": 80,
            "max_charge_kw": 50,
            "min_charger_kw": 25,
            "corridor_km": 10,
        },
    )
    assert b"current location" in resp.data


def test_planner_page_shows_errors(client, manager, mocked_services):
    resp = client.post(
        "/routes/plan",
        data={
            "vehicle_id": 0,
            "origin": "start",
            "destination": "nowhere",
            "battery_capacity_kwh": 40,
            "efficiency_km_per_kwh": 5,
            "start_soc_pct": 80,
            "reserve_pct": 15,
            "target_soc_pct": 80,
            "max_charge_kw": 50,
            "min_charger_kw": 25,
            "corridor_km": 10,
        },
    )
    assert b"nope" in resp.data

    resp = client.post(
        "/routes/plan",
        data={
            "vehicle_id": 0,
            "origin": "start",
            "destination": "end",
            "reserve_pct": 15,
            "target_soc_pct": 80,
            "max_charge_kw": 50,
            "min_charger_kw": 25,
            "corridor_km": 10,
        },
    )
    assert b"For a custom EV" in resp.data


def test_driver_cannot_plan_with_other_vehicle(
    client, make_user, login, make_vehicle, mocked_services
):
    make_user()
    other, _ = make_vehicle(last_soc_pct=80)
    login()
    resp = client.post(
        "/routes/plan",
        data={
            "vehicle_id": other.id,
            "origin": "start",
            "destination": "end",
            "reserve_pct": 15,
            "target_soc_pct": 80,
            "max_charge_kw": 50,
            "min_charger_kw": 25,
            "corridor_km": 10,
        },
    )
    assert b"Charging stops" not in resp.data  # not offered in the list, so rejected
