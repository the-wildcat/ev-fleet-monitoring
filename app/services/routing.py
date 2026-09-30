"""Route planning with charging stops.

1. Geocode the origin/destination with OpenStreetMap Nominatim (or accept "lat, lon").
2. Get the road route from OSRM (distance, duration, geometry). If OSRM is unreachable,
   fall back to a straight-line estimate and say so.
3. Find charging stations within a corridor around the route (vectorised haversine).
4. Plan stops greedily: drive as far as the battery safely allows (keeping a reserve), charge
   at the farthest reachable station, repeat. "Farthest reachable" minimises the number of
   stops when charging to the same level each time.
"""

from __future__ import annotations

import logging
import re
from dataclasses import dataclass, field
from functools import lru_cache
from pathlib import Path

import numpy as np
import pandas as pd
import requests

log = logging.getLogger(__name__)
EARTH_RADIUS_KM = 6371.0
INDIA_BBOX = {"lat": (6.0, 37.5), "lon": (68.0, 98.0)}
# Spelling variants found in the station dataset -> one display name.
_CITY_ALIASES = {
    "Bangalore": "Bengaluru",
    "Banglore": "Bengaluru",
    "Bengaluru Urban": "Bengaluru",
    "Gurgaon": "Gurugram",
}
_LATLON = re.compile(r"^\s*(-?\d+(?:\.\d+)?)\s*,\s*(-?\d+(?:\.\d+)?)\s*$")


class RoutingError(Exception):
    """A problem the user can fix (place not found, impossible trip, bad input)."""


# --- charging stations ------------------------------------------------------------------------


@dataclass(frozen=True)
class Stations:
    names: np.ndarray
    addresses: np.ndarray
    cities: np.ndarray
    lat: np.ndarray
    lon: np.ndarray
    cleaning_report: dict

    def __len__(self) -> int:
        return len(self.lat)


def _in_india(lat: pd.Series, lon: pd.Series) -> pd.Series:
    return lat.between(*INDIA_BBOX["lat"]) & lon.between(*INDIA_BBOX["lon"])


@lru_cache(maxsize=4)
def load_stations(path: Path) -> Stations:
    """Load and clean the charging-station dataset (cached per file).

    The raw file has duplicate rows, some swapped latitude/longitude pairs and a few corrupted
    coordinates; swaps are repaired, anything else outside India is dropped.
    """
    df = pd.read_csv(path)
    report = {"raw_rows": len(df)}

    swapped = ~_in_india(df.latitude, df.longitude) & _in_india(df.longitude, df.latitude)
    df.loc[swapped, ["latitude", "longitude"]] = df.loc[swapped, ["longitude", "latitude"]].values
    report["swapped_fixed"] = int(swapped.sum())

    valid = _in_india(df.latitude, df.longitude)
    report["invalid_dropped"] = int((~valid).sum())
    df = df[valid]

    before = len(df)
    df = df.drop_duplicates(subset=["name", "latitude", "longitude"])
    report["duplicates_dropped"] = before - len(df)

    df = df.assign(
        address=df.address.fillna(""),
        city=df.city.astype(str).str.strip().str.title().replace(_CITY_ALIASES),
    )
    report["clean_rows"] = len(df)
    log.info("Charging stations loaded: %s", report)
    return Stations(
        names=df.name.to_numpy(),
        addresses=df.address.to_numpy(),
        cities=df.city.to_numpy(),
        lat=df.latitude.to_numpy(dtype=float),
        lon=df.longitude.to_numpy(dtype=float),
        cleaning_report=report,
    )


def haversine_matrix(lat1, lon1, lat2, lon2) -> np.ndarray:
    """Pairwise great-circle distances (km) between point sets 1 (rows) and 2 (columns)."""
    lat1, lon1, lat2, lon2 = (
        np.radians(np.asarray(a, dtype=float)) for a in (lat1, lon1, lat2, lon2)
    )
    dlat = lat2[None, :] - lat1[:, None]
    dlon = lon2[None, :] - lon1[:, None]
    h = (
        np.sin(dlat / 2) ** 2
        + np.cos(lat1)[:, None] * np.cos(lat2)[None, :] * np.sin(dlon / 2) ** 2
    )
    return 2 * EARTH_RADIUS_KM * np.arcsin(np.sqrt(np.clip(h, 0, 1)))


# --- geocoding and routing (external services) ------------------------------------------------


@dataclass(frozen=True)
class Place:
    lat: float
    lon: float
    label: str


def parse_latlon(text: str) -> Place | None:
    match = _LATLON.match(text or "")
    if not match:
        return None
    lat, lon = float(match.group(1)), float(match.group(2))
    if not (-90 <= lat <= 90 and -180 <= lon <= 180):
        return None
    return Place(lat, lon, f"{lat:.5f}, {lon:.5f}")


