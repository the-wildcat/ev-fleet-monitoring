"""Registration, email verification, login/logout, password reset and profile."""

from urllib.parse import urlsplit

from flask import current_app, flash, redirect, render_template, request, url_for
from flask_login import current_user, login_required, login_user, logout_user
from werkzeug.security import check_password_hash, generate_password_hash

from app.auth import bp
from app.auth.forms import (
    ChangePasswordForm,
    EmailOnlyForm,
    LoginForm,
    RegisterForm,
    ResetPasswordForm,
)
from app.auth.tokens import (
    load_reset_token,
    load_verify_token,
    make_reset_token,
    make_verify_token,
)
from app.extensions import db
from app.models import Role, User
from app.services.email import send_email
from app.utils import utcnow

# Checked against when an email isn't registered, so a failed login takes the same time
# whether or not the account exists (prevents discovering accounts by timing).
_DUMMY_HASH = generate_password_hash("not-a-real-password")


def _find_user(email: str) -> User | None:
    return db.session.execute(db.select(User).filter_by(email=email)).scalar_one_or_none()


def _send_verification(user: User) -> None:
    link = url_for("auth.verify_email", token=make_verify_token(user), _external=True)
    send_email(
        user.email, "Verify your EV Fleet Monitor account", "verify_email", user=user, link=link
    )


def _is_safe_next(target: str | None) -> bool:
    """Only allow redirects to paths on this site (blocks open-redirect attacks)."""
    if not target:
        return False
    parts = urlsplit(target)
    return not parts.scheme and not parts.netloc and target.startswith("/")


# --- registration & verification ------------------------------------------------------------


@bp.route("/register", methods=["GET", "POST"])
def register():
    if current_user.is_authenticated:
        return redirect(url_for("main.index"))

    form = RegisterForm()
    if form.validate_on_submit():
        existing = _find_user(form.email.data)
        if existing:
            # Same on-screen response as a new sign-up, so the form can't be used to discover
            # which emails are registered. The real owner gets a heads-up email instead.
            send_email(
                existing.email,
                "Sign-up attempt on your EV Fleet Monitor account",
                "account_exists",
                user=existing,
                login_link=url_for("auth.login", _external=True),
                reset_link=url_for("auth.forgot_password", _external=True),
            )
        else:
            user = User(name=form.name.data, email=form.email.data, role=Role.DRIVER)
            user.set_password(form.password.data)
            db.session.add(user)
            db.session.commit()
            _send_verification(user)
            current_app.logger.info("New user registered: %s", user.email)
        return render_template("auth/check_email.html", email=form.email.data, purpose="verify")

    return render_template("auth/register.html", form=form)


@bp.route("/verify/<token>")
def verify_email(token: str):
    user = load_verify_token(token)
    if user is None:
        flash("That verification link is invalid or has expired. Request a new one below.", "error")
        return redirect(url_for("auth.resend_verification"))
    if not user.is_verified:
        user.is_verified = True
        db.session.commit()
        current_app.logger.info("Email verified: %s", user.email)
    flash("Your email is verified. You can now log in.", "success")
    return redirect(url_for("auth.login"))


@bp.route("/verify/resend", methods=["GET", "POST"])
def resend_verification():
    form = EmailOnlyForm()
    if form.validate_on_submit():
        user = _find_user(form.email.data)
        if user and not user.is_verified:
            _send_verification(user)
        return render_template("auth/check_email.html", email=form.email.data, purpose="verify")
    return render_template(
        "auth/email_form.html",
        form=form,
        title="Resend verification email",
        intro="Enter the email you signed up with and we'll send a new verification link.",
    )


# --- login / logout -------------------------------------------------------------------------


@bp.route("/login", methods=["GET", "POST"])
def login():
    if current_user.is_authenticated:
        return redirect(url_for("main.index"))

    form = LoginForm()
    if form.validate_on_submit():
        cfg = current_app.config
        user = _find_user(form.email.data)

        if user and user.is_locked():
            minutes = max(1, int((user.locked_until - utcnow()).total_seconds() // 60) + 1)
            flash(f"Too many failed attempts. Try again in about {minutes} minute(s).", "error")
            return render_template("auth/login.html", form=form), 429

        password_ok = (
            user.check_password(form.password.data)
            if user
            else check_password_hash(_DUMMY_HASH, form.password.data)
        )
        if not user or not password_ok:
            if user:
                user.register_failed_login(cfg["LOGIN_MAX_ATTEMPTS"], cfg["LOGIN_LOCKOUT_MINUTES"])
                db.session.commit()
            current_app.logger.warning("Failed login for %s", form.email.data)
            flash("Invalid email or password.", "error")
            return render_template("auth/login.html", form=form), 401

        if not user.is_verified:
            flash("Please verify your email before logging in.", "warning")
            return render_template("auth/login.html", form=form, show_resend=True), 403
        if not user.is_active:
            flash("This account has been deactivated. Contact your administrator.", "error")
            return render_template("auth/login.html", form=form), 403

        user.register_successful_login()
        db.session.commit()
        login_user(user, remember=form.remember.data)
        current_app.logger.info("Login: %s", user.email)

        next_url = request.args.get("next")
        return redirect(next_url if _is_safe_next(next_url) else url_for("main.index"))

    return render_template("auth/login.html", form=form)


@bp.route("/logout", methods=["POST"])
@login_required
def logout():
    logout_user()
    flash("You have been logged out.", "info")
    return redirect(url_for("auth.login"))


# --- password reset -------------------------------------------------------------------------


@bp.route("/forgot-password", methods=["GET", "POST"])
def forgot_password():
    form = EmailOnlyForm()
    if form.validate_on_submit():
        user = _find_user(form.email.data)
        if user and user.is_active:
            link = url_for("auth.reset_password", token=make_reset_token(user), _external=True)
            send_email(
                user.email,
                "Reset your EV Fleet Monitor password",
                "reset_password",
                user=user,
                link=link,
            )
        return render_template("auth/check_email.html", email=form.email.data, purpose="reset")
    return render_template(
        "auth/email_form.html",
        form=form,
        title="Forgot your password?",
        intro="Enter your account email and we'll send you a link to set a new password.",
    )


@bp.route("/reset-password/<token>", methods=["GET", "POST"])
def reset_password(token: str):
    user = load_reset_token(token)
    if user is None:
        flash("That reset link is invalid, expired or already used. Request a new one.", "error")
        return redirect(url_for("auth.forgot_password"))

    form = ResetPasswordForm()
    if form.validate_on_submit():
        user.set_password(form.password.data)
        user.is_verified = True  # following the emailed link proves they own the address
        user.failed_login_count = 0
        user.locked_until = None
        db.session.commit()
        current_app.logger.info("Password reset: %s", user.email)
        flash("Your password has been updated. Please log in.", "success")
        return redirect(url_for("auth.login"))
    return render_template("auth/reset_password.html", form=form)


# --- profile --------------------------------------------------------------------------------


@bp.route("/profile", methods=["GET", "POST"])
@login_required
def profile():
    form = ChangePasswordForm()
    if form.validate_on_submit():
        if not current_user.check_password(form.current_password.data):
            form.current_password.errors.append("Current password is incorrect.")
        else:
            current_user.set_password(form.password.data)
            db.session.commit()
            flash("Password changed.", "success")
            return redirect(url_for("auth.profile"))
    return render_template("auth/profile.html", form=form)
