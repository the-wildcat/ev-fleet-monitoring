import re

from flask_wtf import FlaskForm
from wtforms import BooleanField, EmailField, PasswordField, StringField, SubmitField
from wtforms.validators import DataRequired, Email, EqualTo, Length, ValidationError


def strong_password(form, field) -> None:
    """At least 8 characters with at least one letter and one digit."""
    if not (re.search(r"[A-Za-z]", field.data or "") and re.search(r"\d", field.data or "")):
        raise ValidationError("Password must contain at least one letter and one number.")


def _email_field(label: str = "Email") -> EmailField:
    return EmailField(
        label,
        validators=[DataRequired(), Email(), Length(max=254)],
        filters=[lambda v: v.strip().lower() if v else v],
    )


def _new_password_fields():
    password = PasswordField(
        "Password", validators=[DataRequired(), Length(min=8, max=128), strong_password]
    )
    confirm = PasswordField(
        "Confirm password",
        validators=[DataRequired(), EqualTo("password", message="Passwords must match.")],
    )
    return password, confirm


class RegisterForm(FlaskForm):
    name = StringField(
        "Full name",
        validators=[DataRequired(), Length(max=120)],
        filters=[lambda v: v.strip() if v else v],
    )
    email = _email_field()
    password, confirm = _new_password_fields()
    submit = SubmitField("Create account")


class LoginForm(FlaskForm):
    email = _email_field()
    password = PasswordField("Password", validators=[DataRequired()])
    remember = BooleanField("Keep me logged in")
    submit = SubmitField("Log in")


class EmailOnlyForm(FlaskForm):
    """Used for 'forgot password' and 'resend verification email'."""

    email = _email_field()
    submit = SubmitField("Send link")


class ResetPasswordForm(FlaskForm):
    password, confirm = _new_password_fields()
    submit = SubmitField("Set new password")


class ChangePasswordForm(FlaskForm):
    current_password = PasswordField("Current password", validators=[DataRequired()])
    password, confirm = _new_password_fields()
    submit = SubmitField("Change password")
