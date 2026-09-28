import smtplib
from unittest.mock import MagicMock

import pytest

from app.models import User
from app.services.email import send_email


@pytest.fixture
def smtp(app, monkeypatch):
    app.config.update(MAIL_SERVER="smtp.example.com", MAIL_USERNAME="bot", MAIL_PASSWORD="pw")
    server = MagicMock()
    smtp_cls = MagicMock(return_value=server)
    server.__enter__.return_value = server
    monkeypatch.setattr(smtplib, "SMTP", smtp_cls)
    return smtp_cls, server


def _send(app):
    with app.test_request_context():
        send_email(
            "to@example.com", "Hello", "verify_email", user=User(name="Asha"), link="http://x"
        )


def test_smtp_send_uses_tls_and_login(app, smtp):
    smtp_cls, server = smtp
    _send(app)
    smtp_cls.assert_called_once_with("smtp.example.com", 587, timeout=15)
    server.starttls.assert_called_once()
    server.login.assert_called_once_with("bot", "pw")
    sent = server.send_message.call_args.args[0]
    assert sent["To"] == "to@example.com" and "Hi Asha" in sent.get_content()


def test_smtp_failure_is_logged_not_raised(app, smtp, caplog):
    _, server = smtp
    server.send_message.side_effect = smtplib.SMTPException("boom")
    _send(app)  # must not raise
    assert "Failed to send email" in caplog.text


def test_failed_send_shows_body_in_debug_mode(app, smtp, caplog):
    _, server = smtp
    server.send_message.side_effect = smtplib.SMTPException("boom")
    app.debug = True
    _send(app)
    assert "Undelivered email body" in caplog.text and "Hi Asha" in caplog.text


def test_send_test_email_cli_success(app, smtp):
    _, server = smtp
    result = app.test_cli_runner().invoke(args=["send-test-email", "me@example.com"])
    assert result.exit_code == 0, result.output
    assert "Test email sent to me@example.com" in result.output
    assert server.send_message.call_args.args[0]["To"] == "me@example.com"


def test_send_test_email_cli_reports_bad_credentials(app, smtp):
    _, server = smtp
    server.login.side_effect = smtplib.SMTPAuthenticationError(535, b"bad credentials")
    result = app.test_cli_runner().invoke(args=["send-test-email", "me@example.com"])
    assert result.exit_code != 0
    assert "App Password" in result.output


def test_send_test_email_cli_requires_mail_server(app):
    result = app.test_cli_runner().invoke(args=["send-test-email", "me@example.com"])
    assert result.exit_code != 0
    assert "MAIL_SERVER is empty" in result.output


def test_send_test_email_cli_requires_credentials(app):
    app.config.update(MAIL_SERVER="smtp.example.com", MAIL_USERNAME="", MAIL_PASSWORD="")
    result = app.test_cli_runner().invoke(args=["send-test-email", "me@example.com"])
    assert result.exit_code != 0
    assert "MAIL_USERNAME and MAIL_PASSWORD" in result.output


def test_sender_defaults_to_mail_username(monkeypatch):
    import importlib

    import app.config as config

    monkeypatch.setenv("MAIL_USERNAME", "fleet.bot@gmail.com")
    monkeypatch.delenv("MAIL_DEFAULT_SENDER", raising=False)
    reloaded = importlib.reload(config)
    try:
        assert reloaded.Config.MAIL_DEFAULT_SENDER == "EV Fleet Monitor <fleet.bot@gmail.com>"
    finally:
        monkeypatch.undo()
        importlib.reload(config)


def test_console_mode_logs_email(app, caplog):
    caplog.set_level("INFO")
    _send(app)
    assert "Email (console mode) to to@example.com" in caplog.text
