# EV Fleet Monitoring

Real-Time EV Fleet Monitoring and Predictive Analytics Solution, an Infosys Springboard
internship project being rebuilt module by module to production standards.

> **Status:** Phase 0 (foundation) and Modules 1–3 (authentication and roles; EV registration
> and real-time monitoring; route optimisation and battery health) are complete. Modules 4–6
> are in progress; see the roadmap below. Full documentation will be added in the delivery phase.

## Quick start (Windows / PowerShell)

```powershell
python -m venv .venv
.\.venv\Scripts\Activate.ps1
pip install -r requirements-dev.txt
Copy-Item .env.example .env      # then edit values if needed
flask db upgrade                 # create/update the database tables
flask create-admin               # create the first administrator (prompts for details)
flask seed-vehicles              # optional: add 6 demo EVs driven by the simulator
flask run                        # open http://127.0.0.1:5000
pytest                           # run the test suite
ruff check .                     # lint
```

In Git Bash on Windows, activate with `source .venv/Scripts/activate`. On macOS/Linux, use
`source .venv/bin/activate`. In both, copy the env file with `cp .env.example .env`.

## Users and roles

| Role | How you get it | Can do |
|---|---|---|
| Driver | Default for every sign-up | View the vehicles assigned to them and their live data |
| Fleet Manager | Promoted by an admin | Whole fleet: vehicles, alerts, analytics, reports |
| Admin | `flask create-admin`, or promoted | Everything, plus user management (`/admin/users`) |

Sign-up requires email verification. Without SMTP settings in `.env`, the verification and
password-reset links are printed in the terminal running `flask run`. Other CLI commands:
`flask set-role <email> <admin|fleet_manager|driver>`.

Security measures: passwords hashed with scrypt; CSRF protection on every form; account lockout
after 5 failed logins (15 minutes); signed, expiring, single-use reset links; no account
enumeration through sign-up, login or reset; safe post-login redirects.

## Vehicles and real-time monitoring

Managers register EVs (make, model, registration number, battery size, efficiency, assigned
driver) and keep a service history for each. Every vehicle reports telemetry: location, speed,
battery charge (SoC), charging state, battery temperature and odometer. That data drives:

- **Live map** (`/monitoring/live`): all vehicles on OpenStreetMap, refreshed every 5 seconds,
  coloured by state (moving, idle, charging, offline) with low-battery warnings.
- **Vehicle page**: live figures, the last hour's route, and speed/battery charts.
- **Overview**: fleet totals (online, charging, average charge, low battery).

### Where telemetry comes from

| Source | When to use | How |
|---|---|---|
| Built-in simulator | No real devices (default) | Runs inside the web server. Vehicles marked "simulate" drive loops through Delhi, Bengaluru, Mumbai, Kolkata or Hyderabad, drain and recharge their batteries, and each has its own driving style. |
| Device API | Real telematics devices | `POST /api/v1/telemetry` with the vehicle's API key (shown once when the vehicle is registered). |

Simulator settings in `.env`: `SIMULATOR_ENABLED`, `SIMULATOR_INTERVAL_SECONDS` (default 5),
`SIMULATOR_TIME_SCALE` (default 12, so each 5 s tick simulates 1 minute of driving). To run it as
a separate process instead, set `SIMULATOR_ENABLED=false` and run `flask simulate`.

### Telemetry API

```bash
curl -X POST http://127.0.0.1:5000/api/v1/telemetry \
  -H "X-API-Key: evk_..." -H "Content-Type: application/json" \
  -d '{"lat": 28.6139, "lon": 77.2090, "speed_kmh": 42.5, "soc_pct": 76.2,
       "is_charging": false, "battery_temp_c": 31.4, "odometer_km": 15234.7}'
```

Required: `lat`, `lon`, `speed_kmh`, `soc_pct`. Optional: `is_charging`, `battery_temp_c`,
`odometer_km`, `acceleration_mps2`, `power_kw`, `recorded_at` (ISO 8601; defaults to now).
Send up to 100 buffered readings as `{"readings": [...]}`; a batch is stored all-or-nothing.
Responses: `201 {"accepted": n}`; `400` with per-field errors; `401` bad key; `403` inactive vehicle.

Readings older than `TELEMETRY_RETENTION_DAYS` (default 7) are pruned automatically, or with
`flask prune-telemetry`.

## Route planner

`/routes/plan` plans a trip for a fleet vehicle (or a custom EV) from a place name, coordinates
or the vehicle's current location:

1. Places are found with OpenStreetMap **Nominatim**; road routes, including alternatives, come
   from **OSRM**. Both are free and need no API key; set `NOMINATIM_URL` / `OSRM_URL` to use your
   own instances. If OSRM is unreachable the planner falls back to a clearly labelled
   straight-line estimate.
