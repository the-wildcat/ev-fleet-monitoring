"""Outgoing email.

Sends through SMTP when MAIL_SERVER is configured. Otherwise the message is written to the
application log, so sign-up and password reset work out of the box during development.
In tests every message is also appended to `app.extensions["mail_outbox"]` for assertions.
"""

import smtplib
from email.message import EmailMessage

from flask import current_app, render_template


def send_email(to: str, subject: str, template: str, **context) -> None:
    """Render `emails/<template>.txt` and send it to `to`."""
    body = render_template(f"emails/{template}.txt", **context)
    cfg = current_app.config

    msg = EmailMessage()
    msg["Subject"] = subject
    msg["From"] = cfg["MAIL_DEFAULT_SENDER"]
    msg["To"] = to
    msg.set_content(body)

    if current_app.testing:
        current_app.extensions.setdefault("mail_outbox", []).append(msg)

    if not cfg["MAIL_SERVER"]:
        current_app.logger.info("Email (console mode) to %s: %s\n%s", to, subject, body)
        return

    try:
        with smtplib.SMTP(cfg["MAIL_SERVER"], cfg["MAIL_PORT"], timeout=15) as smtp:
            if cfg["MAIL_USE_TLS"]:
                smtp.starttls()
            if cfg["MAIL_USERNAME"]:
                smtp.login(cfg["MAIL_USERNAME"], cfg["MAIL_PASSWORD"])
            smtp.send_message(msg)
        current_app.logger.info("Email sent to %s: %s", to, subject)
    except (smtplib.SMTPException, OSError):
        # Don't break the user's request because the mail server is down; log it instead.
        current_app.logger.exception("Failed to send email to %s: %s", to, subject)
