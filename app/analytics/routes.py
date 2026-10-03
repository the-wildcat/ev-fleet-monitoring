"""Energy consumption and operating-cost analysis page."""

from datetime import timedelta

from flask import render_template, request
from flask_login import login_required

from app.analytics import bp
from app.extensions import db
from app.services.energy import analyse, local_today, refresh_today_if_stale
from app.vehicles.access import visible_vehicles_stmt

PERIODS = {7: "Last 7 days", 30: "Last 30 days", 90: "Last 90 days"}


@bp.route("/")
@login_required
def energy():
    days = request.args.get("days", 30, type=int)
    days = days if days in PERIODS else 30
    vehicle_id = request.args.get("vehicle_id", 0, type=int)

    refresh_today_if_stale()
    vehicles = db.session.execute(visible_vehicles_stmt()).scalars().all()
    # Only the user's own vehicles can be selected; anything else means "all".
    match = next((v for v in vehicles if v.id == vehicle_id), None)
    last = local_today()
    report = analyse([match] if match else vehicles, last - timedelta(days=days - 1), last)
    return render_template(
        "analytics/energy.html",
        report=report,
        vehicles=vehicles,
        vehicle_id=match.id if match else 0,
        selected_plate=match.plate_number if match else None,
        days=days,
        periods=PERIODS,
    )