@lru_cache(maxsize=512)
def _nominatim_search(base_url: str, query: str, user_agent: str, timeout: float) -> Place | None:
    resp = requests.get(
        f"{base_url}/search",
        params={"q": query, "format": "jsonv2", "limit": 1, "countrycodes": "in"},
        headers={"User-Agent": user_agent},
        timeout=timeout,
    )
    resp.raise_for_status()
    results = resp.json()
    if not results:
        return None
    top = results[0]
    return Place(float(top["lat"]), float(top["lon"]), top.get("display_name", query))


def geocode(query: str, *, base_url: str, user_agent: str, timeout: float) -> Place:
    """Turn a place name (or "lat, lon") into coordinates."""
    query = (query or "").strip()
    if not query:
        raise RoutingError("Enter a place name or coordinates.")
    direct = parse_latlon(query)
    if direct:
        return direct
    try:
        place = _nominatim_search(base_url, query.lower(), user_agent, timeout)
    except requests.RequestException as err:
        log.warning("Geocoding failed for %r: %s", query, err)
        raise RoutingError(
            "The place-search service is unreachable right now. "
            "Try again, or enter coordinates like 28.6139, 77.2090."
        ) from err
    if place is None:
        raise RoutingError(f"Couldn't find “{query}” in India. Try a more specific name.")
    return place


@dataclass
class Route:
    distance_km: float
    duration_min: float
    coords: list[tuple[float, float]]  # (lat, lon)
    source: str  # "osrm" or "estimate"


def road_routes(
    origin: Place, dest: Place, *, base_url: str, timeout: float, alternatives: int = 2
) -> list[Route]:
    """Road routes from OSRM (fastest first, plus alternatives), or a straight-line estimate
    if OSRM is unavailable."""
    url = f"{base_url}/route/v1/driving/{origin.lon},{origin.lat};{dest.lon},{dest.lat}"
    try:
        resp = requests.get(
            url,
            params={"overview": "full", "geometries": "geojson", "alternatives": alternatives},
            timeout=timeout,
        )
        resp.raise_for_status()
        data = resp.json()
    except (requests.RequestException, ValueError) as err:
        log.warning("OSRM unavailable, using straight-line estimate: %s", err)
        return [estimated_route(origin, dest)]
    if data.get("code") != "Ok" or not data.get("routes"):
        raise RoutingError("No drivable road route was found between these places.")
    return [
        Route(
            r["distance"] / 1000,
            r["duration"] / 60,
            [(lat, lon) for lon, lat in r["geometry"]["coordinates"]],
            "osrm",
        )
        for r in data["routes"]
    ]


def estimated_route(origin: Place, dest: Place) -> Route:
    """Straight line x 1.3 (typical road detour factor) at an average 50 km/h."""
    straight = float(haversine_matrix([origin.lat], [origin.lon], [dest.lat], [dest.lon])[0, 0])
    steps = np.linspace(0, 1, 60)
    coords = [
        (origin.lat + (dest.lat - origin.lat) * t, origin.lon + (dest.lon - origin.lon) * t)
        for t in steps
    ]
    distance = straight * 1.3
    return Route(distance, distance / 50 * 60, coords, "estimate")


# --- charging-stop planning -------------------------------------------------------------------


@dataclass
class EVParams:
    capacity_kwh: float
    efficiency_km_per_kwh: float
    start_soc_pct: float
    reserve_pct: float = 15.0  # never plan to arrive below this
    target_soc_pct: float = 80.0  # charge up to this at each stop (DC charging slows above ~80%)
    charger_kw: float = 50.0  # assumed charger power (the dataset has no power ratings)

    def km_for(self, soc_pct: float) -> float:
        return self.capacity_kwh * soc_pct / 100 * self.efficiency_km_per_kwh

    def soc_used(self, km: float) -> float:
        return km / self.efficiency_km_per_kwh / self.capacity_kwh * 100


@dataclass
class Stop:
    number: int
    name: str
    address: str
    city: str
    lat: float
    lon: float
    route_km: float  # position along the route
    detour_km: float  # one-way distance from the route
    arrive_soc_pct: float
    depart_soc_pct: float
    charge_kwh: float
    charge_min: float


@dataclass
class TripPlan:
    origin: Place
    destination: Place
    route: Route
    ev: EVParams
    stops: list[Stop] = field(default_factory=list)
    arrive_soc_pct: float | None = None
    feasible: bool = True
    problem: str | None = None
    nearby: list[dict] = field(default_factory=list)  # stations in the corridor (for the map)

    @property
    def charging_min(self) -> float:
        return sum(s.charge_min for s in self.stops)

    @property
    def detour_km(self) -> float:
        return sum(2 * s.detour_km for s in self.stops)

    @property
    def total_km(self) -> float:
        return self.route.distance_km + self.detour_km

    @property
    def total_min(self) -> float:
        avg_kmh = self.route.distance_km / max(self.route.duration_min / 60, 1e-6)
        return self.route.duration_min + self.charging_min + self.detour_km / avg_kmh * 60

    @property
    def energy_kwh(self) -> float:
        return self.total_km / self.ev.efficiency_km_per_kwh


