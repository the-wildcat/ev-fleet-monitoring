# Model card: battery state-of-health (SoH)

## What it does
Estimates a lithium-ion cell's **state of health**: remaining capacity as a percentage of its
rated capacity. It uses measurements a battery management system (BMS) or service diagnostic
can take **without a full capacity test**:

| Input | Unit | Training range |
|---|---|---|
| Charge/discharge cycles | cycles | 1 – 197 |
| Ambient temperature | °C | 4 – 44 |
| Discharge current | A | ~1 – 4 |
| Average voltage under load | V | ~2.8 – 3.7 |
| Peak cell temperature | °C | ~10 – 70 |
| Internal resistance (Re + Rct) | mΩ | ~68 – 326 |

The status follows EV industry practice:

| Status | SoH | Why |
|---|---|---|
| Good | ≥ 80% | 80% is the usual "end of first life" point for EV batteries |
| Fair | 70 – 80% | monitor; plan replacement |
| Needs Replacement | < 70% | below the 70% typically guaranteed by EV battery warranties |

Used by the **Battery Health** page, `POST /api/v1/predict/battery` and the battery-wear alerts.

## Data
**NASA Ames Prognostics Center of Excellence, Li-ion Battery Aging Data Set**
(B. Saha and K. Goebel, 2007, NASA Prognostics Data Repository). 34 commercial 18650 cells,
rated 2.0 Ah, repeatedly charged and discharged at 4 °C, 24 °C and 43 °C until they wore out, with
periodic electrochemical impedance (EIS) measurements.

`python -m scripts.build_battery_dataset` downloads the original archive and builds
`data/battery_cycles_nasa.csv`, one row per discharge cycle:
- inputs from the discharge measurements; internal resistance = Re + Rct from the most recent
  impedance test before that cycle;
- target `soh_pct` = measured capacity / 2.0 Ah × 100.

Cleaning, with counts printed by the script:

| Step | Rows removed |
|---|---|
| Duplicate copies of B0025–B0028 (shipped in two archives) | 112 |
| Cycles before the first impedance test (no resistance value) | 76 |
| Capacity outside 20–105% of rating (measurement glitches) | 215 |
| Single-cycle spikes > 10 points from the battery's rolling median | 16 |
| Batteries with < 20 usable cycles | 2 batteries |

Result: **2,449 discharge cycles from 32 batteries**, covering all three health bands.

## Evaluation
**Grouped cross-validation (8 folds by battery):** every prediction is made by a model that
never saw that battery. Splitting random cycles instead would leak information (neighbouring
cycles of the same cell are nearly identical) and inflate the score.

| Model | MAE (SoH points) | RMSE | R² | Correct status | Worst battery MAE |
|---|---|---|---|---|---|
| Mean baseline | 12.52 | 15.34 | −0.04 | 20% | 36.2 |
| Linear regression | 7.28 | 8.85 | 0.66 | 67% | 16.1 |
| **Random forest (selected)** | **5.35** | **6.88** | **0.79** | **74%** | 15.4 |
| Gradient boosting | 6.06 | 7.74 | 0.74 | 70% | 16.5 |

Feature importance (permutation, drop in R² when shuffled): average voltage under load (1.42),
ambient temperature (0.71), peak temperature (0.26), cycle count (0.25), discharge current
(0.19), internal resistance (0.06).

## Limitations
- **Cell-level, laboratory data.** EV packs contain many cells; apply the model to cell or
  module diagnostics, not to whole-pack telemetry. Lab cycling is harsher and more uniform than
  real driving.
- **Temperature effects mix with ageing.** At 4 °C cells deliver less capacity even when not
  worn; the model learns this (ambient temperature is an input), so compare checks taken at
  similar temperatures.
- **Different test protocols.** NASA ran groups of cells at different currents and cut-off
  voltages, which also changes measured capacity; that partly explains the worst-battery error.
- **Small number of batteries (32).** Expect around ±5 SoH points on a typical unseen cell and
  up to ±15 on unusual ones. Use the status bands as guidance, not a guarantee.
- **Out-of-range inputs** (e.g. 1,000 cycles) are extrapolation; the model is only reliable
  within the training ranges listed above.

## History
The original project used a 100-row synthetic spreadsheet (now in `legacy/datasets/`). Its
health column was an exact formula, `capacity / 40 − cycles / 75`, and it used current
capacity as an input, which is the quantity SoH is computed from, so any model scored perfectly.
Replacing it with real data gives honest, realistic accuracy.

## Retraining
```bash
python -m scripts.build_battery_dataset   # downloads ~210 MB on first run
python -m ml.train_battery_model
```
Commit the updated `data/battery_cycles_nasa.csv` and `models/battery_soh.*`, then restart the app.
