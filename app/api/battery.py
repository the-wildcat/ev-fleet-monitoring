"""POST /api/v1/predict/battery: battery state-of-health predictions (ML model).

Request (single input, or {"inputs": [...]} with up to 100):
    {"cycle_count": 100, "ambient_temperature_c": 24, "discharge_current_a": 2.0,
     "avg_voltage_v": 3.52, "max_temperature_c": 41.0, "internal_resistance_mohm": 131.6}

Response 200:
    {"predictions": [{"soh_pct": 78.6, "status": "Fair"}],
     "model": {"name": "random_forest", "trained_at": "...", "cv_mae": 5.35}}

The endpoint is stateless and stores nothing, so it doesn't require authentication; inputs
are validated and batches capped. 400 = invalid input, 503 = model not trained yet.
"""

from flask import abort, current_app, jsonify, request

from app.api import bp
from app.services.battery_model import validate_features

MAX_INPUTS = 100


@bp.post("/predict/battery")
def predict_battery():
    model = current_app.extensions["battery_model"]
    if not model.available:
        abort(503, description="Battery model not trained. Run: python -m ml.train_battery_model")

    body = request.get_json(silent=True)
    if not isinstance(body, dict):
        abort(400, description="Request body must be a JSON object.")
    inputs = body.get("inputs", [body])
    if not isinstance(inputs, list) or not 1 <= len(inputs) <= MAX_INPUTS:
        abort(400, description=f"`inputs` must be a list of 1 to {MAX_INPUTS} objects.")

    checked = [validate_features(item) for item in inputs]
    problems = [{"index": i, "errors": errs} for i, (_, errs) in enumerate(checked) if errs]
    if problems:
        return jsonify(error="Bad Request", message="Validation failed.", details=problems), 400

    predictions = model.predict([clean for clean, _ in checked])
    meta = model.metadata
    best = meta.get("model")
    return jsonify(
        predictions=[{"soh_pct": p.soh_pct, "status": p.status} for p in predictions],
        model={
            "name": best,
            "trained_at": meta.get("trained_at"),
            "cv_mae": meta.get("metrics", {}).get(best, {}).get("mae"),
        },
    )
