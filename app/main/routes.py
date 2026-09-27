from flask import jsonify, render_template
from sqlalchemy import text

from app.extensions import db
from app.main import bp


@bp.route("/")
def index():
    return render_template("main/index.html")


@bp.route("/healthz")
def healthz():
    """Liveness/readiness probe for Docker and Render."""
    try:
        db.session.execute(text("SELECT 1"))
        return jsonify(status="ok", database="ok")
    except Exception:
        return jsonify(status="degraded", database="unreachable"), 503
