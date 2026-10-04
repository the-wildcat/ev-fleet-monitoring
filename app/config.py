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

    # Public address of the site, used for links in emails sent outside a web request
    APP_BASE_URL = (
        os.environ.get("APP_BASE_URL")
        or os.environ.get("RENDER_EXTERNAL_URL")  # set automatically on Render
        or "http://127.0.0.1:5000"
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
    # Each real interval simulates this many times as much driving. 3 = near real time (a 5 s
    # tick moves a vehicle ~100-200 m along the road); 12 = quick demos (batteries drain fast).
    SIMULATOR_TIME_SCALE = float(os.environ.get("SIMULATOR_TIME_SCALE", "3"))

    # Alert thresholds
    ALERT_LOW_SOC_PCT = 20.0
    ALERT_CRITICAL_SOC_PCT = 10.0
    ALERT_OVERHEAT_C = 45.0
    ALERT_CRITICAL_OVERHEAT_C = 55.0

    # Driver behaviour: event thresholds (common telematics values) and scoring
    HARSH_BRAKE_MPS2 = -3.5  # ~0.35 g deceleration
    HARSH_ACCEL_MPS2 = 3.0  # ~0.3 g
    SPEED_LIMIT_KMH = float(os.environ.get("SPEED_LIMIT_KMH", "80"))  # fleet speed policy
    # Score = 100 - sum(weight x events per 100 km), floored at 0.
    SCORE_WEIGHTS = {"harsh_brake": 4.0, "harsh_accel": 3.0, "speeding": 2.0}
    MIN_SCORING_DISTANCE_KM = 5.0  # less driving than this isn't enough to score
    ENERGY_TARIFF_INR_PER_KWH = float(os.environ.get("ENERGY_TARIFF_INR_PER_KWH", "10"))

    # Energy & cost analysis (defaults; admins can change them on the Settings page)
    CHARGING_EFFICIENCY_PCT = float(os.environ.get("CHARGING_EFFICIENCY_PCT", "90"))
    PETROL_PRICE_INR_PER_L = float(os.environ.get("PETROL_PRICE_INR_PER_L", "105"))
    ICE_KM_PER_L = float(os.environ.get("ICE_KM_PER_L", "15"))
    # Emission factors (kg CO2): Indian grid average per kWh (Central Electricity Authority,
    # CO2 Baseline Database, ~0.72 t/MWh) and petrol combustion per litre (~2.31 kg).
    GRID_EMISSION_KG_PER_KWH = 0.72
    PETROL_EMISSION_KG_PER_L = 2.31
    ENERGY_ROLLUP_MINUTES = 60  # how often the background job refreshes daily energy rows

    # Background jobs: maintenance rules and alert emails (run inside the web process)
    BACKGROUND_JOBS_ENABLED = os.environ.get("BACKGROUND_JOBS_ENABLED", "true").lower() == "true"

    # Maintenance rules (checked in the background every few minutes)
    SERVICE_INTERVAL_KM = 10_000
    SERVICE_INTERVAL_DAYS = 365
    SERVICE_OVERDUE_MARGIN_KM = 1_000  # critical once this far past the interval
    BATTERY_CHECK_INTERVAL_DAYS = 180
    BRAKE_EVENTS_PER_WEEK = 20  # harsh-braking events in 7 days that trigger an inspection
    DEVICE_OFFLINE_HOURS = 24
    MAINTENANCE_CHECK_MINUTES = 10

    # Email managers about new critical alerts (needs MAIL_* settings)
    ALERT_EMAILS_ENABLED = os.environ.get("ALERT_EMAILS_ENABLED", "false").lower() == "true"
    ALERT_EMAIL_COOLDOWN_HOURS = 6  # per vehicle and alert type

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

    @staticmethod
    def database_url(default: str, env_var: str = "DATABASE_URL") -> str:
        url = os.environ.get(env_var) or default
        # Hosting providers hand out "postgres://" or "postgresql://" URLs. SQLAlchemy 2 rejects
        # the first and maps the second to psycopg2, so point both at psycopg 3.
        for prefix in ("postgres://", "postgresql://"):
            if url.startswith(prefix):
                return "postgresql+psycopg://" + url.removeprefix(prefix)
        return url

    @staticmethod
    def engine_options(url: str) -> dict:
        if url.startswith("sqlite"):
            # Wait up to 15 s for a lock instead of failing when two writers overlap.
            return {"connect_args": {"timeout": 15}}
        # Drop pooled connections the server has closed (idle timeouts, restarts).
        return {"pool_pre_ping": True}


class DevelopmentConfig(Config):
    DEBUG = True
    SECRET_KEY = Config.SECRET_KEY or "dev-only-insecure-key"
    SQLALCHEMY_DATABASE_URI = Config.database_url(
        f"sqlite:///{BASE_DIR / 'instance' / 'ev_fleet.db'}"
    )
    SQLALCHEMY_ENGINE_OPTIONS = Config.engine_options(SQLALCHEMY_DATABASE_URI)


class TestingConfig(Config):
    TESTING = True
    SECRET_KEY = "test-key"
    # In-memory SQLite by default; CI also runs the suite against PostgreSQL.
    SQLALCHEMY_DATABASE_URI = Config.database_url("sqlite:///:memory:", "TEST_DATABASE_URL")
    SQLALCHEMY_ENGINE_OPTIONS = Config.engine_options(SQLALCHEMY_DATABASE_URI)
    WTF_CSRF_ENABLED = False
    MAIL_SERVER = ""  # never send real email from tests
    SIMULATOR_ENABLED = False
    BACKGROUND_JOBS_ENABLED = False


class ProductionConfig(Config):
    DEBUG = False
    # Cookies only over HTTPS. Set SESSION_COOKIE_SECURE=false to try the production image
    # over plain http (e.g. docker compose on localhost).
    SESSION_COOKIE_SECURE = os.environ.get("SESSION_COOKIE_SECURE", "true").lower() == "true"
    REMEMBER_COOKIE_SECURE = SESSION_COOKIE_SECURE
    SQLALCHEMY_DATABASE_URI = Config.database_url(
        f"sqlite:///{BASE_DIR / 'instance' / 'ev_fleet.db'}"
    )
    SQLALCHEMY_ENGINE_OPTIONS = Config.engine_options(SQLALCHEMY_DATABASE_URI)
    # Behind Render's (or any) reverse proxy, trust one hop of X-Forwarded-* headers so
    # generated links use https and the real client address is logged.
    PROXY_FIX = True


CONFIGS = {
    "development": DevelopmentConfig,
    "testing": TestingConfig,
    "production": ProductionConfig,
}
