"""Battery state-of-health (SoH) model: shared by the training script and the app.

The model predicts SoH (% of rated capacity remaining) from measurements a battery management
system or service diagnostic can take without a full capacity test: cycle count, temperatures,
discharge current, average voltage under load and internal resistance. It is trained on the
NASA Li-ion Battery Aging dataset (real cells; see ml/MODEL_CARD.md).

Status bands follow EV industry practice: 80% is the usual "end of first life" mark and
manufacturer battery warranties typically guarantee 70%.
"""

from __future__ import annotations

import json
import threading
from dataclasses import dataclass
from pathlib import Path

import joblib
import pandas as pd

# (field name = dataset column, label, unit, min, max). Limits are broad physical bounds for
# a Li-ion cell; the training ranges are narrower (see the model card).
FEATURES: list[tuple[str, str, str, float, float]] = [
    ("cycle_count", "Charge/discharge cycles", "cycles", 0, 5_000),
    ("ambient_temperature_c", "Ambient temperature", "°C", -20, 60),
    ("discharge_current_a", "Discharge current", "A", 0.1, 20),
    ("avg_voltage_v", "Average voltage under load", "V", 2.0, 4.5),
    ("max_temperature_c", "Peak cell temperature", "°C", -20, 90),
    ("internal_resistance_mohm", "Internal resistance", "mΩ", 1, 2_000),
]
FEATURE_NAMES = [name for name, *_ in FEATURES]
TARGET = "soh_pct"

GOOD_SOH = 80.0
REPLACE_SOH = 70.0


def status_for(soh_pct: float) -> str:
    if soh_pct >= GOOD_SOH:
        return "Good"
    if soh_pct >= REPLACE_SOH:
        return "Fair"
    return "Needs Replacement"


def validate_features(raw: object) -> tuple[dict[str, float], dict[str, str]]:
    """Check one input dict; returns (clean values, errors)."""
    if not isinstance(raw, dict):
        return {}, {"_": "Each input must be a JSON object."}
    clean, errors = {}, {}
    for name, _label, _unit, lo, hi in FEATURES:
        value = raw.get(name)
        if value is None:
            errors[name] = "This field is required."
        elif isinstance(value, bool) or not isinstance(value, int | float):
            errors[name] = "Must be a number."
        elif not lo <= value <= hi:
            errors[name] = f"Must be between {lo} and {hi}."
        else:
            clean[name] = float(value)
    return clean, errors


@dataclass
class Prediction:
    soh_pct: float
    status: str


class BatteryModel:
    """Loads the trained model once and serves predictions (thread-safe lazy load)."""

    def __init__(self, model_path: Path) -> None:
        self.model_path = Path(model_path)
        self.meta_path = self.model_path.with_suffix(".json")
        self._model = None
        self._meta: dict | None = None
        self._lock = threading.Lock()

    @property
    def available(self) -> bool:
        return self.model_path.exists()

    def _load(self):
        with self._lock:
            if self._model is None:
                self._model = joblib.load(self.model_path)
                self._meta = json.loads(self.meta_path.read_text(encoding="utf-8"))
        return self._model

    @property
    def metadata(self) -> dict:
        self._load()
        return self._meta or {}

    def predict(self, inputs: list[dict[str, float]]) -> list[Prediction]:
        model = self._load()
        frame = pd.DataFrame([{name: item[name] for name in FEATURE_NAMES} for item in inputs])
        preds = model.predict(frame)
        results = []
        for value in preds:
            soh = round(float(min(100.0, max(0.0, value))), 2)
            results.append(Prediction(soh_pct=soh, status=status_for(soh)))
        return results
