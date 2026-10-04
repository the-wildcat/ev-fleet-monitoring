"""Gunicorn settings for the production container (Docker / Render)."""

import os

bind = f"0.0.0.0:{os.environ.get('PORT', '8000')}"

# One worker process with several threads. The telemetry simulator and the background jobs
# run as threads inside the web process, so more worker processes would start duplicate
# copies of them. To scale out, set SIMULATOR_ENABLED=false and BACKGROUND_JOBS_ENABLED=false,
# run `flask simulate` and a scheduler separately, then raise WEB_CONCURRENCY.
workers = int(os.environ.get("WEB_CONCURRENCY", "1"))
worker_class = "gthread"
threads = int(os.environ.get("GUNICORN_THREADS", "8"))

# Report generation and route planning (external routing API) can take a few seconds.
timeout = 60
graceful_timeout = 20
keepalive = 5

accesslog = "-"
errorlog = "-"
loglevel = os.environ.get("LOG_LEVEL", "info").lower()
# Behind a proxy (Render), log the client address passed in X-Forwarded-For.
forwarded_allow_ips = "*"
