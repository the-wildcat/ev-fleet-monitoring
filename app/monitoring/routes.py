"""Live fleet map and the JSON feed it polls."""

from flask import current_app, jsonify, render_template, url_for
from flask_login import login_required

from app.extensions import db
from app.models import Vehicle
from app.monitoring import bp
from app.vehicles.access import visible_vehicles_stmt

LOW_BATTERY_PCT = 20


def vehicle_snapshot(vehicle: Vehicle, offline_after: int) -> dict:
    range_km = vehicle.range_km
    return {
        "id": vehicle.id,
        "name": vehicle.display_name,
        "plate": vehicle.plate_number,
        "driver": vehicle.driver.name if vehicle.driver else None,
        "status": vehicle.status.value,
        "state": vehicle.live_state(offline_after),
        "lat": vehicle.last_lat,
        "lon": vehicle.last_lon,
        "speed_kmh": vehicle.last_speed_kmh,
        "soc_pct": vehicle.last_soc_pct,
        "low_battery": vehicle.last_soc_pct is not None and vehicle.last_soc_pct < LOW_BATTERY_PCT,
        "is_charging": bool(vehicle.last_is_charging),
        "battery_temp_c": vehicle.last_battery_temp_c,
        "range_km": round(range_km, 1) if range_km is not None else None,
        "odometer_km": round(vehicle.odometer_km or 0, 1),
        "last_seen_at": vehicle.last_seen_at.isoformat() + "Z" if vehicle.last_seen_at else None,
        "url": url_for("vehicles.vehicle_detail", vehicle_id=vehicle.id),
    }


def fleet_summary(snapshots: list[dict]) -> dict:
    socs = [s["soc_pct"] for s in snapshots if s["soc_pct"] is not None]
    states = [s["state"] for s in snapshots]
    return {
        "total": len(snapshots),
        "online": sum(st in ("moving", "idle", "charging") for st in states),
        "moving": states.count("moving"),
        "charging": states.count("charging"),
        "offline": sum(st in ("offline", "no_data") for st in states),
        "low_battery": sum(s["low_battery"] for s in snapshots),
        "avg_soc_pct": round(sum(socs) / len(socs), 1) if socs else None,
    }


def visible_snapshots() -> list[dict]:
    offline_after = current_app.config["OFFLINE_AFTER_SECONDS"]
    vehicles = db.session.execute(visible_vehicles_stmt()).scalars().all()
    return [vehicle_snapshot(v, offline_after) for v in vehicles]


@bp.route("/live")
@login_required
def live():
    return render_template("monitoring/live.html")


@bp.route("/fleet.json")
@login_required
def fleet_json():
    snapshots = visible_snapshots()
    return jsonify(summary=fleet_summary(snapshots), vehicles=snapshots)
