"""Driver behaviour: fleet leaderboard (managers) and per-driver scorecards."""

from flask import abort, current_app, redirect, render_template, request, url_for
from flask_login import current_user, login_required

from app.drivers import bp
from app.extensions import db
from app.models import Role, User
from app.services.driving import (
    FAIR_SCORE,
    GOOD_SCORE,
    daily_trend,
    driver_stats,
    period_bounds,
    recent_events,
)
from app.services.settings import all_settings

PERIODS = {1: "Today", 7: "Last 7 days", 30: "Last 30 days"}


def _period() -> int:
    days = request.args.get("days", 7, type=int)
    return days if days in PERIODS else 7


def _fleet_summary(stats) -> dict:
    scored = [s for s in stats if s.score is not None]
    km = sum(s.distance_km for s in stats)
    events = sum(s.events for s in stats)
    return {
        "drivers": len(stats),
        "distance_km": km,
        "events_per_100km": events / km * 100 if km else None,
        "avg_score": sum(s.score for s in scored) / len(scored) if scored else None,
        "needs_coaching": sum(1 for s in scored if s.score < FAIR_SCORE),
    }


@bp.route("/")
@login_required
def leaderboard():
    if not current_user.is_manager:
        return redirect(url_for("drivers.scorecard", driver_id=current_user.id))
    days = _period()
    since, until = period_bounds(days)
    stats = driver_stats(since, until)
    # Drivers with no telemetry in the period still appear, so nobody is silently missing.
    seen = {s.driver_id for s in stats}
    idle = (
        db.session.execute(
            db.select(User)
            .where(User.role == Role.DRIVER, User.active.is_(True), User.id.not_in(seen))
            .order_by(User.name)
        )
        .scalars()
        .all()
    )
    scatter = [
        {"x": s.score, "y": round(s.energy_vs_rated_pct, 1), "name": s.name}
        for s in stats
        if s.score is not None and s.energy_vs_rated_pct is not None
    ]
    return render_template(
        "drivers/leaderboard.html",
        stats=stats,
        scatter=scatter,
        idle=idle,
        summary=_fleet_summary(stats),
        days=days,
        periods=PERIODS,
        good=GOOD_SCORE,
        fair=FAIR_SCORE,
        cfg=current_app.config,
        settings=all_settings(),
    )


@bp.route("/<int:driver_id>")
@login_required
def scorecard(driver_id: int):
    if not current_user.is_manager and driver_id != current_user.id:
        abort(404)
    driver = db.session.get(User, driver_id) or abort(404)
    days = _period()
    since, until = period_bounds(days)
    stats = driver_stats(since, until, [driver.id])
    return render_template(
        "drivers/scorecard.html",
        driver=driver,
        st=stats[0] if stats else None,
        trend=daily_trend(driver.id, *period_bounds(max(days, 7))),
        events=recent_events(driver.id, since),
        days=days,
        periods=PERIODS,
        cfg=current_app.config,
        settings=all_settings(),
    )
