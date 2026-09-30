"""EV registration, vehicle details, service history and device keys."""

from datetime import timedelta

from flask import current_app, flash, jsonify, redirect, render_template, request, url_for
from flask_login import current_user, login_required

from app.auth.decorators import role_required
from app.extensions import db
from app.models import (
    Alert,
    BatteryCheck,
    Role,
    ServiceRecord,
    ServiceType,
    Telemetry,
    User,
    Vehicle,
    VehicleStatus,
)
from app.utils import utcnow
from app.vehicles import bp
from app.vehicles.access import get_visible_vehicle, visible_vehicles_stmt
from app.vehicles.forms import ConfirmForm, ServiceRecordForm, VehicleForm

MANAGERS = (Role.ADMIN, Role.FLEET_MANAGER)


def _driver_choices() -> list[tuple[int, str]]:
    drivers = db.session.execute(
        db.select(User).where(User.role == Role.DRIVER, User.active.is_(True)).order_by(User.name)
    ).scalars()
    return [(0, "— Unassigned —")] + [(d.id, f"{d.name} ({d.email})") for d in drivers]


def _plate_taken(plate: str, exclude_id: int | None = None) -> bool:
    stmt = db.select(Vehicle.id).where(Vehicle.plate_number == plate)
    if exclude_id:
        stmt = stmt.where(Vehicle.id != exclude_id)
    return db.session.execute(stmt).first() is not None


def _vin_taken(vin: str | None, exclude_id: int | None = None) -> bool:
    if not vin:
        return False
    stmt = db.select(Vehicle.id).where(Vehicle.vin == vin)
    if exclude_id:
        stmt = stmt.where(Vehicle.id != exclude_id)
    return db.session.execute(stmt).first() is not None


def _validate_unique(form: VehicleForm, exclude_id: int | None = None) -> bool:
    ok = True
    if _plate_taken(form.plate_number.data, exclude_id):
        form.plate_number.errors.append("A vehicle with this registration number already exists.")
        ok = False
    if _vin_taken(form.vin.data, exclude_id):
        form.vin.errors.append("A vehicle with this VIN already exists.")
        ok = False
    return ok


def _apply_form(vehicle: Vehicle, form: VehicleForm) -> None:
    vehicle.make = form.make.data.strip()
    vehicle.model = form.model.data.strip()
    vehicle.year = form.year.data
    vehicle.plate_number = form.plate_number.data
    vehicle.vin = form.vin.data
    vehicle.battery_capacity_kwh = form.battery_capacity_kwh.data
    vehicle.efficiency_km_per_kwh = form.efficiency_km_per_kwh.data
    vehicle.status = VehicleStatus(form.status.data)
    vehicle.driver_id = form.driver_id.data or None
    vehicle.simulated = form.simulated.data


# --- list / register / edit / delete ----------------------------------------------------------


@bp.route("/")
@login_required
def list_vehicles():
    vehicles = db.session.execute(visible_vehicles_stmt()).scalars().all()
    return render_template(
        "vehicles/list.html",
        vehicles=vehicles,
        offline_after=current_app.config["OFFLINE_AFTER_SECONDS"],
    )


@bp.route("/new", methods=["GET", "POST"])
@role_required(*MANAGERS)
def create_vehicle():
    form = VehicleForm()
    form.driver_id.choices = _driver_choices()
    if form.validate_on_submit() and _validate_unique(form):
        vehicle = Vehicle()
        _apply_form(vehicle, form)
        api_key = vehicle.issue_api_key()
        db.session.add(vehicle)
        db.session.commit()
        current_app.logger.info(
            "%s registered vehicle %s", current_user.email, vehicle.plate_number
        )
        flash(f"{vehicle.display_name} ({vehicle.plate_number}) registered.", "success")
        # The plain key is shown exactly once, on this response, and never stored.
        return render_template("vehicles/api_key.html", vehicle=vehicle, api_key=api_key)
    return render_template("vehicles/form.html", form=form, vehicle=None)


@bp.route("/<int:vehicle_id>/edit", methods=["GET", "POST"])
@role_required(*MANAGERS)
def edit_vehicle(vehicle_id: int):
    vehicle = get_visible_vehicle(vehicle_id)
    form = VehicleForm(obj=vehicle)
    form.driver_id.choices = _driver_choices()
    if request.method == "GET":
        form.driver_id.data = vehicle.driver_id or 0
        form.status.data = vehicle.status.value
    if form.validate_on_submit() and _validate_unique(form, exclude_id=vehicle.id):
        _apply_form(vehicle, form)
        db.session.commit()
        flash("Vehicle updated.", "success")
        return redirect(url_for("vehicles.vehicle_detail", vehicle_id=vehicle.id))
    return render_template("vehicles/form.html", form=form, vehicle=vehicle)


