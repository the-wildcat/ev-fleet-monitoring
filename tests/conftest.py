import re

import pytest

from app import create_app
from app.extensions import db
from app.models import Role, User

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
def login(client):
    def _login(email="driver@example.com", password=PASSWORD):
        return client.post("/login", data={"email": email, "password": password})

    return _login


def extract_link(message) -> str:
    """Pull the first URL out of an email body and return its path."""
    url = re.search(r"https?://\S+", message.get_content()).group(0)
    return re.sub(r"^https?://[^/]+", "", url)
