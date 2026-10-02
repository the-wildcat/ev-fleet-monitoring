import pytest

from app.extensions import db
from app.models import AlertSeverity, AlertType, Role
from app.services.alerts import raise_alert
from app.utils import is_safe_redirect


def _alert(vehicle, alert_type=AlertType.SERVICE_DUE, severity=AlertSeverity.WARNING):
    alert = raise_alert(
        vehicle, alert_type, severity, f"{alert_type.label} on {vehicle.plate_number}"
    )
    db.session.commit()
    return alert


def test_inbox_lists_and_filters(client, manager, make_vehicle):
    v1, _ = make_vehicle(plate="DL01AB0001")
    v2, _ = make_vehicle(plate="DL01AB0002")
    _alert(v1, AlertType.SERVICE_DUE)
    _alert(v2, AlertType.LOW_BATTERY, AlertSeverity.CRITICAL)

    page = client.get("/alerts/").data
    assert b"Service due" in page and b"Low battery" in page
    page = client.get("/alerts/?severity=critical").data
    assert b"Low battery" in page and b"Service due on" not in page
    page = client.get(f"/alerts/?vehicle_id={v1.id}").data
    assert b"DL01AB0001" in page and b"Low battery on" not in page
    page = client.get("/alerts/?type=service_due").data
    assert b"Service due on" in page and b"Low battery on" not in page


def test_acknowledge_and_resolve_with_note(client, manager, make_vehicle):
    vehicle, _ = make_vehicle()
    alert = _alert(vehicle)
    client.post(f"/alerts/{alert.id}/acknowledge")
    assert alert.acknowledged_at is not None and alert.acknowledged_by_id == manager.id

    resp = client.post(
        f"/alerts/{alert.id}/resolve",
        data={"note": "Service booked for Monday"},
        follow_redirects=True,
    )
    assert b"Resolved: Service due" in resp.data
    assert alert.resolved_at is not None and alert.resolved_by_id == manager.id
    history = client.get("/alerts/?status=resolved").data
    assert b"Service booked for Monday" in history and b"Mona Manager" in history


def test_auto_resolved_alerts_say_so_in_history(client, manager, make_vehicle):
    from app.services.alerts import resolve_alert

    vehicle, _ = make_vehicle()
    _alert(vehicle)
    resolve_alert(vehicle, AlertType.SERVICE_DUE)
    db.session.commit()
    assert b"automatically (condition cleared)" in client.get("/alerts/?status=resolved").data


def test_drivers_see_only_their_vehicles_and_cannot_act(client, make_user, login, make_vehicle):
    driver = make_user()
    mine, _ = make_vehicle(plate="DL01AB0001", driver=driver)
    theirs, _ = make_vehicle(plate="DL01AB0002")
    my_alert = _alert(mine)
    other_alert = _alert(theirs)
    login()
    page = client.get("/alerts/").data
    assert b"DL01AB0001" in page and b"DL01AB0002" not in page
    assert b"Acknowledge" not in page  # read-only for drivers
    assert client.post(f"/alerts/{my_alert.id}/acknowledge").status_code == 403
    assert client.post(f"/alerts/{other_alert.id}/resolve").status_code == 403


def test_manager_gets_404_for_unknown_alert(client, manager):
    assert client.post("/alerts/999/acknowledge").status_code == 404


def test_unsafe_next_is_ignored(client, manager, make_vehicle):
    alert = _alert(make_vehicle()[0])
    resp = client.post(f"/alerts/{alert.id}/acknowledge", data={"next": "https://evil.example.com"})
    assert resp.headers["Location"].endswith("/alerts/")


def test_sidebar_badge_counts_open_alerts(client, manager, make_vehicle):
    vehicle, _ = make_vehicle()
    _alert(vehicle, AlertType.SERVICE_DUE)
    _alert(vehicle, AlertType.DEVICE_OFFLINE)
    assert b'title="Open alerts">2</span>' in client.get("/").data


def test_resolved_alert_reopens_if_condition_persists(app, make_vehicle):
    vehicle, _ = make_vehicle()
    first = _alert(vehicle)
    first.resolved_at = first.created_at  # resolved by hand
    db.session.commit()
    second = _alert(vehicle)  # rule fires again
    assert second.id != first.id and second.resolved_at is None


@pytest.mark.parametrize(
    ("target", "safe"),
    [
        ("/alerts/?page=2", True),
        ("/profile", True),
        ("https://evil.example.com", False),
        ("//evil.example.com", False),
        ("/\\evil.example.com", False),
        ("javascript:alert(1)", False),
        ("", False),
        (None, False),
    ],
)
def test_is_safe_redirect(target, safe):
    assert is_safe_redirect(target) is safe


def test_role_check_helper_unchanged(make_user):
    assert make_user(role=Role.FLEET_MANAGER).is_manager
