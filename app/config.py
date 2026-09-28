"""Application settings, loaded from environment variables (see .env.example)."""

import os
from pathlib import Path

BASE_DIR = Path(__file__).resolve().parent.parent


class Config:
    SECRET_KEY = os.environ.get("SECRET_KEY")
    SQLALCHEMY_TRACK_MODIFICATIONS = False
    LOG_LEVEL = os.environ.get("LOG_LEVEL", "INFO")
    DATA_DIR = BASE_DIR / "data"

    SESSION_COOKIE_HTTPONLY = True
    SESSION_COOKIE_SAMESITE = "Lax"
    REMEMBER_COOKIE_HTTPONLY = True

    # Auth security
    EMAIL_VERIFY_MAX_AGE = 24 * 3600  # seconds a verification link stays valid
    PASSWORD_RESET_MAX_AGE = 3600
    LOGIN_MAX_ATTEMPTS = 5
    LOGIN_LOCKOUT_MINUTES = 15

    # Email: SMTP when MAIL_SERVER is set, otherwise emails are written to the log.
    MAIL_SERVER = os.environ.get("MAIL_SERVER", "")
    MAIL_PORT = int(os.environ.get("MAIL_PORT", "587"))
    MAIL_USE_TLS = os.environ.get("MAIL_USE_TLS", "true").lower() == "true"
    MAIL_USERNAME = os.environ.get("MAIL_USERNAME", "")
    MAIL_PASSWORD = os.environ.get("MAIL_PASSWORD", "")
    # Gmail rejects mail whose From address isn't the authenticated account, so default to it.
    MAIL_DEFAULT_SENDER = os.environ.get("MAIL_DEFAULT_SENDER") or (
        f"EV Fleet Monitor <{MAIL_USERNAME}>"
        if MAIL_USERNAME
        else "EV Fleet Monitor <no-reply@localhost>"
    )

    @staticmethod
    def database_url(default: str) -> str:
        url = os.environ.get("DATABASE_URL") or default
        # Render/Heroku hand out "postgres://", which SQLAlchemy 2 no longer accepts.
        if url.startswith("postgres://"):
            url = url.replace("postgres://", "postgresql+psycopg://", 1)
        return url


class DevelopmentConfig(Config):
    DEBUG = True
    SECRET_KEY = Config.SECRET_KEY or "dev-only-insecure-key"
    SQLALCHEMY_DATABASE_URI = Config.database_url(
        f"sqlite:///{BASE_DIR / 'instance' / 'ev_fleet.db'}"
    )


class TestingConfig(Config):
    TESTING = True
    SECRET_KEY = "test-key"
    SQLALCHEMY_DATABASE_URI = "sqlite:///:memory:"
    WTF_CSRF_ENABLED = False
    MAIL_SERVER = ""  # never send real email from tests


class ProductionConfig(Config):
    DEBUG = False
    SESSION_COOKIE_SECURE = True
    REMEMBER_COOKIE_SECURE = True
    SQLALCHEMY_DATABASE_URI = Config.database_url(
        f"sqlite:///{BASE_DIR / 'instance' / 'ev_fleet.db'}"
    )


CONFIGS = {
    "development": DevelopmentConfig,
    "testing": TestingConfig,
    "production": ProductionConfig,
}
