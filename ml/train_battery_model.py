"""Train the battery state-of-health (SoH) model.

Usage (from the project root):
    python -m ml.train_battery_model

Compares a baseline and three regressors with repeated 5-fold cross-validation, keeps the
model with the lowest mean absolute error, refits it on all rows and writes:
    models/battery_soh.joblib   the fitted scikit-learn pipeline
    models/battery_soh.json     metadata: features, metrics, versions, dataset hash
"""

from __future__ import annotations

import hashlib
import json
import sys
import unicodedata
from datetime import UTC, datetime
from pathlib import Path

import joblib
import numpy as np
import pandas as pd
import sklearn
from sklearn.base import clone
from sklearn.dummy import DummyRegressor
from sklearn.ensemble import GradientBoostingRegressor, RandomForestRegressor
from sklearn.linear_model import LinearRegression
from sklearn.model_selection import RepeatedKFold, cross_validate
from sklearn.pipeline import make_pipeline
from sklearn.preprocessing import StandardScaler

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

from app.services.battery_model import FEATURES, TARGET, status_for  # noqa: E402

DATA_PATH = ROOT / "data" / "Battery_Health_Dataset.xlsx"
MODEL_PATH = ROOT / "models" / "battery_soh.joblib"
RANDOM_STATE = 42

CANDIDATES = {
    "baseline_mean": DummyRegressor(strategy="mean"),
    "linear_regression": make_pipeline(StandardScaler(), LinearRegression()),
    "random_forest": RandomForestRegressor(
        n_estimators=300, min_samples_leaf=2, random_state=RANDOM_STATE
    ),
    "gradient_boosting": GradientBoostingRegressor(random_state=RANDOM_STATE),
}


def load_data() -> tuple[pd.DataFrame, pd.Series, pd.Series]:
    df = pd.read_excel(DATA_PATH)
    # NFKC folds look-alike characters, e.g. the Ohm sign (U+2126) used in the dataset's
    # "mΩ" header into the Greek capital omega (U+03A9) used in FEATURES.
    df.columns = [unicodedata.normalize("NFKC", str(c)).strip() for c in df.columns]
    columns = [col for _, col, *_ in FEATURES]
    missing = set(columns + [TARGET, "Status"]) - set(df.columns)
    if missing:
        raise SystemExit(f"Dataset is missing columns: {sorted(missing)}")
    df = df.dropna(subset=columns + [TARGET])
    return df[columns], df[TARGET], df["Status"]


def status_accuracy(model, X, y_soh, y_status, cv) -> float:
    """How often the status derived from predicted SoH matches the labelled status."""
    hits = total = 0
    for train_idx, test_idx in cv.split(X):
        fitted = clone(model).fit(X.iloc[train_idx], y_soh.iloc[train_idx])
        predicted = [status_for(v) for v in fitted.predict(X.iloc[test_idx])]
        hits += sum(p == t for p, t in zip(predicted, y_status.iloc[test_idx], strict=True))
        total += len(test_idx)
    return hits / total


def main() -> None:
    X, y_soh, y_status = load_data()
    print(f"Loaded {len(X)} rows from {DATA_PATH.name}")

    # The labels follow the documented SoH bands exactly; check so a dataset change is noticed.
    label_agreement = float(
        np.mean([status_for(v) == s for v, s in zip(y_soh, y_status, strict=True)])
    )
    print(f"Status labels consistent with SoH bands: {label_agreement:.0%}")

    cv = RepeatedKFold(n_splits=5, n_repeats=3, random_state=RANDOM_STATE)
    scoring = {
        "mae": "neg_mean_absolute_error",
        "rmse": "neg_root_mean_squared_error",
        "r2": "r2",
    }
    results = {}
    print(f"\n{'model':<20}{'MAE':>8}{'RMSE':>8}{'R2':>8}")
    for name, model in CANDIDATES.items():
        scores = cross_validate(model, X, y_soh, cv=cv, scoring=scoring)
        results[name] = {
            "mae": round(float(-scores["test_mae"].mean()), 3),
            "mae_std": round(float(scores["test_mae"].std()), 3),
            "rmse": round(float(-scores["test_rmse"].mean()), 3),
            "r2": round(float(scores["test_r2"].mean()), 3),
        }
        r = results[name]
        print(f"{name:<20}{r['mae']:>8.2f}{r['rmse']:>8.2f}{r['r2']:>8.3f}")

    best_name = min((n for n in results if n != "baseline_mean"), key=lambda n: results[n]["mae"])
    best = CANDIDATES[best_name]
    results[best_name]["status_accuracy"] = round(
        status_accuracy(
            best, X, y_soh, y_status, RepeatedKFold(n_splits=5, n_repeats=1, random_state=1)
        ),
        3,
    )
    print(f"\nSelected: {best_name} (status accuracy {results[best_name]['status_accuracy']:.0%})")

    best.fit(X, y_soh)
    MODEL_PATH.parent.mkdir(exist_ok=True)
    joblib.dump(best, MODEL_PATH)

    metadata = {
        "model": best_name,
        "target": TARGET,
        "features": [{"name": n, "column": c, "unit": u} for n, c, u, *_ in FEATURES],
        "status_bands": {"Good": ">= 75", "Fair": "50 - 75", "Needs Replacement": "< 50"},
        "cv": "RepeatedKFold(n_splits=5, n_repeats=3)",
        "metrics": results,
        "training_rows": len(X),
        "dataset": DATA_PATH.name,
        "dataset_sha256": hashlib.sha256(DATA_PATH.read_bytes()).hexdigest(),
        "sklearn_version": sklearn.__version__,
        "trained_at": datetime.now(UTC).isoformat(timespec="seconds"),
    }
    MODEL_PATH.with_suffix(".json").write_text(json.dumps(metadata, indent=2), encoding="utf-8")
    print(f"Saved {MODEL_PATH.relative_to(ROOT)} and its metadata")


if __name__ == "__main__":
    main()
