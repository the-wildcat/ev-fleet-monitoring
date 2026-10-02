"""Telemetry simulator: stands in for real vehicle devices.

Each active vehicle marked `simulated` drives a loop through a real Indian city. Every tick
it produces one reading (position, speed, charge, temperature, ...) that is stored through
`app.services.telemetry`, the same code path the device API uses.

Behaviour that later modules analyse is built in:
  * the battery drains with distance; below 20% the vehicle stops to charge;
  * each vehicle has a fixed "aggressiveness" (driving style): aggressive drivers brake and
    accelerate harder, speed more often and use more energy;
  * rare thermal events (several minutes of high battery temperature).

A tick of `interval` real seconds simulates `interval * time_scale` seconds of driving, so a
demo shows visible movement and battery changes within minutes.
"""

from __future__ import annotations

import math
import random
import threading
import time
from dataclasses import dataclass, field

from flask import Flask

from app.extensions import db
from app.models import Vehicle, VehicleStatus
from app.services.telemetry import parse_reading, prune_old_telemetry, record_reading
from app.utils import utcnow

# Waypoint loops through real city locations (lat, lon). Vehicles drive them in order.
CITY_LOOPS: dict[str, list[tuple[float, float]]] = {
    "Delhi": [
        (28.6315, 77.2167),  # Connaught Place
        (28.6129, 77.2295),  # India Gate
        (28.5672, 77.2100),  # AIIMS
        (28.5245, 77.1855),  # Qutub Minar
        (28.5562, 77.1000),  # IGI Airport
        (28.6280, 77.0920),  # Janakpuri
        (28.6517, 77.1906),  # Karol Bagh
    ],
    "Bengaluru": [
        (12.9716, 77.5946),  # MG Road
        (12.9352, 77.6245),  # Koramangala
        (12.8452, 77.6602),  # Electronic City
        (12.9116, 77.6474),  # HSR Layout
        (12.9698, 77.7500),  # Whitefield
        (13.0358, 77.5970),  # Hebbal
    ],
    "Mumbai": [
        (18.9220, 72.8347),  # Colaba
        (19.0176, 72.8562),  # Dadar
        (19.0596, 72.8295),  # Bandra
        (19.1136, 72.8697),  # Andheri
        (19.0760, 72.8777),  # Kurla
        (18.9750, 72.8258),  # Worli
    ],
    "Kolkata": [
        (22.5726, 88.3639),  # Esplanade
        (22.5448, 88.3426),  # Victoria Memorial
        (22.5000, 88.3700),  # Jadavpur
        (22.5150, 88.3950),  # Ruby
        (22.5868, 88.4171),  # Salt Lake
        (22.6203, 88.4504),  # New Town
    ],
    "Hyderabad": [
        (17.3850, 78.4867),  # Abids
        (17.4126, 78.4482),  # Banjara Hills
        (17.4435, 78.3772),  # HITEC City
        (17.4948, 78.3996),  # Kukatpally
        (17.4399, 78.4983),  # Secunderabad
    ],
}

LOW_SOC_PCT = 20.0  # start charging below this
CHARGE_TARGET_PCT = 90.0  # stop charging at this
CHARGER_KW = (7.2, 25.0, 50.0)  # AC, DC, fast DC
# Per tick. At the default 5 s interval (3,600 ticks in 5 hours) that's about one thermal
# event per vehicle every 5 hours.
THERMAL_EVENT_PROBABILITY = 1 / 3600


def haversine_km(a: tuple[float, float], b: tuple[float, float]) -> float:
    lat1, lon1, lat2, lon2 = map(math.radians, (*a, *b))
    h = (
        math.sin((lat2 - lat1) / 2) ** 2
        + math.cos(lat1) * math.cos(lat2) * math.sin((lon2 - lon1) / 2) ** 2
    )
    return 2 * 6371.0 * math.asin(math.sqrt(h))


