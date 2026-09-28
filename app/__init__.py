"""EV Fleet Monitoring application package.

`create_app()` is the application factory: it builds a configured Flask app on demand,
which lets tests, the CLI and the WSGI server each create their own instance.
"""

import logging
import os

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
    from app.auth import bp as auth_bp
    from app.cli import register_cli
    from app.errors import register_error_handlers
    from app.main import bp as main_bp

    app.register_blueprint(main_bp)
    app.register_blueprint(auth_bp)
    app.register_blueprint(admin_bp)
    register_error_handlers(app)
    register_cli(app)

    app.logger.info("App started (env=%s)", config_name)
    return app


def _configure_logging(app: Flask) -> None:
    level = app.config.get("LOG_LEVEL", "INFO")
    logging.basicConfig(
        level=level,
        format="%(asctime)s %(levelname)s [%(name)s] %(message)s",
    )
    app.logger.setLevel(level)
