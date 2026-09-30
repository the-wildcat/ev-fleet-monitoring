"""Versioned JSON API (/api/v1) for devices and integrations.

Devices authenticate with a per-vehicle key in the `X-API-Key` header, not with cookies,
so CSRF protection (which defends cookie-based sessions) is disabled for this blueprint.
"""

from flask import Blueprint

from app.extensions import csrf

bp = Blueprint("api", __name__, url_prefix="/api/v1")
csrf.exempt(bp)

from app.api import battery, telemetry  # noqa: E402, F401  (registers routes on the blueprint)