@dataclass
class VehicleSim:
    """Simulation state for one vehicle (kept in memory between ticks)."""

    points: list[tuple[float, float]]
    segment: int
    soc_pct: float
    odometer_km: float
    aggressiveness: float  # ~0.6 calm .. ~1.8 aggressive
    rng: random.Random
    seg_progress_km: float = 0.0
    speed_kmh: float = 0.0
    charging: bool = False
    charger_kw: float = 0.0
    thermal_ticks: int = 0  # remaining ticks of an ongoing thermal event
    thermal_extra_c: float = 0.0
    position: tuple[float, float] = field(init=False)

    def __post_init__(self) -> None:
        self.position = self.points[self.segment]

    # --- movement -------------------------------------------------------------------------
    def _advance(self, distance_km: float) -> None:
        """Move `distance_km` along the loop, wrapping around at the end."""
        while distance_km > 0:
            start = self.points[self.segment]
            end = self.points[(self.segment + 1) % len(self.points)]
            seg_len = max(haversine_km(start, end), 1e-6)
            remaining = seg_len - self.seg_progress_km
            if distance_km < remaining:
                self.seg_progress_km += distance_km
                distance_km = 0
            else:
                distance_km -= remaining
                self.segment = (self.segment + 1) % len(self.points)
                self.seg_progress_km = 0.0
                start, end = end, self.points[(self.segment + 1) % len(self.points)]
                seg_len = max(haversine_km(start, end), 1e-6)
            t = self.seg_progress_km / seg_len
            self.position = (
                start[0] + (end[0] - start[0]) * t,
                start[1] + (end[1] - start[1]) * t,
            )

    def tick(self, dt_s: float, vehicle: Vehicle) -> dict:
        """Advance the simulation by `dt_s` seconds and return a raw telemetry reading."""
        rng, aggr = self.rng, self.aggressiveness
        capacity = vehicle.battery_capacity_kwh
        accel = 0.0

        if self.charging:
            added_kwh = self.charger_kw * dt_s / 3600
            self.soc_pct = min(100.0, self.soc_pct + added_kwh / capacity * 100)
            self.speed_kmh = 0.0
            power_kw = -self.charger_kw
            temp = 33 + self.charger_kw / 10 + rng.gauss(0, 1)
            if self.soc_pct >= CHARGE_TARGET_PCT:
                self.charging = False
        else:
            old_speed = self.speed_kmh
            # Style affects risky events more than proportionally (calm 0.46x, aggressive 2.4x).
            events = aggr**1.5
            speeding = rng.random() < 0.02 * events  # e.g. on an arterial road
            new_speed = rng.uniform(75, 95) if speeding else rng.uniform(15, 60)
            # Peak acceleration within the interval; aggressive drivers have more harsh events.
            roll = rng.random()
            if roll < 0.012 * events:
                accel = -rng.uniform(3.5, 6.5)  # harsh braking
                new_speed = rng.uniform(5, 25)
            elif roll < 0.022 * events:
                accel = rng.uniform(2.8, 4.5)  # harsh acceleration
            else:
                accel = rng.gauss(0, 0.5) + (new_speed - old_speed) / 3.6 / 10
            self.speed_kmh = new_speed

            distance_km = (old_speed + new_speed) / 2 * dt_s / 3600
            self._advance(distance_km)
            self.odometer_km += distance_km

            # Aggressive driving costs energy: about +20% at the top of the range.
            energy_kwh = (
                distance_km / vehicle.efficiency_km_per_kwh * (1 + 0.25 * (aggr - 1)) + 0.002
            )
            self.soc_pct = max(0.0, self.soc_pct - energy_kwh / capacity * 100)
            power_kw = energy_kwh / (dt_s / 3600)
            temp = 26 + 0.08 * new_speed + 2 * (aggr - 1) + rng.gauss(0, 1)

            if self.soc_pct <= LOW_SOC_PCT:
                self.charging = True
                self.speed_kmh = 0.0
                self.charger_kw = rng.choice(CHARGER_KW)

        # Rare thermal events lasting several ticks (e.g. a cooling fault), for battery alerts.
        if self.thermal_ticks == 0 and rng.random() < THERMAL_EVENT_PROBABILITY:
            self.thermal_ticks = rng.randint(3, 8)
            self.thermal_extra_c = rng.uniform(15, 25)
        if self.thermal_ticks > 0:
            temp += self.thermal_extra_c
            self.thermal_ticks -= 1

        return {
            "lat": round(self.position[0], 6),
            "lon": round(self.position[1], 6),
            "speed_kmh": round(self.speed_kmh, 1),
            "soc_pct": round(self.soc_pct, 2),
            "is_charging": self.charging,
            "battery_temp_c": round(temp, 1),
            "odometer_km": round(self.odometer_km, 3),
            "acceleration_mps2": round(max(-20.0, min(20.0, accel)), 2),
            "power_kw": round(power_kw, 2),
        }


