from flask import Blueprint

bp = Blueprint("routing", __name__, url_prefix="/routes")

from app.routing import routes  # noqa: E402, F401  (registers routes on the blueprint)
