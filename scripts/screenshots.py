"""Capture screenshots of every page in light and dark theme (for the README and UI review).

Starts the app on a throwaway database with demo users and vehicles, lets the simulator
drive for a while, then opens each page in Chrome via Playwright.

Usage (from the project root; needs Google Chrome installed):
    python -m scripts.screenshots [--out docs/screenshots] [--drive-seconds 60]
"""

from __future__ import annotations

import argparse
import os
import subprocess
import sys
import tempfile
import time
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
PORT = 5077
BASE = f"http://127.0.0.1:{PORT}"
ADMIN = ("admin@example.com", "DemoAdmin123")
DRIVERS = ["Ravi Kumar", "Priya Das", "Arjun Mehta", "Sara Khan"]

PAGES = [  # (file name, path, wait for selector)
    ("01-landing", "/", None),
    ("02-login", "/login", None),
    ("03-overview", "/", None),
    ("04-live-map", "/monitoring/live", ".leaflet-marker-pane path, .leaflet-interactive"),
    ("05-vehicles", "/vehicles/", None),
    ("06-vehicle-detail", "/vehicles/1", "canvas"),
    ("07-route-planner", "/routes/plan?demo=1", None),
    ("08-battery-health", "/battery/", None),
    ("09-driver-behaviour", "/drivers/?days=1", "canvas"),
    ("10-alerts", "/alerts/", None),
    ("11-energy-cost", "/analytics/?days=7", "canvas"),
    ("12-reports", "/reports/?type=fleet_summary", None),
    ("13-settings", "/admin/settings", None),
]


def flask(env: dict, *args: str) -> None:
    subprocess.run(
        [sys.executable, "-m", "flask", *args],
        cwd=ROOT,
        env=env,
        check=True,
        stdout=subprocess.DEVNULL,
        stderr=subprocess.DEVNULL,
    )


def prepare(env: dict) -> None:
    flask(env, "db", "upgrade")
    flask(env, "create-admin", "--email", ADMIN[0], "--name", "Fleet Admin", "--password", ADMIN[1])
    code = (
        "from app import create_app\n"
        "from app.extensions import db\n"
        "from app.models import Role, User\n"
        "app = create_app()\n"
        "with app.app_context():\n"
        f"    for n in {DRIVERS!r}:\n"
        "        email = n.split()[0].lower() + '@example.com'\n"
        "        u = User(name=n, email=email, role=Role.DRIVER, is_verified=True)\n"
        "        u.set_password('DemoDriver123'); db.session.add(u)\n"
        "    db.session.commit()\n"
    )
    subprocess.run(
        [sys.executable, "-c", code],
        cwd=ROOT,
        env=env,
        check=True,
        stdout=subprocess.DEVNULL,
        stderr=subprocess.DEVNULL,
    )
    flask(env, "seed-vehicles", "--count", "8")


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--out", default="docs/screenshots")
    parser.add_argument("--drive-seconds", type=int, default=60)
    args = parser.parse_args()
    out = ROOT / args.out
    out.mkdir(parents=True, exist_ok=True)

    from playwright.sync_api import sync_playwright

    with tempfile.TemporaryDirectory(ignore_cleanup_errors=True) as tmp:
        env = {
            **os.environ,
            "DATABASE_URL": f"sqlite:///{Path(tmp, 'demo.db').as_posix()}",
            "SIMULATOR_INTERVAL_SECONDS": "0.5",
            "SIMULATOR_TIME_SCALE": "60",
            "MAIL_SERVER": "",
            "ALERT_EMAILS_ENABLED": "false",
            "FLASK_DEBUG": "0",
        }
        prepare(env)
        server = subprocess.Popen(
            [sys.executable, "-m", "flask", "run", "--port", str(PORT), "--no-reload"],
            cwd=ROOT,
            env=env,
            stdout=subprocess.DEVNULL,
            stderr=subprocess.DEVNULL,
        )
        try:
            time.sleep(4)
            with sync_playwright() as p:
                browser = p.chromium.launch(channel="chrome")
                for theme in ("light", "dark"):
                    ctx = browser.new_context(
                        viewport={"width": 1440, "height": 900},
                        color_scheme=theme,
                        device_scale_factor=1,
                    )
                    page = ctx.new_page()
                    page.add_init_script(f"localStorage.setItem('ev-theme', '{theme}')")
                    for name, path, _ in PAGES[:2]:  # public pages
                        page.goto(BASE + path)
                        page.wait_for_load_state("networkidle")
                        page.screenshot(path=out / f"{name}-{theme}.png")
                    page.goto(BASE + "/login")
                    page.fill("#email", ADMIN[0])
                    page.fill("#password", ADMIN[1])
                    with page.expect_navigation():  # wait until the login has completed
                        page.click("#submit")
                    if theme == "light":  # let the simulator build up some driving first
                        time.sleep(args.drive_seconds)
                        flask(env, "rollup-energy", "--days", "1")
                        flask(env, "check-maintenance")
                    for name, path, wait_for in PAGES[2:]:
                        page.goto(BASE + path)
                        page.wait_for_load_state("networkidle")
                        if wait_for:
                            page.wait_for_selector(wait_for, timeout=15000)
                        page.wait_for_timeout(1200)  # chart animations / map tiles
                        page.screenshot(path=out / f"{name}-{theme}.png")
                    ctx.close()
                browser.close()
        finally:
            server.terminate()
            server.wait(timeout=15)  # release the database file before the temp dir is removed
    print(f"Saved screenshots to {out.relative_to(ROOT)}")


if __name__ == "__main__":
    main()
