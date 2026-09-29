"""Custom `flask` commands, e.g. `flask create-admin`."""

import random
import smtplib

import click
from flask import Flask

from app.extensions import db
from app.models import Role, User, Vehicle
from app.services.email import send_email

# (make, model, battery kWh, km per kWh): popular EVs in India, real-world figures.
DEMO_MODELS = [
    ("Tata", "Nexon EV", 40.5, 7.0),
    ("Tata", "Tigor EV", 26.0, 7.5),
    ("MG", "ZS EV", 50.3, 6.5),
    ("Mahindra", "XUV400", 39.4, 6.8),
    ("Hyundai", "Kona Electric", 39.2, 7.2),
    ("BYD", "e6", 71.7, 6.0),
    ("Tata", "Ace EV", 21.3, 6.2),
    ("Citroen", "eC3", 29.2, 7.3),
]
# State codes matching the simulator's cities (Delhi, Karnataka, Maharashtra, WB, Telangana).
DEMO_STATE_CODES = ["DL", "KA", "MH", "WB", "TS"]


def register_cli(app: Flask) -> None:
    @app.cli.command("seed-vehicles")
    @click.option("--count", default=6, show_default=True, help="How many demo EVs to add.")
    def seed_vehicles(count: int) -> None:
        """Add demo EVs (simulated) so the dashboards have something to show."""
        rng = random.Random()
        drivers = (
            db.session.execute(db.select(User).where(User.role == Role.DRIVER)).scalars().all()
        )
        added = 0
        while added < count:
            make, model, kwh, eff = rng.choice(DEMO_MODELS)
            plate = (
                f"{rng.choice(DEMO_STATE_CODES)}{rng.randint(1, 99):02d}"
                f"{rng.choice('ABCDEFGHJKLMNPRSTUVWXYZ')}{rng.choice('ABCDEFGHJKLMNPRSTUVWXYZ')}"
                f"{rng.randint(1000, 9999)}"
            )
            if db.session.execute(db.select(Vehicle.id).filter_by(plate_number=plate)).first():
                continue
            vehicle = Vehicle(
                make=make,
                model=model,
                year=rng.randint(2021, 2026),
                plate_number=plate,
                battery_capacity_kwh=kwh,
                efficiency_km_per_kwh=eff,
                driver_id=drivers[added % len(drivers)].id if drivers else None,
            )
            vehicle.issue_api_key()  # unused by simulated vehicles; rotate in the UI when needed
            db.session.add(vehicle)
            added += 1
        db.session.commit()
        click.echo(f"Added {added} demo vehicles.")

    @app.cli.command("simulate")
    @click.option("--once", is_flag=True, help="Run a single tick and exit.")
    def simulate(once: bool) -> None:
        """Run the telemetry simulator in this terminal (Ctrl+C to stop).

        Use this when the web server's built-in simulator is disabled
        (SIMULATOR_ENABLED=false), e.g. to run it as a separate process in production.
        """
        from app.services.simulator import FleetSimulator

        sim = FleetSimulator(app)
        if once:
            click.echo(f"Recorded {sim.step()} readings.")
            return
        try:
            sim.run_forever()
        except KeyboardInterrupt:
            click.echo("Simulator stopped.")

    @app.cli.command("prune-telemetry")
    @click.option("--days", type=int, help="Keep this many days (default: retention setting).")
    def prune_telemetry(days: int | None) -> None:
        """Delete telemetry readings older than the retention period."""
        from app.services.telemetry import prune_old_telemetry

        removed = prune_old_telemetry(days or app.config["TELEMETRY_RETENTION_DAYS"])
        db.session.commit()
        click.echo(f"Removed {removed} readings.")

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
