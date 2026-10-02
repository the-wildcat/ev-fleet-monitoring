from flask import Blueprint

bp = Blueprint("drivers", __name__, url_prefix="/drivers")

from app.drivers import routes  # noqa: E402, F401  (registers routes on the blueprint)
