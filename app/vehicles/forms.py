import re
from datetime import date

from flask_wtf import FlaskForm
from wtforms import (
    BooleanField,
    DateField,
    FloatField,
    IntegerField,
    SelectField,
    StringField,
    SubmitField,
    TextAreaField,
)
from wtforms.validators import DataRequired, Length, NumberRange, Optional, ValidationError

from app.models import ServiceType, VehicleStatus

# Standard plates (state code, district, series, number), e.g. WB12AD3456, DL3CAB1234,
# and Bharat (BH) series plates, e.g. 22BH1234AA. Spaces and hyphens are ignored.
_PLATE_PATTERNS = (
    re.compile(r"^[A-Z]{2}\d{1,2}[A-Z]{0,3}\d{4}$"),
    re.compile(r"^\d{2}BH\d{4}[A-Z]{1,2}$"),
)
# VINs are 17 characters and never use I, O or Q (they look like 1 and 0).
_VIN_PATTERN = re.compile(r"^[A-HJ-NPR-Z0-9]{17}$")


def normalise_plate(value: str | None) -> str | None:
    return re.sub(r"[\s-]", "", value).upper() if value else value


def normalise_vin(value: str | None) -> str | None:
    """Uppercase and trim; an empty VIN is stored as NULL (it's optional and unique)."""
    value = (value or "").strip().upper()
    return value or None


def indian_plate(form, field) -> None:
    if not any(p.match(field.data or "") for p in _PLATE_PATTERNS):
        raise ValidationError("Enter a valid Indian registration number, e.g. WB12AD3456.")


def vin_format(form, field) -> None:
    if field.data and not _VIN_PATTERN.match(field.data):
        raise ValidationError("A VIN has 17 letters/digits and never uses I, O or Q.")


class VehicleForm(FlaskForm):
    make = StringField("Make", validators=[DataRequired(), Length(max=60)])
    model = StringField("Model", validators=[DataRequired(), Length(max=60)])
    year = IntegerField(
        "Year", validators=[Optional(), NumberRange(min=2010, max=date.today().year + 1)]
    )
    plate_number = StringField(
        "Registration number", validators=[DataRequired(), indian_plate], filters=[normalise_plate]
    )
    vin = StringField(
        "VIN (optional)",
        validators=[Optional(), vin_format],
        filters=[normalise_vin],
    )
    battery_capacity_kwh = FloatField(
        "Battery capacity (kWh)", validators=[DataRequired(), NumberRange(min=5, max=300)]
    )
    efficiency_km_per_kwh = FloatField(
        "Efficiency (km per kWh)",
        default=6.5,
        validators=[DataRequired(), NumberRange(min=2, max=12)],
    )
    status = SelectField("Status", choices=[(s.value, s.label) for s in VehicleStatus])
    driver_id = SelectField("Assigned driver", coerce=int, choices=[])
    simulated = BooleanField("Simulate telemetry (no real device fitted yet)", default=True)
    submit = SubmitField("Save vehicle")


class ServiceRecordForm(FlaskForm):
    service_date = DateField("Date", default=date.today, validators=[DataRequired()])
    service_type = SelectField("Type", choices=[(t.value, t.label) for t in ServiceType])
    odometer_km = FloatField(
        "Odometer (km)", validators=[Optional(), NumberRange(min=0, max=5_000_000)]
    )
    cost_inr = FloatField("Cost (₹)", validators=[Optional(), NumberRange(min=0, max=10_000_000)])
    description = TextAreaField("Work done", validators=[Optional(), Length(max=500)])
    submit = SubmitField("Add service record")

    def validate_service_date(self, field) -> None:
        if field.data and field.data > date.today():
            raise ValidationError("Service date can't be in the future.")


class ConfirmForm(FlaskForm):
    """Empty form carrying only the CSRF token, for POST-only buttons."""
