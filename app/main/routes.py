from flask import jsonify, render_template
from flask_login import current_user
from sqlalchemy import text

from app.extensions import db
from app.main import bp
from app.monitoring.routes import fleet_summary, visible_snapshots


@bp.route("/")
def index():
    if not current_user.is_authenticated:
        return render_template("main/index.html")
    snapshots = visible_snapshots()
    return render_template(
        "main/dashboard.html", summary=fleet_summary(snapshots), vehicles=snapshots
    )


@bp.route("/healthz")
def healthz():
    """Liveness/readiness probe for Docker and Render."""
    try:
        db.session.execute(text("SELECT 1"))
        return jsonify(status="ok", database="ok")
    except Exception:
        return jsonify(status="degraded", database="unreachable"), 503
