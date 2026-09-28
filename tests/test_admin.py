from app.extensions import db
from app.models import Role, User


def _admin_session(make_user, login):
    admin = make_user(email="admin@example.com", role=Role.ADMIN, name="Admin")
    login(email="admin@example.com")
    return admin


def test_admin_can_list_users(client, make_user, login):
    _admin_session(make_user, login)
    make_user(email="driver@example.com")
    resp = client.get("/admin/users")
    assert resp.status_code == 200
    assert b"driver@example.com" in resp.data


def test_driver_cannot_access_admin(client, make_user, login):
    make_user()
    login()
    assert client.get("/admin/users").status_code == 403


def test_fleet_manager_cannot_access_admin(client, make_user, login):
    make_user(email="manager@example.com", role=Role.FLEET_MANAGER)
    login(email="manager@example.com")
    assert client.get("/admin/users").status_code == 403


def test_anonymous_user_is_sent_to_login(client):
    resp = client.get("/admin/users")
    assert resp.status_code == 302
    assert "/login" in resp.headers["Location"]


def test_admin_promotes_driver_to_fleet_manager(client, make_user, login):
    _admin_session(make_user, login)
    driver = make_user()
    client.post(f"/admin/users/{driver.id}/role", data={"role": "fleet_manager"})
    assert db.session.get(User, driver.id).role == Role.FLEET_MANAGER


def test_invalid_role_is_ignored(client, make_user, login):
    _admin_session(make_user, login)
    driver = make_user()
    client.post(f"/admin/users/{driver.id}/role", data={"role": "superuser"})
    assert db.session.get(User, driver.id).role == Role.DRIVER


def test_admin_deactivates_and_reactivates_user(client, make_user, login):
    _admin_session(make_user, login)
    driver = make_user()
    client.post(f"/admin/users/{driver.id}/toggle-active")
    assert not db.session.get(User, driver.id).active
    client.post(f"/admin/users/{driver.id}/toggle-active")
    assert db.session.get(User, driver.id).active


def test_admin_cannot_change_own_role_or_status(client, make_user, login):
    admin = _admin_session(make_user, login)
    resp = client.post(
        f"/admin/users/{admin.id}/role", data={"role": "driver"}, follow_redirects=True
    )
    assert b"can&#39;t change your own role" in resp.data
    client.post(f"/admin/users/{admin.id}/toggle-active")
    admin = db.session.get(User, admin.id)
    assert admin.role == Role.ADMIN and admin.active


def test_unknown_user_returns_404(client, make_user, login):
    _admin_session(make_user, login)
    assert client.post("/admin/users/999/role", data={"role": "driver"}).status_code == 404
