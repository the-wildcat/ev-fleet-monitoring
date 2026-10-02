from datetime import timedelta

import pytest

from app.extensions import db
from app.models import AlertSeverity, AlertType, Role
from app.services.alerts import raise_alert
from app.services.notifications import send_pending_alert_emails
from app.utils import utcnow


@pytest.fixture
def emails_on(app):
    app.config["ALERT_EMAILS_ENABLED"] = True
    return app


def _send(app):
    with app.test_request_context():
        return send_pending_alert_emails()


def _critical(vehicle, alert_type=AlertType.LOW_BATTERY):
    alert = raise_alert(vehicle, alert_type, AlertSeverity.CRITICAL, "Battery at 8%")
    db.session.commit()
    return alert


def test_disabled_by_default(app, make_user, make_vehicle, outbox):
    make_user(role=Role.FLEET_MANAGER, email="m@example.com")
    _critical(make_vehicle()[0])
    assert _send(app) == 0 and outbox == []


def test_critical_alert_emails_active_verified_managers_once(
    emails_on, make_user, make_vehicle, outbox
):
    make_user(role=Role.FLEET_MANAGER, email="manager@example.com")
    make_user(role=Role.ADMIN, email="admin@example.com")
    make_user(role=Role.DRIVER, email="driver@example.com")
    make_user(role=Role.FLEET_MANAGER, email="off@example.com", active=False)
    make_user(role=Role.FLEET_MANAGER, email="new@example.com", verified=False)
    vehicle, _ = make_vehicle()
    alert = _critical(vehicle)

    assert _send(emails_on) == 2
    assert sorted(m["To"] for m in outbox) == ["admin@example.com", "manager@example.com"]
    assert "[Critical] Low battery: WB12AD3456" in outbox[0]["Subject"]
    assert "Battery at 8%" in outbox[0].get_content()
    assert alert.notified_at is not None
    assert _send(emails_on) == 0  # not sent twice


def test_warnings_are_not_emailed(emails_on, make_user, make_vehicle, outbox):
    make_user(role=Role.FLEET_MANAGER, email="m@example.com")
    raise_alert(make_vehicle()[0], AlertType.SERVICE_DUE, AlertSeverity.WARNING, "due")
    db.session.commit()
    assert _send(emails_on) == 0


def test_cooldown_suppresses_repeat_emails(emails_on, make_user, make_vehicle, outbox):
    make_user(role=Role.FLEET_MANAGER, email="m@example.com")
    vehicle, _ = make_vehicle()
    first = _critical(vehicle)
    _send(emails_on)
    first.resolved_at = utcnow()  # problem clears...
    db.session.commit()
    second = _critical(vehicle)  # ...and comes back an hour later
    assert _send(emails_on) == 0  # within the 6 h cool-down: no new email
    assert second.notified_at is not None  # but handled, so it isn't retried forever
    assert len(outbox) == 1

    second.resolved_at = utcnow()
    first.notified_at = second.notified_at = utcnow() - timedelta(hours=7)
    db.session.commit()
    _critical(vehicle)
    assert _send(emails_on) == 1  # cool-down over


def test_escalation_from_warning_triggers_email(emails_on, make_user, make_vehicle, outbox):
    make_user(role=Role.FLEET_MANAGER, email="m@example.com")
    vehicle, _ = make_vehicle()
    raise_alert(vehicle, AlertType.LOW_BATTERY, AlertSeverity.WARNING, "Battery at 18%")
    db.session.commit()
    assert _send(emails_on) == 0
    _critical(vehicle)
    assert _send(emails_on) == 1


def test_cli_requires_enabling(app):
    result = app.test_cli_runner().invoke(args=["send-alert-emails"])
    assert result.exit_code != 0 and "ALERT_EMAILS_ENABLED" in result.output


def test_background_job_runs_maintenance_and_emails(emails_on, make_user, make_vehicle, outbox):
    from app.services.jobs import run_once

    make_user(role=Role.FLEET_MANAGER, email="m@example.com")
    make_vehicle(odometer_km=12_000)  # service overdue -> critical alert
    run_once(emails_on, run_maintenance=True)
    assert len(outbox) == 1 and "Service due" in outbox[0]["Subject"]
