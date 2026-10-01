"""Train the battery state-of-health (SoH) model on real cell-ageing data.

Usage (from the project root):
    python -m scripts.build_battery_dataset   # once: download + clean the NASA data
    python -m ml.train_battery_model

Evaluation uses grouped cross-validation: each fold holds out whole batteries, so the score
measures how well the model predicts batteries it has never seen. (Splitting random cycles
would let the model see other cycles of the same battery and inflate the score.)

Writes:
    models/battery_soh.joblib   the fitted scikit-learn pipeline
    models/battery_soh.json     metadata: features, metrics, versions, dataset hash
"""

from __future__ import annotations

import hashlib
import json
import sys
from datetime import UTC, datetime
from pathlib import Path

import joblib
import numpy as np
import pandas as pd
import sklearn
from sklearn.dummy import DummyRegressor
from sklearn.ensemble import HistGradientBoostingRegressor, RandomForestRegressor
from sklearn.inspection import permutation_importance
from sklearn.linear_model import LinearRegression
from sklearn.model_selection import GroupKFold, cross_val_predict
from sklearn.pipeline import make_pipeline
from sklearn.preprocessing import StandardScaler

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

from app.services.battery_model import (  # noqa: E402
    FEATURE_NAMES,
    FEATURES,
    GOOD_SOH,
    REPLACE_SOH,
    TARGET,
    status_for,
)

DATA_PATH = ROOT / "data" / "battery_cycles_nasa.csv"
MODEL_PATH = ROOT / "models" / "battery_soh.joblib"
RANDOM_STATE = 42
N_SPLITS = 8

CANDIDATES = {
    "baseline_mean": DummyRegressor(strategy="mean"),
    "linear_regression": make_pipeline(StandardScaler(), LinearRegression()),
    "random_forest": RandomForestRegressor(
        n_estimators=400, min_samples_leaf=3, n_jobs=-1, random_state=RANDOM_STATE
    ),
    "gradient_boosting": HistGradientBoostingRegressor(
        max_iter=400, learning_rate=0.05, min_samples_leaf=20, random_state=RANDOM_STATE
    ),
}


def load_data() -> tuple[pd.DataFrame, pd.Series, pd.Series]:
    if not DATA_PATH.exists():
        raise SystemExit(
            f"{DATA_PATH.name} not found. Run: python -m scripts.build_battery_dataset"
        )
    df = pd.read_csv(DATA_PATH)
    df = df.dropna(subset=FEATURE_NAMES + [TARGET])
    return df[FEATURE_NAMES], df[TARGET], df["battery_id"]


def evaluate(model, X, y, groups, cv) -> dict:
    """Out-of-fold predictions for every row, each made by a model that never saw its battery."""
    pred = cross_val_predict(model, X, y, groups=groups, cv=cv)
    err = pred - y
    per_battery_mae = pd.Series(np.abs(err)).groupby(groups.to_numpy()).mean()
    true_status = y.map(status_for)
    pred_status = pd.Series(pred, index=y.index).map(status_for)
    return {
        "mae": round(float(np.abs(err).mean()), 3),
        "rmse": round(float(np.sqrt((err**2).mean())), 3),
        "r2": round(float(1 - (err**2).sum() / ((y - y.mean()) ** 2).sum()), 3),
        "status_accuracy": round(float((true_status == pred_status).mean()), 3),
        "worst_battery_mae": round(float(per_battery_mae.max()), 3),
    }


def main() -> None:
    X, y, groups = load_data()
    print(f"Loaded {len(X)} discharge cycles from {groups.nunique()} batteries ({DATA_PATH.name})")

    cv = GroupKFold(n_splits=N_SPLITS)
    results = {}
    print(f"\n{'model':<20}{'MAE':>7}{'RMSE':>7}{'R2':>7}{'status':>8}{'worst':>7}")
    for name, model in CANDIDATES.items():
        r = results[name] = evaluate(model, X, y, groups, cv)
        print(
            f"{name:<20}{r['mae']:>7.2f}{r['rmse']:>7.2f}{r['r2']:>7.3f}"
            f"{r['status_accuracy']:>8.0%}{r['worst_battery_mae']:>7.1f}"
        )

    best_name = min((n for n in results if n != "baseline_mean"), key=lambda n: results[n]["mae"])
    best = CANDIDATES[best_name].fit(X, y)
    print(f"\nSelected: {best_name}")

    importance = permutation_importance(best, X, y, n_repeats=5, random_state=RANDOM_STATE)
    ranked = sorted(
        zip(FEATURE_NAMES, importance.importances_mean, strict=True), key=lambda p: -p[1]
    )
    print("Permutation importance (drop in R² when a feature is shuffled):")
    for feature, value in ranked:
        print(f"  {feature:<26}{value:.3f}")

    MODEL_PATH.parent.mkdir(exist_ok=True)
    joblib.dump(best, MODEL_PATH)
    training_ranges = {
        name: [round(float(X[name].min()), 2), round(float(X[name].max()), 2)]
        for name in FEATURE_NAMES
    }
    metadata = {
        "model": best_name,
        "target": "SoH % (measured capacity / 2.0 Ah rated capacity)",
        "features": [
            {"name": n, "label": label, "unit": unit, "training_range": training_ranges[n]}
            for n, label, unit, *_ in FEATURES
        ],
        "status_bands": {
            "Good": f">= {GOOD_SOH:g}",
            "Fair": f"{REPLACE_SOH:g} - {GOOD_SOH:g}",
            "Needs Replacement": f"< {REPLACE_SOH:g}",
        },
        "cv": f"GroupKFold(n_splits={N_SPLITS}) by battery (unseen-battery evaluation)",
        "metrics": results,
        "feature_importance": {f: round(float(v), 4) for f, v in ranked},
        "training_rows": len(X),
        "training_batteries": int(groups.nunique()),
        "dataset": DATA_PATH.name,
        "dataset_source": "NASA Ames PCoE Li-ion Battery Aging Data Set (Saha & Goebel, 2007)",
        "dataset_sha256": hashlib.sha256(DATA_PATH.read_bytes()).hexdigest(),
        "sklearn_version": sklearn.__version__,
        "trained_at": datetime.now(UTC).isoformat(timespec="seconds"),
    }
    MODEL_PATH.with_suffix(".json").write_text(json.dumps(metadata, indent=2), encoding="utf-8")
    print(f"Saved {MODEL_PATH.relative_to(ROOT)} and its metadata")


if __name__ == "__main__":
    main()
