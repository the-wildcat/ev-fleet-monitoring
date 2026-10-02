from flask import Blueprint

bp = Blueprint("alerts", __name__, url_prefix="/alerts")

from app.alerts import routes  # noqa: E402, F401  (registers routes on the blueprint)
