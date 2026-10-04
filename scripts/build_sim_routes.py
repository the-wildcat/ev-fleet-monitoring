"""Build data/sim_routes.json: real road loops for the telemetry simulator.

For each city, OSRM routes a closed loop through its landmark waypoints
(app/services/simulator.py CITY_LOOPS), and the road geometry is saved so the simulator
follows actual streets without needing the internet at run time.

Usage (from the project root):
    python -m scripts.build_sim_routes
"""

from __future__ import annotations

import json
import sys
from pathlib import Path

import requests

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

from app.services.simulator import CITY_LOOPS  # noqa: E402

OSRM_URL = "https://router.project-osrm.org"
OUTPUT = ROOT / "data" / "sim_routes.json"


def road_loop(waypoints: list[tuple[float, float]]) -> tuple[list[list[float]], float]:
    stops = [*waypoints, waypoints[0]]  # back to the start: a closed loop
    coords = ";".join(f"{lon},{lat}" for lat, lon in stops)
    resp = requests.get(
        f"{OSRM_URL}/route/v1/driving/{coords}",
        params={"overview": "full", "geometries": "geojson"},
        headers={"User-Agent": "EVFleetMonitor/1.0 (Infosys Springboard project)"},
        timeout=60,
    )
    resp.raise_for_status()
    route = resp.json()["routes"][0]
    points = [[round(lat, 6), round(lon, 6)] for lon, lat in route["geometry"]["coordinates"]]
    return points, route["distance"] / 1000


def main() -> None:
    routes = {}
    for city, waypoints in CITY_LOOPS.items():
        points, km = road_loop(waypoints)
        routes[city] = points
        print(f"{city}: {len(points)} road points, {km:.0f} km loop")
    OUTPUT.write_text(json.dumps(routes, separators=(",", ":")), encoding="utf-8")
    print(f"Wrote {OUTPUT.relative_to(ROOT)} ({OUTPUT.stat().st_size / 1024:.0f} KB)")


if __name__ == "__main__":
    main()
