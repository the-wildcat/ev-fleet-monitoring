"""Route planning with charging stops.

1. Geocode the origin/destination with OpenStreetMap Nominatim (or accept "lat, lon").
2. Get the road route from OSRM (distance, duration, geometry). If OSRM is unreachable,
   fall back to a straight-line estimate and say so.
3. Find car-compatible charging stations within a corridor around the route (vectorised
   haversine), using India's official BEE station list with real charger power ratings.
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
# Power assumed for car chargers whose rating is missing in the source data (slow AC).
UNKNOWN_POWER_KW = 7.4
_LATLON = re.compile(r"^\s*(-?\d+(?:\.\d+)?)\s*,\s*(-?\d+(?:\.\d+)?)\s*$")


class RoutingError(Exception):
    """A problem the user can fix (place not found, impossible trip, bad input)."""


# --- charging stations ------------------------------------------------------------------------


@dataclass(frozen=True)
class Stations:
    names: np.ndarray
    operators: np.ndarray
    addresses: np.ndarray
    cities: np.ndarray
    lat: np.ndarray
    lon: np.ndarray
    power_kw: np.ndarray  # fastest charger at the site that a car can use
    connector_types: np.ndarray

    def __len__(self) -> int:
        return len(self.lat)


@lru_cache(maxsize=4)
def load_stations(path: Path) -> Stations:
    """Car-compatible stations from data/charging_stations_india.csv (cached per file).

    The CSV is built and cleaned from India's official BEE charging-station list by
    scripts/build_charging_stations.py. Stations that only have 2/3-wheeler (LEV) chargers
    are excluded here because cars can't use them.
    """
    df = pd.read_csv(path)
    in_india = df.lat.between(*INDIA_BBOX["lat"]) & df.lon.between(*INDIA_BBOX["lon"])
    df = df[df.car_compatible.astype(bool) & in_india]
    log.info("Loaded %d car-compatible charging stations from %s", len(df), Path(path).name)
    return Stations(
        names=df.name.to_numpy(),
        operators=df.operator.to_numpy(),
        addresses=df.address.fillna("").to_numpy(),
        cities=df.city.fillna("").to_numpy(),
        lat=df.lat.to_numpy(dtype=float),
        lon=df.lon.to_numpy(dtype=float),
        power_kw=df.max_car_kw.fillna(UNKNOWN_POWER_KW).to_numpy(dtype=float),
        connector_types=df.connector_types.fillna("").to_numpy(),
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
    max_charge_kw: float = 50.0  # the car's own charging limit; actual = min(car, charger)
    min_charger_kw: float = 25.0  # skip slower chargers when choosing stops (25 kW ~ DC fast)

    def charge_kw(self, station_kw: float) -> float:
        return min(self.max_charge_kw, station_kw)

    def km_for(self, soc_pct: float) -> float:
        return self.capacity_kwh * soc_pct / 100 * self.efficiency_km_per_kwh

    def soc_used(self, km: float) -> float:
        return km / self.efficiency_km_per_kwh / self.capacity_kwh * 100


@dataclass
class Stop:
    number: int
    name: str
    operator: str
    address: str
    city: str
    station_kw: float  # fastest car charger at the station
    charge_kw: float  # power actually used (limited by the car)
    connector_types: str
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
    corridor = sorted(
        ((float(cum[nearest[i]]), float(off_route[i]), int(i)) for i in in_corridor),
        key=lambda c: c[0],
    )
    plan.nearby = [
        {
            "name": str(stations.names[i]),
            "lat": float(stations.lat[i]),
            "lon": float(stations.lon[i]),
            "kw": float(stations.power_kw[i]),
        }
        for _, _, i in corridor[:500]
    ]
    # Only chargers at least this fast are considered for stops.
    candidates = [c for c in corridor if stations.power_kw[c[2]] >= ev.min_charger_kw]

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
                    f"No charger of {ev.min_charger_kw:g} kW or more within reach after "
                    f"{position:.0f} km (range ≈ {max(reach_km, 0):.0f} km keeping a "
                    f"{ev.reserve_pct:.0f}% reserve). Try a higher charge target, a wider search "
                    "corridor or a lower minimum charger power."
                )
            return plan

        stop_km, detour, idx = max(reachable, key=lambda c: c[0])
        arrive = soc - ev.soc_used(stop_km - position + detour)
        added_kwh = ev.capacity_kwh * (ev.target_soc_pct - arrive) / 100
        station_kw = float(stations.power_kw[idx])
        charge_kw = ev.charge_kw(station_kw)
        plan.stops.append(
            Stop(
                number=len(plan.stops) + 1,
                name=str(stations.names[idx]),
                operator=str(stations.operators[idx]),
                address=str(stations.addresses[idx]),
                city=str(stations.cities[idx]),
                station_kw=station_kw,
                charge_kw=charge_kw,
                connector_types=str(stations.connector_types[idx]),
                lat=float(stations.lat[idx]),
                lon=float(stations.lon[idx]),
                route_km=round(stop_km, 1),
                detour_km=round(detour, 2),
                arrive_soc_pct=round(arrive, 1),
                depart_soc_pct=ev.target_soc_pct,
                charge_kwh=round(added_kwh, 1),
                charge_min=round(added_kwh / charge_kw * 60, 0),
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
