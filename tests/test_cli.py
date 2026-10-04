from app.extensions import db
from app.models import Role, User, Vehicle


def _user(email):
    return db.session.execute(db.select(User).filter_by(email=email)).scalar_one_or_none()


def test_create_admin(app):
    runner = app.test_cli_runner()
    result = runner.invoke(
        args=[
            "create-admin",
            "--email",
            "Boss@Example.com",
            "--name",
            "Boss",
            "--password",
            "Admin123",
        ]
    )
    assert result.exit_code == 0, result.output
    admin = _user("boss@example.com")
    assert admin.role == Role.ADMIN and admin.is_verified and admin.check_password("Admin123")


def test_create_admin_rejects_duplicate(app, make_user):
    make_user(email="boss@example.com")
    result = app.test_cli_runner().invoke(
        args=[
            "create-admin",
            "--email",
            "boss@example.com",
            "--name",
            "Boss",
            "--password",
            "Admin123",
        ]
    )
    assert result.exit_code != 0
    assert "already exists" in result.output


def test_create_admin_rejects_short_password(app):
    result = app.test_cli_runner().invoke(
        args=["create-admin", "--email", "a@example.com", "--name", "A", "--password", "short"]
    )
    assert result.exit_code != 0


def test_set_role(app, make_user):
    make_user(email="ravi@example.com")
    result = app.test_cli_runner().invoke(args=["set-role", "ravi@example.com", "fleet_manager"])
    assert result.exit_code == 0, result.output
    assert _user("ravi@example.com").role == Role.FLEET_MANAGER


def test_set_role_unknown_user(app):
    result = app.test_cli_runner().invoke(args=["set-role", "ghost@example.com", "admin"])
    assert result.exit_code != 0


def test_bootstrap_creates_admin_and_demo_fleet_once(app, monkeypatch):
    monkeypatch.setenv("ADMIN_EMAIL", "Owner@Example.com")
    monkeypatch.setenv("ADMIN_PASSWORD", "Owner12345")
    monkeypatch.setenv("SEED_DEMO_DATA", "true")
    runner = app.test_cli_runner()

    first = runner.invoke(args=["bootstrap"])
    assert first.exit_code == 0, first.output
    admin = _user("owner@example.com")
    assert admin.role == Role.ADMIN and admin.is_verified and admin.check_password("Owner12345")
    assert db.session.scalar(db.select(db.func.count(Vehicle.id))) == 8
    drivers = db.session.scalars(db.select(User).filter_by(role=Role.DRIVER)).all()
    assert len(drivers) == 4

    second = runner.invoke(args=["bootstrap"])  # every restart runs it again
    assert second.exit_code == 0, second.output
    assert "already exists" in second.output
    assert db.session.scalar(db.select(db.func.count(Vehicle.id))) == 8
    assert db.session.scalar(db.select(db.func.count(User.id))) == 5


def test_bootstrap_without_settings_does_nothing(app, monkeypatch):
    for var in ("ADMIN_EMAIL", "ADMIN_PASSWORD", "SEED_DEMO_DATA"):
        monkeypatch.delenv(var, raising=False)
    result = app.test_cli_runner().invoke(args=["bootstrap"])
    assert result.exit_code == 0, result.output
    assert db.session.scalar(db.select(db.func.count(User.id))) == 0


def test_bootstrap_rejects_short_admin_password(app, monkeypatch):
    monkeypatch.setenv("ADMIN_EMAIL", "owner@example.com")
    monkeypatch.setenv("ADMIN_PASSWORD", "short")
    result = app.test_cli_runner().invoke(args=["bootstrap"])
    assert result.exit_code != 0


def test_rotate_api_key_prints_a_working_key(app, make_vehicle):
    vehicle, old_key = make_vehicle(plate="KA01AB1234")
    result = app.test_cli_runner().invoke(args=["rotate-api-key", "ka01ab1234"])
    assert result.exit_code == 0, result.output
    new_key = result.output.strip()
    assert new_key != old_key
    assert vehicle.api_key_hash == Vehicle.hash_api_key(new_key)


def test_rotate_api_key_unknown_plate(app):
    result = app.test_cli_runner().invoke(args=["rotate-api-key", "XX00XX0000"])
    assert result.exit_code != 0
