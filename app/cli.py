"""Custom `flask` commands, e.g. `flask create-admin`."""

import smtplib

import click
from flask import Flask

from app.extensions import db
from app.models import Role, User
from app.services.email import send_email


def register_cli(app: Flask) -> None:
    @app.cli.command("create-admin")
    @click.option("--email", prompt=True, help="Admin email address.")
    @click.option("--name", prompt=True, help="Admin full name.")
    @click.password_option(help="Admin password (prompted if omitted).")
    def create_admin(email: str, name: str, password: str) -> None:
        """Create a verified administrator account (use once to bootstrap the system)."""
        email = email.strip().lower()
        if db.session.execute(db.select(User).filter_by(email=email)).scalar_one_or_none():
            raise click.ClickException(f"A user with email {email} already exists.")
        if len(password) < 8:
            raise click.ClickException("Password must be at least 8 characters.")
        user = User(name=name.strip(), email=email, role=Role.ADMIN, is_verified=True)
        user.set_password(password)
        db.session.add(user)
        db.session.commit()
        click.echo(f"Admin {email} created.")

    @app.cli.command("send-test-email")
    @click.argument("to")
    def send_test_email(to: str) -> None:
        """Send a test email to check the SMTP settings in .env."""
        cfg = app.config
        if not cfg["MAIL_SERVER"]:
            raise click.ClickException("MAIL_SERVER is empty, so emails only go to the console.")
        if not (cfg["MAIL_USERNAME"] and cfg["MAIL_PASSWORD"]):
            raise click.ClickException(
                "MAIL_USERNAME and MAIL_PASSWORD must both be set in .env "
                "(for Gmail: your address and a 16-character App Password)."
            )
        click.echo(f"Sending via {cfg['MAIL_SERVER']}:{cfg['MAIL_PORT']} as {cfg['MAIL_USERNAME']}")
        try:
            with app.test_request_context():
                send_email(to, "EV Fleet Monitor test email", "test_email", raise_errors=True)
        except smtplib.SMTPAuthenticationError as err:
            raise click.ClickException(
                "Gmail rejected the login. Check MAIL_USERNAME and that MAIL_PASSWORD is a "
                f"16-character App Password, not your normal password. ({err.smtp_code})"
            ) from err
        except (smtplib.SMTPException, OSError) as err:
            raise click.ClickException(f"Sending failed: {err}") from err
        click.echo(f"Test email sent to {to}. Check the inbox (and the spam folder).")

    @app.cli.command("set-role")
    @click.argument("email")
    @click.argument("role", type=click.Choice([r.value for r in Role]))
    def set_role(email: str, role: str) -> None:
        """Change a user's role, e.g. `flask set-role ravi@example.com fleet_manager`."""
        user = db.session.execute(
            db.select(User).filter_by(email=email.strip().lower())
        ).scalar_one_or_none()
        if user is None:
            raise click.ClickException(f"No user with email {email}.")
        user.role = Role(role)
        db.session.commit()
        click.echo(f"{user.email} is now {user.role.label}.")
