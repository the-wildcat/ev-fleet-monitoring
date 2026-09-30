from flask_wtf import FlaskForm
from wtforms import BooleanField, FloatField, SelectField, StringField, SubmitField
from wtforms.validators import DataRequired, Length, NumberRange, Optional, ValidationError


class RoutePlanForm(FlaskForm):
    vehicle_id = SelectField("Vehicle", coerce=int, choices=[])
    use_vehicle_location = BooleanField("Start from the vehicle's current location")
    origin = StringField("From", validators=[Optional(), Length(max=200)])
    destination = StringField("To", validators=[DataRequired(), Length(max=200)])
    start_soc_pct = FloatField(
        "Current battery (%)", validators=[Optional(), NumberRange(min=0, max=100)]
    )
    # Used when no vehicle is selected.
    battery_capacity_kwh = FloatField(
        "Battery capacity (kWh)", validators=[Optional(), NumberRange(min=5, max=300)]
    )
    efficiency_km_per_kwh = FloatField(
        "Efficiency (km per kWh)", validators=[Optional(), NumberRange(min=2, max=12)]
    )
    reserve_pct = FloatField(
        "Keep in reserve (%)", default=15, validators=[DataRequired(), NumberRange(min=5, max=50)]
    )
    target_soc_pct = FloatField(
        "Charge up to (%) at stops",
        default=80,
        validators=[DataRequired(), NumberRange(min=30, max=100)],
    )
    charger_kw = FloatField(
        "Charger power (kW)", default=50, validators=[DataRequired(), NumberRange(min=3, max=350)]
    )
    corridor_km = FloatField(
        "Max detour from route (km)",
        default=10,
        validators=[DataRequired(), NumberRange(min=1, max=30)],
    )
    submit = SubmitField("Plan route")

    def validate_origin(self, field) -> None:
        if not field.data and not self.use_vehicle_location.data:
            raise ValidationError("Enter a starting point or start from the vehicle's location.")

    def validate_target_soc_pct(self, field) -> None:
        if self.reserve_pct.data is not None and field.data <= self.reserve_pct.data:
            raise ValidationError("Must be higher than the reserve.")
