"""Flask extensions, created here and bound to the app in create_app().

Keeping them in one module avoids circular imports between blueprints and models.
"""

from flask_login import LoginManager
from flask_migrate import Migrate
from flask_sqlalchemy import SQLAlchemy
from flask_wtf import CSRFProtect

db = SQLAlchemy()
migrate = Migrate()
csrf = CSRFProtect()

login_manager = LoginManager()
login_manager.login_view = "auth.login"
login_manager.login_message = "Please log in to continue."
login_manager.login_message_category = "info"
# Invalidate the session if the user's IP/user-agent fingerprint changes.
login_manager.session_protection = "strong"
