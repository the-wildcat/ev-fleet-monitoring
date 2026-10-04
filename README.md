# EV Fleet Monitoring

[![CI](https://github.com/the-wildcat/ev-fleet-monitoring/actions/workflows/ci.yml/badge.svg)](https://github.com/the-wildcat/ev-fleet-monitoring/actions/workflows/ci.yml)
![Python](https://img.shields.io/badge/python-3.13-blue)
[![License: MIT](https://img.shields.io/badge/license-MIT-green)](LICENSE)

Real-time monitoring and predictive analytics for electric vehicle fleets: live vehicle
tracking, battery health prediction, charging-aware route planning, driver behaviour scoring,
maintenance alerts, energy and cost analysis, and exportable reports.

Built with Flask, SQLAlchemy and scikit-learn, using real data: India's official public
charging-station list and NASA's battery ageing dataset. Developed as the project for the
Infosys Springboard internship.

<p align="center">
  <img src="docs/screenshots/overview-light.webp" alt="Fleet overview dashboard" width="49%">
  <img src="docs/screenshots/live-map-dark.webp" alt="Live fleet map in dark mode" width="49%">
</p>

## Contents

- [Features](#features)
- [Screenshots](#screenshots)
- [Architecture](#architecture)
- [Getting started](#getting-started)
- [Configuration](#configuration)
- [Deployment](#deployment)
- [API](#api)
- [Testing and quality](#testing-and-quality)
- [Modules in detail](#modules-in-detail)
- [Data sources](#data-sources)
- [Project structure](#project-structure)
- [Design decisions and limitations](#design-decisions-and-limitations)
- [License](#license)

## Features

| Area | What it does |
|---|---|
| Accounts and roles | Sign-up with email verification, password reset, login lockout; Admin, Fleet Manager and Driver roles |
| Vehicles and live monitoring | EV registry with service history; live map refreshed every 5 s; per-vehicle route and charts; device telemetry API |
| Route planning | Road routes (OSRM) with charging stops chosen from 14,357 official car charging stations |
| Battery health | Random-forest state-of-health model trained on NASA cell-ageing data; low-charge, overheating and wear alerts |
| Driver behaviour | Harsh braking, harsh acceleration and speeding per 100 km; safety score; energy impact |
| Maintenance | Rule-based service, brake, battery-check and device-offline alerts; inbox with acknowledge/resolve; email for critical alerts |
| Energy and cost | Daily energy roll-up, cost per km, charging spend, savings versus petrol, CO₂ avoided |
| Reports | Customisable reports exported as Excel, PDF or CSV |
| Administration | User and role management; tariff, fuel price and speed-limit settings |
| Interface | Responsive layout, light and dark themes |

## Screenshots

| | |
|---|---|
| ![Landing page](docs/screenshots/landing-dark.webp) | ![Route planner](docs/screenshots/route-planner-light.webp) |
| Landing page (dark theme) | Route planner: New Delhi → Jaipur with two charging stops |
| ![Vehicle detail](docs/screenshots/vehicle-detail-light.webp) | ![Driver behaviour](docs/screenshots/driver-behaviour-light.webp) |
| Vehicle page: last hour's route, speed and charge | Driver leaderboard and safety-vs-energy chart |
| ![Energy and cost](docs/screenshots/energy-cost-dark.webp) | ![Reports](docs/screenshots/reports-light.webp) |
| Energy and cost analysis (dark theme) | Report builder with live preview |

## Architecture

```mermaid
flowchart LR
    subgraph Sources
        SIM[Telemetry simulator]
        DEV[Vehicle devices]
    end
    subgraph App["Flask application (gunicorn)"]
        API["/api/v1 telemetry and battery API"]
        WEB[Web pages and blueprints]
        SVC[Services: alerts, maintenance rules, driver scoring, energy roll-up, reports]
        JOBS[Background jobs]
        ML[Battery SoH model]
    end
    DB[(PostgreSQL / SQLite)]
    EXT[OpenStreetMap: Nominatim and OSRM]
    SMTP[SMTP email]

    DEV -->|X-API-Key| API
    SIM --> SVC
    API --> SVC
    WEB --> SVC
    JOBS --> SVC
    SVC --> DB
    WEB --> ML
    WEB --> EXT
    SVC --> SMTP
```

- **Application factory and blueprints:** one blueprint per feature area, with business logic in
  `app/services/` so pages, the API, CLI commands and background jobs share the same code.
- **Telemetry path:** the simulator and the device API both call the same `record_reading()`
  function, which stores the reading and evaluates the battery alert rules. Everything
  downstream works the same whichever source the data came from.
- **Background work:** the simulator and periodic jobs (maintenance rules, energy roll-up,
  telemetry pruning, alert emails) run as threads in the web process. Each is also available as
  a CLI command, so an external scheduler can run them instead.
- **Database:** SQLAlchemy 2 models with Alembic migrations; SQLite for development and
  PostgreSQL in production. The test suite runs against both in CI.

**Tech stack:** Python 3.13 · Flask 3 · SQLAlchemy 2 · Flask-Migrate · Flask-Login · Flask-WTF ·
pandas · scikit-learn · ReportLab · openpyxl · Bootstrap 5 · Chart.js · Leaflet · gunicorn ·
PostgreSQL · Docker · GitHub Actions

## Getting started

### Run locally

Requires Python 3.11 or newer.

```bash
python -m venv .venv
source .venv/bin/activate          # Windows PowerShell: .\.venv\Scripts\Activate.ps1
pip install -r requirements-dev.txt
cp .env.example .env               # Windows PowerShell: Copy-Item .env.example .env
flask db upgrade                   # create the database tables
flask create-admin                 # first administrator (prompts for details)
flask seed-vehicles                # optional: demo EVs driven by the simulator
flask run                          # http://127.0.0.1:5000
```

Without SMTP settings, verification and password-reset links are printed in the terminal
running `flask run`.

### Run with Docker

Runs the production image with PostgreSQL, the same setup as the hosted deployment:

```bash
docker compose up --build
```

Open http://localhost:8000 and log in as `admin@example.com` / `ChangeMe123`. Change these by
setting `ADMIN_EMAIL` and `ADMIN_PASSWORD` in your shell or in a `.env` file before the first
start. Demo drivers and vehicles are added automatically. `docker compose down -v` removes the
containers and the database volume.

## Configuration

Settings are read from environment variables (or `.env`); see [`.env.example`](.env.example)
for the full list with comments.

| Variable | Default | Purpose |
|---|---|---|
| `APP_ENV` | `development` | `development`, `production` or `testing` |
| `SECRET_KEY` | — | Required in production; signs sessions and email links |
| `DATABASE_URL` | SQLite in `instance/` | e.g. `postgresql://user:pass@host:5432/ev_fleet` |
| `MAIL_SERVER`, `MAIL_USERNAME`, `MAIL_PASSWORD` | empty | SMTP for emails; empty prints them to the log |
| `SIMULATOR_ENABLED` | `true` | Built-in telemetry simulator |
| `SIMULATOR_TIME_SCALE` | `3` | 3 is near real time; 12 drains batteries visibly for demos |
| `BACKGROUND_JOBS_ENABLED` | `true` | Maintenance rules, energy roll-up, pruning, alert emails |
| `ALERT_EMAILS_ENABLED` | `false` | Email managers about new critical alerts |
| `TELEMETRY_RETENTION_DAYS` | `7` | Raw readings kept; daily energy totals are kept permanently |
| `ADMIN_EMAIL`, `ADMIN_PASSWORD` | empty | Admin created on start-up by `flask bootstrap` (Docker/Render) |
| `SEED_DEMO_DATA` | `false` | Add demo drivers and vehicles to an empty fleet on start-up |

Electricity tariff, charging efficiency, petrol price and mileage, and the fleet speed limit
can also be changed by an admin on the Settings page.

## Deployment

The repository includes a [Render](https://render.com) Blueprint ([`render.yaml`](render.yaml))
for the Docker image and a managed PostgreSQL database:

1. In the Render dashboard choose **New → Blueprint** and select this repository.
2. Enter `ADMIN_EMAIL` and `ADMIN_PASSWORD` for the first administrator, and SMTP settings if
   you want emails sent (otherwise they appear in the service log).
3. Render builds the image. On every start the container runs `flask db upgrade` and
   `flask bootstrap` (admin and demo data, both idempotent) before starting gunicorn.

`SECRET_KEY` is generated by Render, and `/healthz` is used as the health check. On the free
plan the service sleeps after about 15 minutes without traffic (the simulator pauses with it),
and the free database expires after 30 days.

The image works on any container platform: it runs as a non-root user, listens on `$PORT`
(default 8000) and has a built-in health check.

## API

Versioned JSON API under `/api/v1`. Errors use a consistent shape:
`{"error": "...", "message": "...", "details": [...]}`.

| Method | Path | Auth | Purpose |
|---|---|---|---|
| `POST` | `/api/v1/telemetry` | `X-API-Key` (per vehicle) | Ingest one reading or a batch of up to 100 |
| `POST` | `/api/v1/predict/battery` | none (stateless) | Battery state-of-health prediction, single or batch |
| `GET` | `/healthz` | none | Liveness and database check |

A Postman collection with example requests and tests is in
[`postman/`](postman/ev-fleet-monitoring.postman_collection.json). Set `baseUrl` and `apiKey`
(issue a device key on the vehicle page or with `flask rotate-api-key <plate>`). It also runs
headless:

```bash
npx newman run postman/ev-fleet-monitoring.postman_collection.json \
  --env-var baseUrl=http://127.0.0.1:5000 --env-var apiKey=evk_...
```

## Testing and quality

```bash
pytest --cov            # unit and integration tests with coverage
ruff check .            # lint
ruff format --check .   # formatting
```

Set `TEST_DATABASE_URL` to run the suite against PostgreSQL instead of in-memory SQLite.

GitHub Actions ([`.github/workflows/ci.yml`](.github/workflows/ci.yml)) runs on every push and
pull request:

1. **Lint:** ruff checks and formatting.
2. **Tests:** the full suite on SQLite and on PostgreSQL 17, failing below 90% coverage.
3. **End to end:** builds the Docker image, starts it with PostgreSQL via Docker Compose and
   runs the Postman collection against it.

External services (Nominatim, OSRM, SMTP) are mocked in tests.

## Modules in detail

### Users and roles

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

### Vehicles and real-time monitoring

Managers register EVs (make, model, registration number, battery size, efficiency, assigned
driver) and keep a service history for each. Every vehicle reports telemetry: location, speed,
battery charge (SoC), charging state, battery temperature and odometer. That data drives:

- **Live map** (`/monitoring/live`): all vehicles on OpenStreetMap, refreshed every 5 seconds,
  coloured by state (moving, idle, charging, offline) with low-battery warnings.
- **Vehicle page**: live figures, the last hour's route, and speed/battery charts.
- **Overview**: fleet totals (online, charging, average charge, low battery).

#### Where telemetry comes from

| Source | When to use | How |
|---|---|---|
| Built-in simulator | No real devices (default) | Runs inside the web server. Vehicles marked "simulate" drive real road loops (OSRM geometry in `data/sim_routes.json`, rebuilt by `python -m scripts.build_sim_routes`) through Delhi, Bengaluru, Mumbai, Kolkata or Hyderabad, drain and recharge their batteries, and each has its own driving style. |
| Device API | Real telematics devices | `POST /api/v1/telemetry` with the vehicle's API key (shown once when the vehicle is registered). |

Simulator settings in `.env`: `SIMULATOR_ENABLED`, `SIMULATOR_INTERVAL_SECONDS` (default 5),
`SIMULATOR_TIME_SCALE` (default 3, near real time; use 12 for a fast demo where batteries
drain visibly). Event rates are scaled to the tick length, so driver scores don't depend on it. To run it as
a separate process instead, set `SIMULATOR_ENABLED=false` and run `flask simulate`.

#### Telemetry API

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

### Route planner

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

### Battery health and alerts

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

### Driver behaviour

`/drivers` (managers: fleet leaderboard; drivers: their own scorecard) analyses telemetry for
today, the last 7 days or the last 30 days:

- **Events:** harsh braking (≤ −3.5 m/s², about 0.35 g), harsh acceleration (≥ 3.0 m/s²) and
  speeding (above `SPEED_LIMIT_KMH`, default 80), counted **per 100 km** so drivers who cover
  more distance aren't penalised.
- **Score** = 100 − (4 × braking + 3 × acceleration + 2 × speeding) per 100 km. Good ≥ 85,
  Fair ≥ 70, otherwise "needs coaching". At least 5 km of driving is needed for a score.
- **Energy impact:** energy actually used (from battery-charge drops while driving) compared
  with what the vehicle's rated efficiency predicts for the same distance, plus the extra cost
  per 100 km at `ENERGY_TARIFF_INR_PER_KWH`. Comparing against each vehicle's own rating keeps
  vehicle size from skewing driver comparisons.
- Each reading records the driver assigned at that moment, so reassigning a vehicle doesn't
  move past driving to the new driver.
- The scorecard shows a daily trend, an event breakdown with a coaching tip, and recent events
  with map links.

### Maintenance alerts and the alerts inbox

Rules run automatically every 10 minutes in the background (or with `flask check-maintenance`):

| Alert | Rule |
|---|---|
| Service due | Routine service every 10,000 km or 12 months: warning when due (or within 500 km), critical 1,000 km past the interval. Adding a routine service record closes it. |
| Brake inspection | 20 or more harsh-braking events in 7 days; closes after a brake service or when events drop. |
| Battery check due | No battery health check for 180 days. |
| Device offline | An active vehicle has sent no data for 24 hours. |

`/alerts` lists all battery and maintenance alerts with filters (type, severity, vehicle) and a
history tab. Managers can **acknowledge** alerts or **resolve** them with a note. An alert
reopens automatically if its rule fires again. The sidebar shows the number of open alerts.

**Email notifications** (`ALERT_EMAILS_ENABLED=true`): managers and admins are emailed about new
critical alerts by the background jobs, at most once per vehicle and alert type every 6 hours.
`flask send-alert-emails` sends pending ones manually.

### Energy and cost analysis

`/analytics` covers the last 7, 30 or 90 days for the whole fleet or one vehicle (drivers see
their own vehicles):

| Figure | How it's calculated |
|---|---|
| Energy used | Battery-charge drops between readings while driving × battery capacity |
| Charging | Charge added while plugged in ÷ charging efficiency = electricity bought, and its cost |
| Energy cost | Energy used ÷ charging efficiency × tariff. Cost per km is based on *consumption*, so it doesn't swing with when vehicles happen to charge (standard fleet cost accounting). |
| Operating cost | Energy cost + service-record costs in the period |
| kWh/100 km vs rated | Each vehicle's consumption compared with its rated efficiency |
| Saved vs petrol | Petrol cost for the same distance (default 15 km/L at ₹105/L) minus energy cost |
| CO₂ avoided | Petrol at 2.31 kg/L minus grid electricity for the energy used at India's average 0.72 kg/kWh (Central Electricity Authority) |

**Storage:** a background job rolls telemetry up into one `daily_energy` row per vehicle per
local (IST) day every hour, and the page refreshes today's row every few minutes. The history
survives when raw telemetry is pruned, and long periods load quickly. Each day stores the tariff
that applied, so changing the tariff doesn't rewrite past costs. `flask rollup-energy --days N`
rebuilds past days.

**Settings** (`/admin/settings`, admins): electricity tariff, charging efficiency, petrol price,
comparable petrol mileage and the fleet speed limit. Defaults come from `.env`; changes apply
immediately.

### Reports

`/reports` builds customisable reports: choose the report, a date range (up to a year), the
vehicles, the columns and a title, preview it, then download it.

| Report | Contents |
|---|---|
| Fleet summary | Per vehicle: distance, energy, kWh/100 km vs rated, energy and maintenance cost, cost/km, alerts, latest battery health |
| Energy & cost by day | Daily rows per vehicle from the energy roll-up |
| Driver behaviour | Score, harsh events, events per 100 km and energy impact per driver |
| Alerts | Every alert in the period with severity, acknowledgement and outcome |
| Service history | Maintenance work and costs |

Formats:
- **Excel (.xlsx):** styled header, filters, frozen header row, number and ₹ formats, and a summary sheet.
- **PDF:** title, period, author, summary figures and the table; landscape for wide reports;
  page numbers; bundled DejaVu Sans font so ₹ renders everywhere.
- **CSV:** UTF-8 with BOM so Excel shows ₹ correctly.

Reports respect the same access rules as the pages (drivers: their own vehicles and driving).
Text cells beginning with `=`, `+`, `-` or `@` are prefixed with `'` in CSV and Excel files to
block spreadsheet formula injection. The builder uses GET parameters, so a report's URL can be
bookmarked and the downloads always match the preview.

### User interface

The shared layout (`app/templates/base.html`) has a collapsible sidebar grouped by task, a top
bar with the alerts bell, theme switch and user menu, and a separate public layout for the
landing and sign-in pages.

- **Light and dark themes.** The first visit follows the operating system's setting, and the
  choice is remembered in the browser. Colours are defined once as CSS variables in
  `app/static/css/app.css`; charts and maps restyle themselves when the theme changes
  (`app/static/js/app.js`).
- **Responsive.** On small screens the sidebar becomes a slide-out menu.
- **Maps** use OpenStreetMap tiles (no API key). In dark mode only the base map is darkened, so
  vehicle markers keep their status colours.
- **Live motion.** Simulated vehicles drive along real road geometry, and the browser animates
  each marker smoothly between updates.

Screenshots can be regenerated with `python -m scripts.screenshots` (uses Playwright with the
installed Google Chrome).

## Data sources

Both datasets are real and rebuilt from their official sources by scripts in `scripts/`.
Downloads go to `data/raw/`, which git ignores; the cleaned outputs in `data/` are committed.

| Dataset | Source | Used for | Rebuild |
|---|---|---|---|
| `data/charging_stations_india.csv` | **Bureau of Energy Efficiency (Ministry of Power, Govt. of India)**, *EV Public Charging Stations Data till 26 October 2025*, [beeindia.gov.in](https://www.beeindia.gov.in/WriteReadData/RTF1984/EV_PCS_Data_29277.pdf) | Route planner | `python -m scripts.build_charging_stations` |
| `data/battery_cycles_nasa.csv` | **NASA Ames Prognostics Center of Excellence**, *Li-ion Battery Aging Data Set*, B. Saha & K. Goebel (2007), [NASA PCoE data repository](https://www.nasa.gov/intelligent-systems-division/discovery-and-systems-health/pcoe/pcoe-data-set-repository/) | Battery health model | `python -m scripts.build_battery_dataset`, then `python -m ml.train_battery_model` |

The PDF font (DejaVu Sans, `app/static/fonts/`) is redistributed under its free licence,
included alongside it.

Each script prints a cleaning report: duplicates, invalid coordinates or measurements, and
normalised connector types. These replace the datasets of the first version (a synthetic battery
sheet and an unofficial station list), which is available in the first commit of the history.

## Project structure

```
app/                  Flask application
  __init__.py         application factory
  config.py           settings per environment, read from environment variables
  models/             database tables (SQLAlchemy)
  auth/ admin/        accounts, roles, user management, settings
  vehicles/ monitoring/ routing/ battery/ drivers/ alerts/ analytics/ reports/
                      one blueprint per feature area
  api/                JSON API (/api/v1)
  services/           business logic shared by pages, API, CLI and background jobs
  templates/ static/  Jinja templates, CSS, JavaScript, fonts
  cli.py              flask commands (create-admin, bootstrap, seed-vehicles, simulate, ...)
migrations/           database migrations (Alembic)
ml/                   model training script and model card
models/               trained battery model and its metadata
data/                 cleaned datasets used by the app
scripts/              rebuild the datasets from their sources; page screenshots
tests/                pytest suite
docker/               container entrypoint
postman/              API collection
docs/screenshots/     images used in this README
```

## Design decisions and limitations

- **Simulated telemetry.** There are no physical vehicles, so a simulator drives demo EVs along
  real road loops in five Indian cities. Real devices use the same ingest path through the
  telemetry API, so replacing the simulator needs no changes elsewhere.
- **In-process background work.** Simple to deploy on a single instance. To scale out, set
  `SIMULATOR_ENABLED=false` and `BACKGROUND_JOBS_ENABLED=false`, run the equivalent CLI
  commands from a scheduler or worker, and increase the gunicorn workers.
- **Polling, not push.** The live map polls a JSON endpoint every 5 seconds. That is enough for
  this fleet size; WebSockets or server-sent events would suit larger fleets.
- **Public routing services.** Nominatim's and OSRM's public servers have usage limits; point
  `NOMINATIM_URL` and `OSRM_URL` at self-hosted instances for production traffic.
- **Battery model scope.** The SoH model is trained on laboratory cell data. It demonstrates the
  method with honest error estimates, but a production model would need pack-level data from
  the fleet's own vehicles. See [`ml/MODEL_CARD.md`](ml/MODEL_CARD.md).

## License

Released under the [MIT License](LICENSE). The charging-station and battery datasets remain
subject to their publishers' terms, and the bundled DejaVu fonts to their own licence.