@bp.route("/<int:vehicle_id>/delete", methods=["POST"])
@role_required(*MANAGERS)
def delete_vehicle(vehicle_id: int):
    vehicle = get_visible_vehicle(vehicle_id)
    if ConfirmForm().validate_on_submit():
        if request.form.get("confirm_plate", "").replace(" ", "").upper() != vehicle.plate_number:
            flash("Type the registration number exactly to confirm deletion.", "error")
            return redirect(url_for("vehicles.vehicle_detail", vehicle_id=vehicle.id))
        db.session.delete(vehicle)
        db.session.commit()
        current_app.logger.info("%s deleted vehicle %s", current_user.email, vehicle.plate_number)
        flash(f"{vehicle.plate_number} and all its data were deleted.", "success")
    return redirect(url_for("vehicles.list_vehicles"))


# --- detail, telemetry history ----------------------------------------------------------------


def _render_detail(vehicle: Vehicle, service_form: ServiceRecordForm):
    open_alerts = db.session.execute(
        db.select(Alert)
        .filter_by(vehicle_id=vehicle.id, resolved_at=None)
        .order_by(Alert.created_at.desc())
    ).scalars()
    battery_checks = db.session.execute(
        db.select(BatteryCheck)
        .filter_by(vehicle_id=vehicle.id)
        .order_by(BatteryCheck.created_at.desc())
        .limit(10)
    ).scalars()
    return render_template(
        "vehicles/detail.html",
        vehicle=vehicle,
        service_form=service_form,
        confirm_form=ConfirmForm(),
        open_alerts=list(open_alerts),
        battery_checks=list(battery_checks),
        offline_after=current_app.config["OFFLINE_AFTER_SECONDS"],
    )


@bp.route("/<int:vehicle_id>")
@login_required
def vehicle_detail(vehicle_id: int):
    return _render_detail(get_visible_vehicle(vehicle_id), ServiceRecordForm())


@bp.route("/<int:vehicle_id>/telemetry.json")
@login_required
def vehicle_telemetry(vehicle_id: int):
    """Recent readings for the detail-page charts. ?minutes= (default 60, max 1440)."""
    vehicle = get_visible_vehicle(vehicle_id)
    minutes = min(max(request.args.get("minutes", 60, type=int), 1), 1440)
    since = utcnow() - timedelta(minutes=minutes)
    rows = (
        db.session.execute(
            db.select(Telemetry)
            .where(Telemetry.vehicle_id == vehicle.id, Telemetry.recorded_at >= since)
            .order_by(Telemetry.recorded_at.desc())
            .limit(500)
        )
        .scalars()
        .all()
    )
    return jsonify(vehicle_id=vehicle.id, readings=[r.to_dict() for r in reversed(rows)])


# --- service history --------------------------------------------------------------------------


@bp.route("/<int:vehicle_id>/services", methods=["POST"])
@role_required(*MANAGERS)
def add_service_record(vehicle_id: int):
    vehicle = get_visible_vehicle(vehicle_id)
    form = ServiceRecordForm()
    if form.validate_on_submit():
        db.session.add(
            ServiceRecord(
                vehicle_id=vehicle.id,
                service_date=form.service_date.data,
                service_type=ServiceType(form.service_type.data),
                odometer_km=form.odometer_km.data,
                cost_inr=form.cost_inr.data,
                description=(form.description.data or "").strip(),
            )
        )
        db.session.commit()
        flash("Service record added.", "success")
        return redirect(url_for("vehicles.vehicle_detail", vehicle_id=vehicle.id) + "#service")
    # Re-render the page with the form errors shown.
    return _render_detail(vehicle, form), 400


@bp.route("/<int:vehicle_id>/services/<int:record_id>/delete", methods=["POST"])
@role_required(*MANAGERS)
def delete_service_record(vehicle_id: int, record_id: int):
    vehicle = get_visible_vehicle(vehicle_id)
    record = db.session.get(ServiceRecord, record_id)
    if record and record.vehicle_id == vehicle.id and ConfirmForm().validate_on_submit():
        db.session.delete(record)
        db.session.commit()
        flash("Service record deleted.", "success")
    return redirect(url_for("vehicles.vehicle_detail", vehicle_id=vehicle.id) + "#service")


# --- device API key ---------------------------------------------------------------------------


@bp.route("/<int:vehicle_id>/api-key", methods=["POST"])
@role_required(*MANAGERS)
def regenerate_api_key(vehicle_id: int):
    vehicle = get_visible_vehicle(vehicle_id)
    if not ConfirmForm().validate_on_submit():
        return redirect(url_for("vehicles.vehicle_detail", vehicle_id=vehicle.id))
    api_key = vehicle.issue_api_key()
    db.session.commit()
    current_app.logger.info("%s rotated API key for %s", current_user.email, vehicle.plate_number)
    return render_template("vehicles/api_key.html", vehicle=vehicle, api_key=api_key, rotated=True)
