"""Outgoing email.

Sends through SMTP when MAIL_SERVER is configured. Otherwise the message is written to the
application log, so sign-up and password reset work out of the box during development.
In tests every message is also appended to `app.extensions["mail_outbox"]` for assertions.
"""

import smtplib
from email.message import EmailMessage

from flask import current_app, render_template


def send_email(to: str, subject: str, template: str, raise_errors: bool = False, **context) -> bool:
    """Render `emails/<template>.txt` and send it to `to`. Returns True if it was delivered.

    Delivery failures are logged rather than raised, so a mail outage doesn't break sign-up;
    pass `raise_errors=True` to surface them (used by `flask send-test-email`).
    """
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
        return True

    try:
        with smtplib.SMTP(cfg["MAIL_SERVER"], cfg["MAIL_PORT"], timeout=15) as smtp:
            if cfg["MAIL_USE_TLS"]:
                smtp.starttls()
            if cfg["MAIL_USERNAME"]:
                smtp.login(cfg["MAIL_USERNAME"], cfg["MAIL_PASSWORD"])
            smtp.send_message(msg)
    except (smtplib.SMTPException, OSError):
        if raise_errors:
            raise
        current_app.logger.exception("Failed to send email to %s: %s", to, subject)
        if current_app.debug:
            # Development only: show the message so the developer isn't stuck without the link.
            current_app.logger.warning("Undelivered email body (debug mode):\n%s", body)
        return False

    current_app.logger.info("Email sent to %s: %s", to, subject)
    return True
