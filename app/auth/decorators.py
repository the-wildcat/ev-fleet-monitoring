from collections.abc import Callable
from functools import wraps

from flask import abort
from flask_login import current_user, login_required

from app.models import Role


def role_required(*roles: Role) -> Callable:
    """Allow only logged-in users with one of `roles`; others get 403 Forbidden.

    Usage:
        @bp.route("/admin/users")
        @role_required(Role.ADMIN)
        def users(): ...
    """

    def decorator(view: Callable) -> Callable:
        @wraps(view)
        @login_required
        def wrapped(*args, **kwargs):
            if not current_user.has_role(*roles):
                abort(403)
            return view(*args, **kwargs)

        return wrapped

    return decorator
