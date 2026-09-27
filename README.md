# EV Fleet Monitoring

Real-Time EV Fleet Monitoring and Predictive Analytics Solution, an Infosys Springboard
internship project being rebuilt module by module to production standards.

> **Status:** Phase 0 (project foundation) is complete. Modules 1–6 are in progress; see the
> roadmap below. Full documentation will be added in the delivery phase.

## Quick start (Windows / PowerShell)

```powershell
python -m venv .venv
.\.venv\Scripts\Activate.ps1
pip install -r requirements-dev.txt
Copy-Item .env.example .env      # then edit values if needed
flask run                        # open http://127.0.0.1:5000
pytest                           # run the test suite
ruff check .                     # lint
```

On macOS/Linux, activate with `source .venv/bin/activate` and copy with `cp .env.example .env`.

## Project layout

```
app/            Flask application (app factory, blueprints, templates, static files)
  config.py     settings per environment, read from environment variables
  extensions.py database, migrations, login and CSRF extensions
  main/         overview page and /healthz endpoint
data/           datasets used by the app
legacy/         original submission, kept for reference until each module is rebuilt
tests/          pytest test suite
wsgi.py         entry point for `flask run` and gunicorn
```

## Roadmap

| Phase | Scope | Status |
|---|---|---|
| 0 | Project foundation: structure, config, base layout, tests | Done |
| 1 | User authentication, email verification, roles | Planned |
| 2 | EV registration and real-time monitoring | Planned |
| 3 | Route optimisation and battery health (ML) | Planned |
| 4 | Driver behaviour and maintenance alerts | Planned |
| 5 | Energy and cost analysis | Planned |
| 6 | Report generation | Planned |
| 7 | Admin, Docker, CI, deployment | Planned |
