"""Battery state-of-health (SoH) model: shared by the training script and the app.

The model predicts SoH (%) from a battery diagnostic reading. The dataset's Status labels
follow fixed SoH bands (Good >= 75, Fair 50-75, Needs Replacement < 50), so the status is
derived from the predicted SoH rather than predicted by a second model.
"""

from __future__ import annotations

import json
import threading
from dataclasses import dataclass
from pathlib import Path

import joblib
import pandas as pd

# (API/form field name, dataset column name, unit, min, max)
FEATURES: list[tuple[str, str, str, float, float]] = [
    ("capacity_mah", "Battery Capacity (mAh)", "mAh", 500, 10_000),
    ("cycle_count", "Cycle Count", "cycles", 0, 10_000),
    ("voltage_v", "Voltage (V)", "V", 2.0, 5.0),
    ("temperature_c", "Temperature (°C)", "°C", -30, 80),
    ("internal_resistance_mohm", "Internal Resistance (mΩ)", "mΩ", 1, 1_000),
]
TARGET = "Battery Health (%)"

GOOD_SOH = 75.0
REPLACE_SOH = 50.0


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
    for name, _col, _unit, lo, hi in FEATURES:
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
        frame = pd.DataFrame([{col: item[name] for name, col, *_ in FEATURES} for item in inputs])
        preds = model.predict(frame)
        results = []
        for value in preds:
            soh = round(float(min(100.0, max(0.0, value))), 2)
            results.append(Prediction(soh_pct=soh, status=status_for(soh)))
        return results
