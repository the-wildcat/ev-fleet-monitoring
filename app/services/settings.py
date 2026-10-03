"""Admin-editable system settings.

Each setting has a default in config (overridable via environment variables) and can be
changed at runtime by an administrator on the Settings page; the database value wins.
Values are validated against the ranges below before being saved.
"""

from __future__ import annotations

from dataclasses import dataclass

from flask import current_app

from app.extensions import db
from app.models import Setting
from app.utils import utcnow


@dataclass(frozen=True)
class SettingDef:
    key: str
    config_key: str  # default comes from app.config[config_key]
    label: str
    unit: str
    minimum: float
    maximum: float
    help: str
    group: str


SETTINGS: dict[str, SettingDef] = {
    s.key: s
    for s in [
        SettingDef(
            key="energy_tariff_inr_per_kwh",
            config_key="ENERGY_TARIFF_INR_PER_KWH",
            label="Electricity tariff",
            unit="₹/kWh",
            minimum=0,
            maximum=100,
            help="Average price paid per kWh of charging.",
            group="Energy & cost",
        ),
        SettingDef(
            key="charging_efficiency_pct",
            config_key="CHARGING_EFFICIENCY_PCT",
            label="Charging efficiency",
            unit="%",
            minimum=50,
            maximum=100,
            help="Share of grid energy that reaches the battery (AC ~88%, DC ~92%).",
            group="Energy & cost",
        ),
        SettingDef(
            key="petrol_price_inr_per_l",
            config_key="PETROL_PRICE_INR_PER_L",
            label="Petrol price",
            unit="₹/L",
            minimum=0,
            maximum=500,
            help="Used for the savings-versus-petrol comparison.",
            group="Energy & cost",
        ),
        SettingDef(
            key="ice_km_per_l",
            config_key="ICE_KM_PER_L",
            label="Comparable petrol car mileage",
            unit="km/L",
            minimum=1,
            maximum=50,
            help="Fuel economy of the petrol car the EVs replace.",
            group="Energy & cost",
        ),
        SettingDef(
            key="speed_limit_kmh",
            config_key="SPEED_LIMIT_KMH",
            label="Fleet speed limit",
            unit="km/h",
            minimum=20,
            maximum=200,
            help="Driving above this counts as a speeding event.",
            group="Driver behaviour",
        ),
    ]
}


def get_setting(key: str) -> float:
    definition = SETTINGS[key]
    row = db.session.get(Setting, key)
    if row is not None:
        try:
            return float(row.value)
        except ValueError:
            current_app.logger.warning("Ignoring invalid stored setting %s=%r", key, row.value)
    return float(current_app.config[definition.config_key])


def all_settings() -> dict[str, float]:
    return {key: get_setting(key) for key in SETTINGS}


def set_setting(key: str, value: float, user_id: int | None) -> None:
    definition = SETTINGS[key]
    if not definition.minimum <= value <= definition.maximum:
        raise ValueError(
            f"{definition.label} must be between {definition.minimum:g} and {definition.maximum:g}."
        )
    row = db.session.get(Setting, key)
    if row is None:
        row = Setting(key=key, value="")
        db.session.add(row)
    row.value = repr(float(value))
    row.updated_by_id = user_id
    row.updated_at = utcnow()


def reset_setting(key: str) -> None:
    row = db.session.get(Setting, key)
    if row is not None:
        db.session.delete(row)
        db.session.flush()  # so get_setting() sees the default straight away
