"""Custom `flask` commands, e.g. `flask create-admin`."""

import click
from flask import Flask

from app.extensions import db
from app.models import Role, User


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
