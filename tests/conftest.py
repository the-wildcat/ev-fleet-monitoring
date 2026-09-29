import re

import pytest

from app import create_app
from app.extensions import db
from app.models import Role, User, Vehicle

PASSWORD = "Secret123"


@pytest.fixture
def app():
    app = create_app("testing")
    with app.app_context():
        db.create_all()
        yield app
        db.session.remove()
        db.drop_all()
        db.engine.dispose()  # close pooled SQLite connections between tests


@pytest.fixture
def client(app):
    return app.test_client()


@pytest.fixture
def outbox(app):
    """Emails sent during the test (see app/services/email.py)."""
    return app.extensions.setdefault("mail_outbox", [])


@pytest.fixture
def make_user(app):
    def _make_user(
        email="driver@example.com",
        role=Role.DRIVER,
        verified=True,
        active=True,
        name="Test User",
        password=PASSWORD,
    ) -> User:
        user = User(name=name, email=email, role=role, is_verified=verified, active=active)
        user.set_password(password)
        db.session.add(user)
        db.session.commit()
        return user

    return _make_user


@pytest.fixture
def make_vehicle(app):
    """Create a vehicle; returns (vehicle, plain_api_key)."""

    def _make_vehicle(plate="WB12AD3456", driver=None, **fields) -> tuple[Vehicle, str]:
        vehicle = Vehicle(
            make=fields.pop("make", "Tata"),
            model=fields.pop("model", "Nexon EV"),
            plate_number=plate,
            battery_capacity_kwh=fields.pop("battery_capacity_kwh", 40.5),
            efficiency_km_per_kwh=fields.pop("efficiency_km_per_kwh", 7.0),
            driver_id=driver.id if driver else None,
            **fields,
        )
        key = vehicle.issue_api_key()
        db.session.add(vehicle)
        db.session.commit()
        return vehicle, key

    return _make_vehicle


@pytest.fixture
def manager(make_user, login):
    """A logged-in fleet manager."""
    user = make_user(email="manager@example.com", role=Role.FLEET_MANAGER, name="Mona Manager")
    login(email="manager@example.com")
    return user


@pytest.fixture
def login(client):
    def _login(email="driver@example.com", password=PASSWORD):
        return client.post("/login", data={"email": email, "password": password})

    return _login


def extract_link(message) -> str:
    """Pull the first URL out of an email body and return its path."""
    url = re.search(r"https?://\S+", message.get_content()).group(0)
    return re.sub(r"^https?://[^/]+", "", url)
