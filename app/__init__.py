"""EV Fleet Monitoring application package.

`create_app()` is the application factory: it builds a configured Flask app on demand,
which lets tests, the CLI and the WSGI server each create their own instance.
"""

import logging
import os
from datetime import UTC, datetime
from zoneinfo import ZoneInfo

from flask import Flask

from app.config import CONFIGS
from app.extensions import csrf, db, login_manager, migrate

_PLACEHOLDER_KEYS = {"change-me", "dev-only-insecure-key"}


def create_app(config_name: str | None = None) -> Flask:
    config_name = config_name or os.environ.get("APP_ENV", "development")
    config_class = CONFIGS.get(config_name)
    if config_class is None:
        raise ValueError(f"Unknown APP_ENV {config_name!r}; expected one of {sorted(CONFIGS)}")

    app = Flask(__name__, instance_relative_config=True)
    app.config.from_object(config_class)
    if not app.config.get("SECRET_KEY"):
        raise RuntimeError("SECRET_KEY must be set (see .env.example)")
    if config_name == "production" and app.config["SECRET_KEY"] in _PLACEHOLDER_KEYS:
        raise RuntimeError("SECRET_KEY is still a placeholder; set a real secret in production")

    os.makedirs(app.instance_path, exist_ok=True)
    _configure_logging(app)

    db.init_app(app)
    # Batch mode lets Alembic alter tables on SQLite, which can't ALTER most columns in place.
    migrate.init_app(app, db, render_as_batch=True)
    login_manager.init_app(app)
    csrf.init_app(app)

    from app import models  # noqa: F401  (register tables and the user loader)
    from app.admin import bp as admin_bp
    from app.alerts import bp as alerts_bp
    from app.api import bp as api_bp
    from app.auth import bp as auth_bp
    from app.battery import bp as battery_bp
    from app.cli import register_cli
    from app.drivers import bp as drivers_bp
    from app.errors import register_error_handlers
    from app.main import bp as main_bp
    from app.monitoring import bp as monitoring_bp
    from app.routing import bp as routing_bp
    from app.services.battery_model import BatteryModel
    from app.vehicles import bp as vehicles_bp

    for blueprint in (
        main_bp,
        auth_bp,
        admin_bp,
        vehicles_bp,
        monitoring_bp,
        routing_bp,
        battery_bp,
        drivers_bp,
        alerts_bp,
        api_bp,
    ):
        app.register_blueprint(blueprint)
    # Loaded lazily on the first prediction, then shared by all requests.
    app.extensions["battery_model"] = BatteryModel(app.config["BATTERY_MODEL_PATH"])
    register_error_handlers(app)
    register_cli(app)
    _register_template_filters(app)
    _start_background_threads_on_first_request(app)

    app.logger.info("App started (env=%s)", config_name)
    return app


def _register_template_filters(app: Flask) -> None:
    tz = ZoneInfo(app.config["APP_TIMEZONE"])

    @app.template_filter("localtime")
    def localtime(value: datetime | None, fmt: str = "%d %b %Y, %H:%M") -> str:
        """Render a stored naive-UTC datetime in the display timezone (IST by default)."""
        if value is None:
            return "—"
        return value.replace(tzinfo=UTC).astimezone(tz).strftime(fmt)


def _start_background_threads_on_first_request(app: Flask) -> None:
    """Start the telemetry simulator and background jobs with the first web request.

    Starting them here (rather than in create_app) means CLI commands such as
    `flask db upgrade` never spawn them, and with the debug reloader only the serving
    process runs them.
    """
    if app.testing:
        return
    simulator, jobs = app.config["SIMULATOR_ENABLED"], app.config["BACKGROUND_JOBS_ENABLED"]
    if not (simulator or jobs):
        return

    @app.before_request
    def _ensure_background_threads() -> None:
        if simulator:
            from app.services.simulator import start_background_simulator

            start_background_simulator(app)
        if jobs:
            from app.services.jobs import start_background_jobs

            start_background_jobs(app)


def _configure_logging(app: Flask) -> None:
    level = app.config.get("LOG_LEVEL", "INFO")
    logging.basicConfig(
        level=level,
        format="%(asctime)s %(levelname)s [%(name)s] %(message)s",
    )
    app.logger.setLevel(level)
