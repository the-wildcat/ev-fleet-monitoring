"""Flask extensions, created here and bound to the app in create_app().

Keeping them in one module avoids circular imports between blueprints and models.
"""

import sqlite3

from flask_login import LoginManager
from flask_migrate import Migrate
from flask_sqlalchemy import SQLAlchemy
from flask_wtf import CSRFProtect
from sqlalchemy import event
from sqlalchemy.engine import Engine


@event.listens_for(Engine, "connect")
def _sqlite_pragmas(dbapi_connection, _record) -> None:
    """SQLite defaults that other databases already have.

    - foreign_keys: enforce FKs, so deleting a vehicle cascades to its telemetry.
    - WAL journal: readers (web pages) don't block the writer (telemetry simulator/API).
    """
    if isinstance(dbapi_connection, sqlite3.Connection):
        cursor = dbapi_connection.cursor()
        cursor.execute("PRAGMA foreign_keys=ON")
        cursor.execute("PRAGMA journal_mode=WAL")
        cursor.close()


db = SQLAlchemy()
migrate = Migrate()
csrf = CSRFProtect()

login_manager = LoginManager()
login_manager.login_view = "auth.login"
login_manager.login_message = "Please log in to continue."
login_manager.login_message_category = "info"
# Invalidate the session if the user's IP/user-agent fingerprint changes.
login_manager.session_protection = "strong"
