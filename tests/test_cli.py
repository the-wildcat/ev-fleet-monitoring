from app.extensions import db
from app.models import Role, User


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
