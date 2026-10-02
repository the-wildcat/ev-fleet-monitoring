"""Email fleet managers about new critical alerts.

Runs from the background jobs (not inside the request that raised the alert), so the
telemetry API stays fast and a slow mail server can't block it. To avoid flooding inboxes,
at most one email is sent per vehicle and alert type within ALERT_EMAIL_COOLDOWN_HOURS;
alerts suppressed by the cool-down are marked as handled without an email.
"""

from __future__ import annotations

from datetime import timedelta

from flask import current_app, url_for

from app.extensions import db
from app.models import Alert, AlertSeverity, Role, User
from app.services.email import send_email
from app.utils import utcnow


def _recipients() -> list[User]:
    return list(
        db.session.execute(
            db.select(User).where(
                User.role.in_((Role.ADMIN, Role.FLEET_MANAGER)),
                User.active.is_(True),
                User.is_verified.is_(True),
            )
        ).scalars()
    )


def send_pending_alert_emails() -> int:
    """Email managers about open critical alerts not yet notified. Returns emails sent."""
    cfg = current_app.config
    if not cfg["ALERT_EMAILS_ENABLED"]:
        return 0
    pending = (
        db.session.execute(
            db.select(Alert)
            .where(
                Alert.severity == AlertSeverity.CRITICAL,
                Alert.resolved_at.is_(None),
                Alert.notified_at.is_(None),
            )
            .order_by(Alert.created_at)
        )
        .scalars()
        .all()
    )
    if not pending:
        return 0

    recipients = _recipients()
    cooldown_start = utcnow() - timedelta(hours=cfg["ALERT_EMAIL_COOLDOWN_HOURS"])
    sent = 0
    for alert in pending:
        recently_notified = db.session.execute(
            db.select(Alert.id).where(
                Alert.vehicle_id == alert.vehicle_id,
                Alert.alert_type == alert.alert_type,
                Alert.notified_at >= cooldown_start,
                Alert.id != alert.id,
            )
        ).first()
        if not recently_notified:
            link = url_for("alerts.inbox", _external=True)
            for user in recipients:
                send_email(
                    user.email,
                    f"[Critical] {alert.alert_type.label}: {alert.vehicle.plate_number}",
                    "alert_critical",
                    user=user,
                    alert=alert,
                    link=link,
                )
                sent += 1
        alert.notified_at = utcnow()
    db.session.commit()
    return sent
