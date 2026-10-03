"""User management for administrators: list users, change roles, (de)activate accounts."""

from flask import abort, current_app, flash, redirect, render_template, request, url_for
from flask_login import current_user
from flask_wtf import FlaskForm
from wtforms import SelectField

from app.admin import bp
from app.auth.decorators import role_required
from app.extensions import db
from app.models import Role, User
from app.services.settings import SETTINGS, all_settings, reset_setting, set_setting


class RoleForm(FlaskForm):
    role = SelectField("Role", choices=[(r.value, r.label) for r in Role])


class ActionForm(FlaskForm):
    """Empty form: only carries the CSRF token for POST buttons."""


def _get_other_user(user_id: int) -> User:
    user = db.session.get(User, user_id) or abort(404)
    if user.id == current_user.id:
        # Prevents an admin from demoting or deactivating themselves and getting locked out.
        flash("You can't change your own role or status.", "warning")
        abort(redirect(url_for("admin.users")))
    return user


@bp.route("/settings", methods=["GET", "POST"])
@role_required(Role.ADMIN)
def settings():
    """Edit system settings (tariff, speed limit, ...); blank or 'reset' restores the default."""
    errors: dict[str, str] = {}
    if request.method == "POST" and ActionForm().validate_on_submit():
        if "reset" in request.form:
            for key in SETTINGS:
                reset_setting(key)
            db.session.commit()
            flash("All settings restored to their defaults.", "success")
            return redirect(url_for("admin.settings"))
        changes = {}
        for key, definition in SETTINGS.items():
            raw = request.form.get(key, "").strip()
            if not raw:
                errors[key] = f"{definition.label} is required."
                continue
            try:
                value = float(raw)
            except ValueError:
                errors[key] = "Enter a number."
                continue
            try:
                set_setting(key, value, current_user.id)  # validates the allowed range
                changes[key] = value
            except ValueError as err:
                errors[key] = str(err)
        if errors:
            db.session.rollback()
        else:
            db.session.commit()
            current_app.logger.info("%s updated settings: %s", current_user.email, changes)
            flash("Settings saved.", "success")
            return redirect(url_for("admin.settings"))

    values = all_settings()
    defaults = {key: current_app.config[d.config_key] for key, d in SETTINGS.items()}
    return render_template(
        "admin/settings.html",
        definitions=SETTINGS,
        values={k: request.form.get(k, values[k]) for k in SETTINGS} if errors else values,
        defaults=defaults,
        errors=errors,
        form=ActionForm(),
    )


@bp.route("/users")
@role_required(Role.ADMIN)
def users():
    all_users = db.session.execute(db.select(User).order_by(User.created_at.desc())).scalars().all()
    return render_template(
        "admin/users.html",
        users=all_users,
        role_form=RoleForm(),
        action_form=ActionForm(),
        roles=list(Role),
    )


@bp.route("/users/<int:user_id>/role", methods=["POST"])
@role_required(Role.ADMIN)
def change_role(user_id: int):
    user = _get_other_user(user_id)
    form = RoleForm()
    if form.validate_on_submit():
        user.role = Role(form.role.data)
        db.session.commit()
        current_app.logger.info(
            "%s set role of %s to %s", current_user.email, user.email, user.role
        )
        flash(f"{user.name} is now a {user.role.label}.", "success")
    return redirect(url_for("admin.users"))


@bp.route("/users/<int:user_id>/toggle-active", methods=["POST"])
@role_required(Role.ADMIN)
def toggle_active(user_id: int):
    user = _get_other_user(user_id)
    if ActionForm().validate_on_submit():
        user.active = not user.active
        db.session.commit()
        state = "activated" if user.active else "deactivated"
        current_app.logger.info("%s %s %s", current_user.email, state, user.email)
        flash(f"{user.name} has been {state}.", "success")
    return redirect(url_for("admin.users"))