def aggressiveness_for(vehicle_id: int) -> float:
    """Stable driving style per vehicle, so the same driver behaves consistently."""
    return round(random.Random(vehicle_id * 7919).uniform(0.6, 1.8), 2)


def new_sim_for(vehicle: Vehicle, rng: random.Random) -> VehicleSim:
    cities = list(CITY_LOOPS)
    points = CITY_LOOPS[cities[vehicle.id % len(cities)]]
    segment = 0
    if vehicle.last_lat is not None and vehicle.last_lon is not None:
        # Resume near where the vehicle was last seen (e.g. after an app restart).
        here = (vehicle.last_lat, vehicle.last_lon)
        segment = min(range(len(points)), key=lambda i: haversine_km(points[i], here))
    sim = VehicleSim(
        points=points,
        segment=segment,
        soc_pct=vehicle.last_soc_pct if vehicle.last_soc_pct is not None else rng.uniform(55, 95),
        odometer_km=vehicle.odometer_km or 0.0,
        aggressiveness=aggressiveness_for(vehicle.id),
        rng=random.Random(rng.random()),
    )
    if vehicle.last_is_charging and sim.soc_pct < CHARGE_TARGET_PCT:
        sim.charging, sim.charger_kw = True, rng.choice(CHARGER_KW)
    return sim


class FleetSimulator:
    def __init__(self, app: Flask, seed: int | None = None) -> None:
        self.app = app
        self.rng = random.Random(seed)
        self.sims: dict[int, VehicleSim] = {}
        self._last_prune = 0.0

    def step(self, dt_s: float | None = None) -> int:
        """Produce one reading for every simulated vehicle. Returns how many were recorded."""
        cfg = self.app.config
        dt_s = dt_s or cfg["SIMULATOR_INTERVAL_SECONDS"] * cfg["SIMULATOR_TIME_SCALE"]
        with self.app.app_context():
            vehicles = db.session.execute(
                db.select(Vehicle).where(
                    Vehicle.simulated.is_(True), Vehicle.status == VehicleStatus.ACTIVE
                )
            ).scalars()
            count = 0
            now = utcnow()
            for vehicle in vehicles:
                sim = self.sims.get(vehicle.id) or new_sim_for(vehicle, self.rng)
                self.sims[vehicle.id] = sim
                raw = sim.tick(dt_s, vehicle)
                clean, errors = parse_reading(raw)
                if errors:  # would indicate a simulator bug; never store bad data
                    self.app.logger.error("Simulator produced invalid reading: %s", errors)
                    continue
                clean["recorded_at"] = now
                record_reading(vehicle, clean)
                count += 1
            self._maybe_prune()
            db.session.commit()
            return count

    def _maybe_prune(self) -> None:
        if time.monotonic() - self._last_prune > 3600:
            self._last_prune = time.monotonic()
            removed = prune_old_telemetry(self.app.config["TELEMETRY_RETENTION_DAYS"])
            if removed:
                self.app.logger.info("Pruned %d old telemetry readings", removed)

    def run_forever(self, stop: threading.Event | None = None) -> None:
        stop = stop or threading.Event()
        interval = self.app.config["SIMULATOR_INTERVAL_SECONDS"]
        self.app.logger.info(
            "Telemetry simulator running (every %ss, %sx speed)",
            interval,
            self.app.config["SIMULATOR_TIME_SCALE"],
        )
        while not stop.is_set():
            started = time.monotonic()
            try:
                self.step()
            except Exception:
                self.app.logger.exception("Simulator tick failed")
                with self.app.app_context():
                    db.session.rollback()
            stop.wait(max(0.0, interval - (time.monotonic() - started)))


_thread_lock = threading.Lock()


def start_background_simulator(app: Flask) -> bool:
    """Start the simulator in a daemon thread once per process. Returns True if started."""
    with _thread_lock:
        if app.extensions.get("simulator_thread"):
            return False
        sim = FleetSimulator(app)
        thread = threading.Thread(target=sim.run_forever, name="telemetry-simulator", daemon=True)
        app.extensions["simulator_thread"] = thread
        thread.start()
        return True
