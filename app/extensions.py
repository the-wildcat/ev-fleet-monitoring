"""Flask extensions, created here and bound to the app in create_app().

Keeping them in one module avoids circular imports between blueprints and models.
"""

from flask_migrate import Migrate
from flask_sqlalchemy import SQLAlchemy
from flask_wtf import CSRFProtect

db = SQLAlchemy()
migrate = Migrate()
csrf = CSRFProtect()
