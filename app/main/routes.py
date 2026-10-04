from flask import jsonify, redirect, render_template, url_for
from flask_login import current_user
from sqlalchemy import text

from app.extensions import db
from app.main import bp
from app.monitoring.routes import fleet_summary, visible_snapshots
from app.services.alerts import open_alerts_for


@bp.route("/")
def index():
    if not current_user.is_authenticated:
        return render_template("main/index.html")
    snapshots = visible_snapshots()
    return render_template(
        "main/dashboard.html",
        summary=fleet_summary(snapshots),
        vehicles=snapshots,
        alerts=open_alerts_for([s["id"] for s in snapshots], limit=6),
    )


@bp.route("/healthz")
def healthz():
    """Liveness/readiness probe for Docker and Render."""
    try:
        db.session.execute(text("SELECT 1"))
        return jsonify(status="ok", database="ok")
    except Exception:
        return jsonify(status="degraded", database="unreachable"), 503


@bp.route("/favicon.ico")
def favicon():
    """Browsers request /favicon.ico on their own; point them at the SVG icon."""
    return redirect(url_for("static", filename="favicon.svg"), code=301)
