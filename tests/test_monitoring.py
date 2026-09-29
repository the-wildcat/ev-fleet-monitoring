from datetime import timedelta

from app.extensions import db
from app.utils import utcnow


def _set_state(vehicle, **fields):
    for name, value in fields.items():
        setattr(vehicle, name, value)
    db.session.commit()


def test_fleet_json_states_and_summary(client, manager, make_vehicle):
    now = utcnow()
    moving, _ = make_vehicle(plate="DL01AB0001")
    charging, _ = make_vehicle(plate="DL01AB0002")
    offline, _ = make_vehicle(plate="DL01AB0003")
    make_vehicle(plate="DL01AB0004")  # never reported
    _set_state(
        moving, last_lat=28.6, last_lon=77.2, last_speed_kmh=40, last_soc_pct=15, last_seen_at=now
    )
    _set_state(
        charging,
        last_lat=28.6,
        last_lon=77.2,
        last_speed_kmh=0,
        last_soc_pct=50,
        last_is_charging=True,
        last_seen_at=now,
    )
    _set_state(
        offline,
        last_lat=28.6,
        last_lon=77.2,
        last_soc_pct=80,
        last_seen_at=now - timedelta(hours=1),
    )

    data = client.get("/monitoring/fleet.json").get_json()
    states = {v["plate"]: v["state"] for v in data["vehicles"]}
    assert states == {
        "DL01AB0001": "moving",
        "DL01AB0002": "charging",
        "DL01AB0003": "offline",
        "DL01AB0004": "no_data",
    }
    s = data["summary"]
    assert (s["total"], s["moving"], s["charging"], s["offline"], s["low_battery"]) == (
        4,
        1,
        1,
        2,
        1,
    )
    assert s["avg_soc_pct"] == round((15 + 50 + 80) / 3, 1)

    v = next(v for v in data["vehicles"] if v["plate"] == "DL01AB0001")
    assert v["range_km"] == round(40.5 * 0.15 * 7.0, 1)


def test_driver_feed_only_contains_assigned_vehicles(client, make_user, login, make_vehicle):
    driver = make_user()
    make_vehicle(plate="DL01AB0001", driver=driver)
    make_vehicle(plate="DL01AB0002")
    login()
    plates = [v["plate"] for v in client.get("/monitoring/fleet.json").get_json()["vehicles"]]
    assert plates == ["DL01AB0001"]


def test_live_page_and_dashboard_render(client, manager, make_vehicle):
    make_vehicle()
    assert b"fleet-map" in client.get("/monitoring/live").data
    dashboard = client.get("/").data
    assert b"Fleet overview" in dashboard and b"WB12AD3456" in dashboard


def test_fleet_json_requires_login(client):
    assert client.get("/monitoring/fleet.json").status_code == 302


def test_localtime_filter_shows_ist(app):
    from datetime import datetime

    f = app.jinja_env.filters["localtime"]
    assert f(datetime(2026, 1, 1, 0, 0), "%H:%M") == "05:30"
    assert f(None) == "—"
