"""Battery Health page: fleet battery overview, ML health checks and battery alerts."""

from flask import current_app, flash, redirect, render_template, url_for
from flask_login import current_user, login_required
from flask_wtf import FlaskForm
from sqlalchemy import func
from wtforms import FloatField, SelectField, SubmitField
from wtforms.validators import InputRequired, NumberRange

from app.battery import bp
from app.extensions import db
from app.models import AlertType, BatteryCheck
from app.services.alerts import evaluate_battery_health, open_alerts_for
from app.services.battery_model import FEATURES
from app.vehicles.access import get_visible_vehicle, visible_vehicles_stmt

BATTERY_ALERTS = (AlertType.LOW_BATTERY, AlertType.BATTERY_OVERHEAT, AlertType.BATTERY_DEGRADED)


class BatteryCheckForm(FlaskForm):
    vehicle_id = SelectField("Save result to vehicle", coerce=int, choices=[])
    submit = SubmitField("Predict battery health")


# One numeric field per model feature, generated from FEATURES so the form, API and model
# can't drift apart.
for _name, _label, _unit, _lo, _hi in FEATURES:
    setattr(
        BatteryCheckForm,
        _name,
        FloatField(
            f"{_label} ({_unit})",
            validators=[InputRequired(), NumberRange(min=_lo, max=_hi)],
            render_kw={"step": "any"},
        ),
    )


def battery_model():
    return current_app.extensions["battery_model"]


def _latest_checks(vehicle_ids: list[int]) -> dict[int, BatteryCheck]:
    if not vehicle_ids:
        return {}
    newest = (
        db.select(BatteryCheck.vehicle_id, func.max(BatteryCheck.id).label("id"))
        .where(BatteryCheck.vehicle_id.in_(vehicle_ids))
        .group_by(BatteryCheck.vehicle_id)
        .subquery()
    )
    rows = db.session.execute(
        db.select(BatteryCheck).join(newest, BatteryCheck.id == newest.c.id)
    ).scalars()
    return {c.vehicle_id: c for c in rows}


@bp.route("/", methods=["GET", "POST"])
@login_required
def overview():
    vehicles = db.session.execute(visible_vehicles_stmt()).scalars().all()
    form = BatteryCheckForm()
    form.vehicle_id.choices = [(0, "— Don't save (prediction only) —")]
    if current_user.is_manager:
        form.vehicle_id.choices += [
            (v.id, f"{v.plate_number} · {v.display_name}") for v in vehicles
        ]

    prediction = None
    model = battery_model()
    if form.validate_on_submit():
        if not model.available:
            flash(
                "The battery model hasn't been trained yet: run "
                "`python -m ml.train_battery_model`.",
                "error",
            )
        else:
            inputs = {name: getattr(form, name).data for name, *_ in FEATURES}
            prediction = model.predict([inputs])[0]
            if form.vehicle_id.data and current_user.is_manager:
                vehicle = get_visible_vehicle(form.vehicle_id.data)
                db.session.add(
                    BatteryCheck(
                        vehicle_id=vehicle.id,
                        **inputs,
                        predicted_soh_pct=prediction.soh_pct,
                        status=prediction.status,
                        model_name=model.metadata.get("model", "unknown"),
                        created_by_id=current_user.id,
                    )
                )
                evaluate_battery_health(vehicle, prediction.soh_pct)
                db.session.commit()
                flash(
                    f"Battery check saved for {vehicle.plate_number}: "
                    f"{prediction.soh_pct:.1f}% ({prediction.status}).",
                    "success",
                )
                return redirect(url_for("battery.overview"))

    ids = [v.id for v in vehicles]
    return render_template(
        "battery/overview.html",
        vehicles=vehicles,
        latest_checks=_latest_checks(ids),
        alerts=open_alerts_for(ids, BATTERY_ALERTS),
        form=form,
        features=FEATURES,
        prediction=prediction,
        model_meta=model.metadata if model.available else None,
        offline_after=current_app.config["OFFLINE_AFTER_SECONDS"],
    )
