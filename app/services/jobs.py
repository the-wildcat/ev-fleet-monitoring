"""Periodic background jobs inside the web process: maintenance rules and alert emails.

A daemon thread wakes every minute, runs the maintenance rules every
MAINTENANCE_CHECK_MINUTES and sends any pending critical-alert emails. For larger
deployments the same functions can be run by an external scheduler instead
(`flask check-maintenance`, `flask send-alert-emails`) with BACKGROUND_JOBS_ENABLED=false.
"""

from __future__ import annotations

import threading
import time

from flask import Flask

from app.extensions import db

TICK_SECONDS = 60


def run_once(app: Flask, run_maintenance: bool) -> None:
    from app.services.maintenance import run_maintenance_checks
    from app.services.notifications import send_pending_alert_emails

    # A request context gives url_for() the site address for links in emails.
    with app.test_request_context(base_url=app.config["APP_BASE_URL"]):
        if run_maintenance:
            checked = run_maintenance_checks()
            app.logger.info("Maintenance rules checked for %d vehicles", checked)
        sent = send_pending_alert_emails()
        if sent:
            app.logger.info("Sent %d critical-alert emails", sent)


def _loop(app: Flask, stop: threading.Event) -> None:
    every = app.config["MAINTENANCE_CHECK_MINUTES"] * 60
    last_maintenance = 0.0
    while not stop.is_set():
        due = time.monotonic() - last_maintenance >= every
        try:
            run_once(app, run_maintenance=due)
            if due:
                last_maintenance = time.monotonic()
        except Exception:
            app.logger.exception("Background job failed")
            with app.app_context():
                db.session.rollback()
        stop.wait(TICK_SECONDS)


_lock = threading.Lock()


def start_background_jobs(app: Flask) -> bool:
    """Start the jobs thread once per process. Returns True if started."""
    with _lock:
        if app.extensions.get("jobs_thread"):
            return False
        stop = threading.Event()
        thread = threading.Thread(
            target=_loop, args=(app, stop), name="background-jobs", daemon=True
        )
        app.extensions["jobs_thread"] = thread
        thread.start()
        return True
