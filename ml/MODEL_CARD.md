# Model card: battery state-of-health (SoH)

## What it does
Predicts a battery's **state of health** (SoH, % of original capacity remaining) from one
diagnostic reading, and derives a status from it:

| Status | SoH band |
|---|---|
| Good | ≥ 75% |
| Fair | 50% – 75% |
| Needs Replacement | < 50% |

Inputs: capacity (mAh), cycle count, voltage (V), temperature (°C), internal resistance (mΩ).
Used by the **Battery Health** page, the `POST /api/v1/predict/battery` endpoint and the
battery-wear alerts.

## Training
- **Data:** `data/Battery_Health_Dataset.xlsx`, 100 rows, no missing values. The readings are
  cell-level (2,000–4,000 mAh, 3.2–4.2 V), so inputs should come from a cell or module
  diagnostic test, not from whole-pack telemetry.
- **Method:** `python -m ml.train_battery_model` compares a mean baseline, linear regression,
  random forest and gradient boosting with repeated 5-fold cross-validation (5 × 3 = 15 fits
  each), keeps the lowest mean absolute error (MAE), then refits on all rows.
- **Outputs:** `models/battery_soh.joblib` and `models/battery_soh.json` (metrics, feature list,
  scikit-learn version, dataset SHA-256 and training time, for reproducibility).

## Results (cross-validated)

| Model | MAE (SoH %) | RMSE | R² |
|---|---|---|---|
| Mean baseline | 14.11 | 16.23 | −0.07 |
| **Linear regression (selected)** | **0.00** | **0.00** | **1.000** |
| Random forest | 2.47 | 3.13 | 0.958 |
| Gradient boosting | 1.65 | 2.05 | 0.982 |

Status derived from predicted SoH matches the labelled status 100% of the time.

## Important limitation: the dataset is synthetic
A perfect score is a warning sign, so the fitted model was inspected. The labels follow an
exact formula:

> **SoH ≈ capacity_mAh / 40 − cycle_count / 75**

Voltage, temperature and internal resistance have no effect (coefficients ≈ 0). Linear
regression therefore recovers the generating formula, which is why it scores perfectly. Notes:

- The original project used a random forest; on this data it is less accurate than the linear
  model, which the comparison above makes visible.
- The model is only as realistic as the dataset. **Real batteries** degrade non-linearly and
  depend on temperature and resistance; with real data, expect a non-zero error and possibly a
  different winning model. The training pipeline needs no code changes for that: replace the
  dataset and re-run the script.
- 100 rows is very small; cross-validation is used instead of a single train/test split for that
  reason.
- Inputs outside the training ranges (above) are extrapolations; the API enforces broad
  physical limits but cannot guarantee accuracy outside the observed range.

## Retraining
```bash
python -m ml.train_battery_model
```
Commit the updated `models/battery_soh.*` files. The app loads the model at the first
prediction; restart the app to pick up a new model.
