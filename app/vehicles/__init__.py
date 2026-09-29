from flask import Blueprint

bp = Blueprint("vehicles", __name__, url_prefix="/vehicles")

from app.vehicles import routes  # noqa: E402, F401  (registers routes on the blueprint)
