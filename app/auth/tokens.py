"""Signed, time-limited tokens for email links (verification and password reset).

Tokens are signed with SECRET_KEY, so they can't be forged, and carry their own expiry,
so nothing needs to be stored in the database.
"""

from flask import current_app
from itsdangerous import BadSignature, SignatureExpired, URLSafeTimedSerializer

from app.extensions import db
from app.models import User

_VERIFY_SALT = "email-verify"
_RESET_SALT = "password-reset"


def _serializer(salt: str) -> URLSafeTimedSerializer:
    return URLSafeTimedSerializer(current_app.config["SECRET_KEY"], salt=salt)


def make_verify_token(user: User) -> str:
    return _serializer(_VERIFY_SALT).dumps({"uid": user.id, "email": user.email})


def load_verify_token(token: str) -> User | None:
    data = _load(token, _VERIFY_SALT, current_app.config["EMAIL_VERIFY_MAX_AGE"])
    if data is None:
        return None
    user = db.session.get(User, data.get("uid"))
    # The email must still match, so a link can't verify an address the user has since changed.
    return user if user and user.email == data.get("email") else None


def make_reset_token(user: User) -> str:
    # Embedding part of the current password hash makes the link single-use:
    # once the password changes, the hash changes and old links stop working.
    return _serializer(_RESET_SALT).dumps({"uid": user.id, "ph": user.password_hash[-16:]})


def load_reset_token(token: str) -> User | None:
    data = _load(token, _RESET_SALT, current_app.config["PASSWORD_RESET_MAX_AGE"])
    if data is None:
        return None
    user = db.session.get(User, data.get("uid"))
    return user if user and user.password_hash[-16:] == data.get("ph") else None


def _load(token: str, salt: str, max_age: int) -> dict | None:
    try:
        data = _serializer(salt).loads(token, max_age=max_age)
    except (SignatureExpired, BadSignature):
        return None
    return data if isinstance(data, dict) else None