2. Car charging stations within a chosen distance of each route come from India's official
   public charging-station list (see [Data sources](#data-sources)), including each station's
   real charger power. 2/3-wheeler-only (LEV) chargers are excluded.
3. Stops are chosen greedily: drive as far as the battery allows while keeping a reserve, charge
   at the farthest reachable charger of at least the chosen power (25 kW DC by default), repeat.
   This minimises the number of stops. Charging time uses the lower of the station's power and
   the car's own limit.
4. Every alternative route is planned and the quickest feasible one (driving + charging) is
   recommended. If none works, the planner explains why and checks whether charging to 100% would.

## Battery health and alerts

- **ML health check** (`/battery`): estimates state of health (SoH, % of rated capacity) from
  measurements a BMS or service diagnostic can take without a full capacity test. It is a
  random forest trained on **real cell-ageing data from NASA**; on batteries it never saw, the
  typical error is about ±5 SoH points (R² 0.79). Managers can save results to a vehicle's
  battery history. See [`ml/MODEL_CARD.md`](ml/MODEL_CARD.md) for the method, evaluation and
  limitations.
- **Prediction API:** `POST /api/v1/predict/battery` with `cycle_count`,
  `ambient_temperature_c`, `discharge_current_a`, `avg_voltage_v`, `max_temperature_c`,
  `internal_resistance_mohm` (or `{"inputs": [...]}`, up to 100).
- **Alerts**, raised automatically and resolved when the condition clears (one open alert per
  vehicle and type):

| Alert | Warning | Critical | Clears when |
|---|---|---|---|
| Low battery | charge < 20% while not charging | < 10% | charging, or ≥ 25% |
| Battery overheating | ≥ 45 °C | ≥ 55 °C | ≤ 42 °C |
| Battery wear (from a saved check) | SoH < 80% | SoH < 70% | a later check ≥ 80% |

The wear thresholds follow EV practice: 80% is the usual end-of-first-life mark, and battery
warranties typically guarantee 70%.

## Data sources

Both datasets are real and rebuilt from their official sources by scripts in `scripts/`.
Downloads go to `data/raw/`, which git ignores; the cleaned outputs in `data/` are committed.

| Dataset | Source | Used for | Rebuild |
|---|---|---|---|
| `data/charging_stations_india.csv` | **Bureau of Energy Efficiency (Ministry of Power, Govt. of India)**, *EV Public Charging Stations Data till 26 October 2025*, [beeindia.gov.in](https://www.beeindia.gov.in/WriteReadData/RTF1984/EV_PCS_Data_29277.pdf) | Route planner | `python -m scripts.build_charging_stations` |
| `data/battery_cycles_nasa.csv` | **NASA Ames Prognostics Center of Excellence**, *Li-ion Battery Aging Data Set*, B. Saha & K. Goebel (2007), [NASA PCoE data repository](https://www.nasa.gov/intelligent-systems-division/discovery-and-systems-health/pcoe/pcoe-data-set-repository/) | Battery health model | `python -m scripts.build_battery_dataset`, then `python -m ml.train_battery_model` |

Each script prints a cleaning report: duplicates, invalid coordinates or measurements, and
normalised connector types. The original submission's datasets (a synthetic battery sheet and an
unofficial station list) are kept in `legacy/datasets/` for reference.

## Project layout

```
app/            Flask application (app factory, blueprints, templates, static files)
  config.py     settings per environment, read from environment variables
  extensions.py database, migrations, login and CSRF extensions
  models/       database tables (SQLAlchemy)
  auth/         sign-up, email verification, login/logout, password reset, profile, roles
  admin/        user management for administrators
  vehicles/     EV registration, details, service history, device API keys
  monitoring/   live fleet map and its JSON feed
  routing/      route planner page
  battery/      battery health page (ML checks, alerts)
  api/          versioned JSON API (/api/v1): telemetry ingest, battery prediction
  services/     email, telemetry, simulator, alerts, routing, battery model
  main/         overview dashboard and /healthz endpoint
  cli.py        create-admin, set-role, send-test-email, seed-vehicles, simulate, prune-telemetry
migrations/     database schema versions (Alembic via Flask-Migrate)
scripts/        rebuild datasets from their official sources
ml/             model training script and model card
models/         trained model + metadata (committed, so the app works without retraining)
data/           datasets used by the app
legacy/         original submission, kept for reference until each module is rebuilt
tests/          pytest test suite
wsgi.py         entry point for `flask run` and gunicorn
```

## Roadmap

| Phase | Scope | Status |
|---|---|---|
| 0 | Project foundation: structure, config, base layout, tests | Done |
| 1 | User authentication, email verification, roles | Done |
| 2 | EV registration and real-time monitoring | Done |
| 3 | Route optimisation and battery health (ML) | Done |
| 4 | Driver behaviour and maintenance alerts | Planned |
| 5 | Energy and cost analysis | Planned |
| 6 | Report generation | Planned |
| 7 | Admin, Docker, CI, deployment | Planned |
