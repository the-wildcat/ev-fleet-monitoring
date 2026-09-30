from flask import Blueprint

bp = Blueprint("battery", __name__, url_prefix="/battery")

from app.battery import routes  # noqa: E402, F401  (registers routes on the blueprint)
