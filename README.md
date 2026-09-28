# EV Fleet Monitoring

Real-Time EV Fleet Monitoring and Predictive Analytics Solution, an Infosys Springboard
internship project being rebuilt module by module to production standards.

> **Status:** Phase 0 (foundation) and Module 1 (authentication and roles) are complete.
> Modules 2–6 are in progress; see the roadmap below. Full documentation will be added in the
> delivery phase.

## Quick start (Windows / PowerShell)

```powershell
python -m venv .venv
.\.venv\Scripts\Activate.ps1
pip install -r requirements-dev.txt
Copy-Item .env.example .env      # then edit values if needed
flask db upgrade                 # create/update the database tables
flask create-admin               # create the first administrator (prompts for details)
flask run                        # open http://127.0.0.1:5000
pytest                           # run the test suite
ruff check .                     # lint
```

In Git Bash on Windows, activate with `source .venv/Scripts/activate`. On macOS/Linux, use
`source .venv/bin/activate`. In both, copy the env file with `cp .env.example .env`.

## Users and roles

| Role | How you get it | Can do |
|---|---|---|
| Driver | Default for every sign-up | Their own vehicle and driving data (from Module 2) |
| Fleet Manager | Promoted by an admin | Whole fleet: vehicles, alerts, analytics, reports |
| Admin | `flask create-admin`, or promoted | Everything, plus user management (`/admin/users`) |

Sign-up requires email verification. Without SMTP settings in `.env`, the verification and
password-reset links are printed in the terminal running `flask run`. Other CLI commands:
`flask set-role <email> <admin|fleet_manager|driver>`.

Security measures: passwords hashed with scrypt; CSRF protection on every form; account lockout
after 5 failed logins (15 minutes); signed, expiring, single-use reset links; no account
enumeration through sign-up, login or reset; safe post-login redirects.

## Project layout

```
app/            Flask application (app factory, blueprints, templates, static files)
  config.py     settings per environment, read from environment variables
  extensions.py database, migrations, login and CSRF extensions
  models/       database tables (SQLAlchemy)
  auth/         sign-up, email verification, login/logout, password reset, profile, roles
  admin/        user management for administrators
  services/     shared services (email)
  main/         overview page and /healthz endpoint
  cli.py        `flask create-admin`, `flask set-role`
migrations/     database schema versions (Alembic via Flask-Migrate)
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
| 2 | EV registration and real-time monitoring | Planned |
| 3 | Route optimisation and battery health (ML) | Planned |
| 4 | Driver behaviour and maintenance alerts | Planned |
| 5 | Energy and cost analysis | Planned |
| 6 | Report generation | Planned |
| 7 | Admin, Docker, CI, deployment | Planned |