def _route_positions(coords: list[tuple[float, float]], total_km: float, max_points: int = 400):
    """Downsample the route and compute each point's distance from the start (km)."""
    pts = np.asarray(coords, dtype=float)
    if len(pts) > max_points:
        pts = pts[np.linspace(0, len(pts) - 1, max_points).astype(int)]
    seg = np.array(
        [
            haversine_matrix([a[0]], [a[1]], [b[0]], [b[1]])[0, 0]
            for a, b in zip(pts[:-1], pts[1:], strict=True)
        ]
    )
    cum = np.concatenate([[0.0], np.cumsum(seg)])
    # Scale so the last point matches the router's distance (geometry is simplified).
    if cum[-1] > 0:
        cum *= total_km / cum[-1]
    return pts, cum


def plan_trip(
    origin: Place,
    dest: Place,
    route: Route,
    ev: EVParams,
    stations: Stations,
    corridor_km: float = 5.0,
) -> TripPlan:
    if ev.target_soc_pct <= ev.reserve_pct:
        raise RoutingError("Charge target must be higher than the reserve.")
    plan = TripPlan(origin=origin, destination=dest, route=route, ev=ev)

    pts, cum = _route_positions(route.coords, route.distance_km)
    dist = haversine_matrix(stations.lat, stations.lon, pts[:, 0], pts[:, 1])
    nearest = dist.argmin(axis=1)
    off_route = dist[np.arange(len(stations)), nearest]
    in_corridor = np.where(off_route <= corridor_km)[0]
    candidates = sorted(
        ((float(cum[nearest[i]]), float(off_route[i]), int(i)) for i in in_corridor),
        key=lambda c: c[0],
    )
    plan.nearby = [
        {
            "name": str(stations.names[i]),
            "lat": float(stations.lat[i]),
            "lon": float(stations.lon[i]),
        }
        for _, _, i in candidates[:400]
    ]

    position, soc = 0.0, ev.start_soc_pct
    while True:
        reach_km = ev.km_for(soc - ev.reserve_pct)  # can be negative if below the reserve
        remaining = route.distance_km - position
        if remaining <= reach_km:
            plan.arrive_soc_pct = round(soc - ev.soc_used(remaining), 1)
            return plan

        reachable = [
            c for c in candidates if c[0] > position + 1.0 and (c[0] - position + c[1]) <= reach_km
        ]
        if not reachable:
            plan.feasible = False
            if soc <= ev.reserve_pct:
                plan.problem = (
                    f"Battery is at {soc:.0f}%, at or below the {ev.reserve_pct:.0f}% reserve. "
                    "Charge before departing."
                )
            else:
                plan.problem = (
                    f"No charging station within reach after {position:.0f} km "
                    f"(range ≈ {max(reach_km, 0):.0f} km keeping a {ev.reserve_pct:.0f}% "
                    "reserve). Try a higher charge target or a wider search corridor."
                )
            return plan

        stop_km, detour, idx = max(reachable, key=lambda c: c[0])
        arrive = soc - ev.soc_used(stop_km - position + detour)
        added_kwh = ev.capacity_kwh * (ev.target_soc_pct - arrive) / 100
        plan.stops.append(
            Stop(
                number=len(plan.stops) + 1,
                name=str(stations.names[idx]),
                address=str(stations.addresses[idx]),
                city=str(stations.cities[idx]),
                lat=float(stations.lat[idx]),
                lon=float(stations.lon[idx]),
                route_km=round(stop_km, 1),
                detour_km=round(detour, 2),
                arrive_soc_pct=round(arrive, 1),
                depart_soc_pct=ev.target_soc_pct,
                charge_kwh=round(added_kwh, 1),
                charge_min=round(added_kwh / ev.charger_kw * 60, 0),
            )
        )
        # Back on the route at the station's position, minus the energy to return from it.
        position = stop_km
        soc = ev.target_soc_pct - ev.soc_used(detour)


@dataclass
class PlanResult:
    best: TripPlan
    alternatives: list[TripPlan]  # the other routes, for comparison
    # Set when no route works at the requested charge target but would at 100%.
    full_charge_suggestion: TripPlan | None = None


def choose_plan(
    origin: Place,
    dest: Place,
    routes: list[Route],
    ev: EVParams,
    stations: Stations,
    corridor_km: float = 5.0,
) -> PlanResult:
    """Plan every route and recommend the quickest feasible one (charging time included)."""
    plans = [plan_trip(origin, dest, r, ev, stations, corridor_km) for r in routes]
    feasible = sorted((p for p in plans if p.feasible), key=lambda p: p.total_min)
    if feasible:
        best = feasible[0]
        return PlanResult(best, [p for p in plans if p is not best])

    best = plans[0]  # nothing works: show the fastest route and explain why
    suggestion = None
    if ev.target_soc_pct < 100:
        full = EVParams(**{**ev.__dict__, "target_soc_pct": 100.0})
        retry = [plan_trip(origin, dest, r, full, stations, corridor_km) for r in routes]
        workable = sorted((p for p in retry if p.feasible), key=lambda p: p.total_min)
        suggestion = workable[0] if workable else None
    return PlanResult(best, plans[1:], suggestion)
