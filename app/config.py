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

    # Display timezone (timestamps are stored in UTC)
    APP_TIMEZONE = os.environ.get("APP_TIMEZONE", "Asia/Kolkata")

    # Telemetry
    OFFLINE_AFTER_SECONDS = 120  # no reading for this long -> vehicle shown as offline
    TELEMETRY_RETENTION_DAYS = int(os.environ.get("TELEMETRY_RETENTION_DAYS", "7"))
    TELEMETRY_MAX_BATCH = 100

    # Built-in telemetry simulator (stands in for real vehicle devices)
    SIMULATOR_ENABLED = os.environ.get("SIMULATOR_ENABLED", "true").lower() == "true"
    SIMULATOR_INTERVAL_SECONDS = float(os.environ.get("SIMULATOR_INTERVAL_SECONDS", "5"))
    # Each real interval simulates this many times as much driving (12 -> 5 s becomes 1 min).
    SIMULATOR_TIME_SCALE = float(os.environ.get("SIMULATOR_TIME_SCALE", "12"))

    # Alert thresholds
    ALERT_LOW_SOC_PCT = 20.0
    ALERT_CRITICAL_SOC_PCT = 10.0
    ALERT_OVERHEAT_C = 45.0
    ALERT_CRITICAL_OVERHEAT_C = 55.0

    # Battery health model (see ml/MODEL_CARD.md)
    BATTERY_MODEL_PATH = BASE_DIR / "models" / "battery_soh.joblib"

    # Route planning: OpenStreetMap services (override to use self-hosted instances)
    NOMINATIM_URL = os.environ.get("NOMINATIM_URL", "https://nominatim.openstreetmap.org")
    OSRM_URL = os.environ.get("OSRM_URL", "https://router.project-osrm.org")
    # Nominatim's usage policy requires an identifying User-Agent with contact details.
    ROUTING_USER_AGENT = os.environ.get(
        "ROUTING_USER_AGENT", "EVFleetMonitor/1.0 (Infosys Springboard project)"
    )
    ROUTING_TIMEOUT_SECONDS = 10
    # Built from India's official BEE list by scripts/build_charging_stations.py
    CHARGING_STATIONS_PATH = BASE_DIR / "data" / "charging_stations_india.csv"

    # Wait up to 15 s for a lock instead of failing when two writers overlap (SQLite only).
    SQLALCHEMY_ENGINE_OPTIONS = {"connect_args": {"timeout": 15}}

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
    SIMULATOR_ENABLED = False


class ProductionConfig(Config):
    DEBUG = False
    SESSION_COOKIE_SECURE = True
    REMEMBER_COOKIE_SECURE = True
    SQLALCHEMY_DATABASE_URI = Config.database_url(
        f"sqlite:///{BASE_DIR / 'instance' / 'ev_fleet.db'}"
    )
    # The SQLite `timeout` argument isn't valid for other drivers (e.g. PostgreSQL).
    SQLALCHEMY_ENGINE_OPTIONS = (
        Config.SQLALCHEMY_ENGINE_OPTIONS if SQLALCHEMY_DATABASE_URI.startswith("sqlite") else {}
    )


CONFIGS = {
    "development": DevelopmentConfig,
    "testing": TestingConfig,
    "production": ProductionConfig,
}
