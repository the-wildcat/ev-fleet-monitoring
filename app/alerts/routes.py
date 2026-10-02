"""Alerts inbox: filter, acknowledge and resolve alerts; history of resolved alerts."""

from flask import abort, current_app, flash, redirect, render_template, request, url_for
from flask_login import current_user, login_required
from flask_wtf import FlaskForm
from sqlalchemy import func
from wtforms import TextAreaField
from wtforms.validators import Length, Optional

from app.alerts import bp
from app.auth.decorators import role_required
from app.extensions import db
from app.models import Alert, AlertSeverity, AlertType, Role, Vehicle
from app.services.alerts import CRITICAL_FIRST
from app.utils import is_safe_redirect, utcnow
from app.vehicles.access import visible_vehicles_stmt

PAGE_SIZE = 50
MANAGERS = (Role.ADMIN, Role.FLEET_MANAGER)


class ResolveForm(FlaskForm):
    note = TextAreaField("Resolution note", validators=[Optional(), Length(max=500)])


class AckForm(FlaskForm):
    """CSRF token only."""


def _visible_vehicle_ids() -> list[int]:
    stmt = visible_vehicles_stmt().with_only_columns(Vehicle.id)
    return list(db.session.execute(stmt).scalars())


def _get_visible_alert(alert_id: int) -> Alert:
    alert = db.session.get(Alert, alert_id)
    if alert is None or alert.vehicle_id not in _visible_vehicle_ids():
        abort(404)
    return alert


@bp.app_context_processor
def open_alert_count() -> dict:
    """Number of open alerts on the user's vehicles, for the sidebar badge."""
    if not current_user.is_authenticated:
        return {}

    def count() -> int:
        ids = _visible_vehicle_ids()
        if not ids:
            return 0
        return db.session.execute(
            db.select(func.count(Alert.id)).where(
                Alert.vehicle_id.in_(ids), Alert.resolved_at.is_(None)
            )
        ).scalar_one()

    return {"open_alert_count": count}


@bp.route("/")
@login_required
def inbox():
    status = request.args.get("status", "open")
    alert_type = request.args.get("type", "")
    severity = request.args.get("severity", "")
    vehicle_id = request.args.get("vehicle_id", 0, type=int)
    page = max(request.args.get("page", 1, type=int), 1)

    ids = _visible_vehicle_ids()
    stmt = db.select(Alert).where(Alert.vehicle_id.in_(ids))
    if status == "resolved":
        stmt = stmt.where(Alert.resolved_at.is_not(None)).order_by(Alert.resolved_at.desc())
    else:
        status = "open"
        stmt = stmt.where(Alert.resolved_at.is_(None)).order_by(
            Alert.acknowledged_at.is_not(None), CRITICAL_FIRST, Alert.created_at.desc()
        )
    if alert_type in AlertType._value2member_map_:
        stmt = stmt.where(Alert.alert_type == AlertType(alert_type))
    if severity in AlertSeverity._value2member_map_:
        stmt = stmt.where(Alert.severity == AlertSeverity(severity))
    if vehicle_id in ids:
        stmt = stmt.where(Alert.vehicle_id == vehicle_id)

    total = db.session.execute(
        db.select(func.count()).select_from(stmt.order_by(None).subquery())
    ).scalar_one()
    alerts = (
        db.session.execute(stmt.limit(PAGE_SIZE).offset((page - 1) * PAGE_SIZE)).scalars().all()
    )
    vehicles = db.session.execute(visible_vehicles_stmt()).scalars().all()
    return render_template(
        "alerts/inbox.html",
        alerts=alerts,
        total=total,
        page=page,
        pages=max(1, -(-total // PAGE_SIZE)),
        status=status,
        filters={"type": alert_type, "severity": severity, "vehicle_id": vehicle_id},
        alert_types=list(AlertType),
        vehicles=vehicles,
        ack_form=AckForm(),
        resolve_form=ResolveForm(),
    )


def _back():
    target = request.form.get("next")
    return redirect(target if is_safe_redirect(target) else url_for("alerts.inbox"))


@bp.route("/<int:alert_id>/acknowledge", methods=["POST"])
@role_required(*MANAGERS)
def acknowledge(alert_id: int):
    alert = _get_visible_alert(alert_id)
    if AckForm().validate_on_submit() and alert.is_open and alert.acknowledged_at is None:
        alert.acknowledged_at = utcnow()
        alert.acknowledged_by_id = current_user.id
        db.session.commit()
        flash(f"Acknowledged: {alert.alert_type.label} on {alert.vehicle.plate_number}.", "success")
    return _back()


@bp.route("/<int:alert_id>/resolve", methods=["POST"])
@role_required(*MANAGERS)
def resolve(alert_id: int):
    alert = _get_visible_alert(alert_id)
    form = ResolveForm()
    if form.validate_on_submit() and alert.is_open:
        alert.resolved_at = utcnow()
        alert.resolved_by_id = current_user.id
        alert.resolution_note = (form.note.data or "").strip() or None
        db.session.commit()
        current_app.logger.info("%s resolved alert %s", current_user.email, alert.id)
        flash(
            f"Resolved: {alert.alert_type.label} on {alert.vehicle.plate_number}. "
            "It will reopen automatically if the problem is still detected.",
            "success",
        )
    return _back()
