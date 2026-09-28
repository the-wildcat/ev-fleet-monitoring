from datetime import timedelta

from app.extensions import db
from app.models import Role, User
from app.utils import utcnow
from tests.conftest import PASSWORD, extract_link


def _register(client, email="new@example.com", password="Passw0rd!", confirm=None):
    return client.post(
        "/register",
        data={
            "name": "New Driver",
            "email": email,
            "password": password,
            "confirm": confirm or password,
        },
    )


def _get_user(email):
    return db.session.execute(db.select(User).filter_by(email=email)).scalar_one_or_none()


# --- registration -----------------------------------------------------------------------------


def test_register_creates_unverified_driver_and_sends_verification(client, outbox):
    resp = _register(client, email="  New@Example.com ")
    assert resp.status_code == 200
    assert b"Check your email" in resp.data

    user = _get_user("new@example.com")  # email is normalised
    assert user is not None
    assert user.role == Role.DRIVER
    assert not user.is_verified
    assert user.password_hash != "Passw0rd!"  # stored hashed, never in plain text
    assert len(outbox) == 1 and "Verify" in outbox[0]["Subject"]


def test_register_rejects_weak_password(client):
    resp = _register(client, password="password")
    assert b"at least one letter and one number" in resp.data
    assert _get_user("new@example.com") is None


def test_register_rejects_mismatched_confirmation(client):
    resp = _register(client, password="Passw0rd!", confirm="Different1")
    assert b"Passwords must match" in resp.data


def test_register_existing_email_gives_same_response_and_warns_owner(client, make_user, outbox):
    make_user(email="taken@example.com")
    resp = _register(client, email="taken@example.com")
    assert b"Check your email" in resp.data  # no hint that the email is registered
    assert len(outbox) == 1 and "Sign-up attempt" in outbox[0]["Subject"]
    assert db.session.query(User).count() == 1


# --- email verification -----------------------------------------------------------------------


def test_verification_link_verifies_account(client, outbox):
    _register(client)
    resp = client.get(extract_link(outbox[0]), follow_redirects=True)
    assert b"Your email is verified" in resp.data
    assert _get_user("new@example.com").is_verified


def test_invalid_verification_token_is_rejected(client):
    resp = client.get("/verify/not-a-real-token", follow_redirects=True)
    assert b"invalid or has expired" in resp.data


def test_expired_verification_token_is_rejected(app, client, outbox):
    _register(client)
    app.config["EMAIL_VERIFY_MAX_AGE"] = -1
    resp = client.get(extract_link(outbox[0]), follow_redirects=True)
    assert b"invalid or has expired" in resp.data
    assert not _get_user("new@example.com").is_verified


def test_resend_verification_sends_new_link_only_to_unverified(client, make_user, outbox):
    make_user(email="pending@example.com", verified=False)
    make_user(email="done@example.com", verified=True)
    client.post("/verify/resend", data={"email": "pending@example.com"})
    client.post("/verify/resend", data={"email": "done@example.com"})
    client.post("/verify/resend", data={"email": "nobody@example.com"})
    assert [m["To"] for m in outbox] == ["pending@example.com"]


# --- login / logout ---------------------------------------------------------------------------


def test_login_success_redirects_and_records_login(client, make_user, login):
    user = make_user()
    resp = login()
    assert resp.status_code == 302
    assert user.last_login_at is not None
    assert b"Log out" in client.get("/").data


def test_login_wrong_password(make_user, login):
    make_user()
    resp = login(password="WrongPass1")
    assert resp.status_code == 401
    assert b"Invalid email or password" in resp.data


def test_login_unknown_email_gives_same_message(login):
    resp = login(email="ghost@example.com")
    assert resp.status_code == 401
    assert b"Invalid email or password" in resp.data


def test_login_requires_verified_email(make_user, login):
    make_user(verified=False)
    resp = login()
    assert resp.status_code == 403
    assert b"verify your email" in resp.data


def test_login_blocked_for_deactivated_account(make_user, login):
    make_user(active=False)
    resp = login()
    assert resp.status_code == 403
    assert b"deactivated" in resp.data


def test_account_locks_after_repeated_failures(app, make_user, login):
    user = make_user()
    for _ in range(app.config["LOGIN_MAX_ATTEMPTS"]):
        login(password="WrongPass1")
    assert user.is_locked()

    resp = login()  # even the correct password is refused while locked
    assert resp.status_code == 429
    assert b"Too many failed attempts" in resp.data


def test_lock_expires(make_user, login):
    user = make_user()
    user.locked_until = utcnow() - timedelta(minutes=1)
    db.session.commit()
    assert login().status_code == 302


def test_login_redirects_to_safe_next_only(client, make_user):
    make_user()
    data = {"email": "driver@example.com", "password": PASSWORD}
    resp = client.post("/login?next=/profile", data=data)
    assert resp.headers["Location"].endswith("/profile")

    client.post("/logout")
    resp = client.post("/login?next=https://evil.example.com/", data=data)
    assert "evil.example.com" not in resp.headers["Location"]


def test_logout(client, make_user, login):
    make_user()
    login()
    resp = client.post("/logout", follow_redirects=True)
    assert b"You have been logged out" in resp.data
    assert client.get("/profile").status_code == 302  # back to the login page


def test_protected_page_requires_login(client):
    resp = client.get("/profile")
    assert resp.status_code == 302
    assert "/login" in resp.headers["Location"]


# --- password reset ---------------------------------------------------------------------------


def test_password_reset_flow_and_link_is_single_use(client, make_user, outbox, login):
    make_user()
    resp = client.post("/forgot-password", data={"email": "driver@example.com"})
    assert b"If an account exists" in resp.data
    link = extract_link(outbox[0])

    resp = client.post(link, data={"password": "NewPass123", "confirm": "NewPass123"})
    assert resp.status_code == 302
    assert login(password="NewPass123").status_code == 302

    client.post("/logout")
    resp = client.get(link, follow_redirects=True)
    assert b"already used" in resp.data


def test_forgot_password_unknown_email_sends_nothing(client, outbox):
    resp = client.post("/forgot-password", data={"email": "ghost@example.com"})
    assert b"If an account exists" in resp.data
    assert outbox == []


def test_password_reset_also_verifies_and_unlocks(client, make_user, outbox):
    user = make_user(verified=False)
    user.locked_until = utcnow() + timedelta(minutes=10)
    db.session.commit()
    client.post("/forgot-password", data={"email": user.email})
    client.post(extract_link(outbox[0]), data={"password": "NewPass123", "confirm": "NewPass123"})
    assert user.is_verified and not user.is_locked()


# --- profile ----------------------------------------------------------------------------------


def test_change_password(client, make_user, login):
    user = make_user()
    login()
    resp = client.post(
        "/profile",
        data={"current_password": PASSWORD, "password": "Changed123", "confirm": "Changed123"},
        follow_redirects=True,
    )
    assert b"Password changed" in resp.data
    assert user.check_password("Changed123")


def test_change_password_requires_current_password(client, make_user, login):
    make_user()
    login()
    resp = client.post(
        "/profile",
        data={"current_password": "Wrong1234", "password": "Changed123", "confirm": "Changed123"},
    )
    assert b"Current password is incorrect" in resp.data
