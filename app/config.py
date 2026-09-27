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


class ProductionConfig(Config):
    DEBUG = False
    SESSION_COOKIE_SECURE = True
    SQLALCHEMY_DATABASE_URI = Config.database_url(
        f"sqlite:///{BASE_DIR / 'instance' / 'ev_fleet.db'}"
    )


CONFIGS = {
    "development": DevelopmentConfig,
    "testing": TestingConfig,
    "production": ProductionConfig,
}
