# EV Fleet Monitoring

Real-Time EV Fleet Monitoring and Predictive Analytics Solution, an Infosys Springboard
internship project being rebuilt module by module to production standards.

> **Status:** all six modules in the project specification are complete (authentication and
> roles; EV registration and real-time monitoring; route optimisation and battery health;
> driver behaviour and maintenance alerts; energy and cost analysis; report generation).
> Deployment packaging (Docker, CI, hosting) is in progress; see the roadmap below.

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
| Built-in simulator | No real devices (default) | Runs inside the web server. Vehicles marked "simulate" drive real road loops (OSRM geometry in `data/sim_routes.json`, rebuilt by `python -m scripts.build_sim_routes`) through Delhi, Bengaluru, Mumbai, Kolkata or Hyderabad, drain and recharge their batteries, and each has its own driving style. |
| Device API | Real telematics devices | `POST /api/v1/telemetry` with the vehicle's API key (shown once when the vehicle is registered). |

Simulator settings in `.env`: `SIMULATOR_ENABLED`, `SIMULATOR_INTERVAL_SECONDS` (default 5),
`SIMULATOR_TIME_SCALE` (default 3, near real time; use 12 for a fast demo where batteries
drain visibly). Event rates are scaled to the tick length, so driver scores don't depend on it. To run it as
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

## Driver behaviour

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

## Maintenance alerts and the alerts inbox

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

## Energy and cost analysis

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

## Reports

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
normalised connector types. The original submission's datasets (a synthetic battery sheet and an
unofficial station list) are kept in `legacy/datasets/` for reference.

## User interface

The interface uses a shared layout (`app/templates/base.html`): a collapsible sidebar grouped by
task (Monitor, Analyse, Plan, Admin), a top bar with the alerts bell, theme switch and user menu,
and a public landing page with sign-up and log-in.

- **Light and dark themes.** The first visit follows the operating system's setting, and the toggle
  remembers the choice in the browser. Colours are defined once as CSS variables in
  `app/static/css/app.css`. Charts (Chart.js) and maps (Leaflet) restyle themselves when the theme
  changes (`app/static/js/app.js`).
- **Responsive.** On phones the sidebar becomes a slide-out menu.
- **Maps** use OpenStreetMap tiles (no API key). In dark mode only the base map is darkened, so
  the vehicle markers keep their status colours.
- **Live map motion.** Vehicles drive along real road geometry (`data/sim_routes.json`, built from
  OSRM by `scripts/build_sim_routes.py`). The browser animates each marker smoothly between the
  5-second updates.

Screenshots of every page in both themes can be regenerated with Playwright, which uses the
installed Google Chrome:

```powershell
python -m scripts.screenshots --out docs/screenshots
```

## Project layout

```
app/            Flask application (app factory, blueprints, templates, static files)
  config.py     settings per environment, read from environment variables
  extensions.py database, migrations, login and CSRF extensions
  models/       database tables (SQLAlchemy)
  auth/         sign-up, email verification, login/logout, password reset, profile, roles
  admin/        user management and system settings for administrators
  vehicles/     EV registration, details, service history, device API keys
  monitoring/   live fleet map and its JSON feed
  routing/      route planner page
  battery/      battery health page (ML checks, alerts)
  drivers/      driver behaviour leaderboard and scorecards
  alerts/       alerts inbox (acknowledge, resolve, history)
  analytics/    energy and cost analysis
  reports/      report builder and downloads
  api/          versioned JSON API (/api/v1): telemetry ingest, battery prediction
  services/     email, telemetry, simulator, alerts, routing, battery model, driving,
                maintenance rules, notifications, background jobs, energy roll-up, settings,
                report definitions and exporters (CSV/Excel/PDF)
  main/         landing page, overview dashboard and /healthz endpoint
  static/       shared CSS (design tokens, light/dark) and JS (theme, charts, map tiles)
  cli.py        create-admin, set-role, send-test-email, seed-vehicles, simulate,
                prune-telemetry, check-maintenance, send-alert-emails, rollup-energy
migrations/     database schema versions (Alembic via Flask-Migrate)
scripts/        rebuild datasets from their official sources; page screenshots
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
| 4 | Driver behaviour and maintenance alerts | Done |
| 5 | Energy and cost analysis | Done |
| 6 | Report generation | Done |
| 7 | Admin, Docker, CI, deployment | Planned |
