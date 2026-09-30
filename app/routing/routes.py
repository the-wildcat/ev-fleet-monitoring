"""Route planner page: road route + charging stops based on battery level."""

from flask import current_app, render_template, request
from flask_login import login_required

from app.extensions import db
from app.routing import bp
from app.routing.forms import RoutePlanForm
from app.services.routing import (
    EVParams,
    Place,
    RoutingError,
    choose_plan,
    geocode,
    load_stations,
    road_routes,
)
from app.vehicles.access import get_visible_vehicle, visible_vehicles_stmt

MAX_MAP_POINTS = 800  # keep the page light: the route line is downsampled for display


def _thin(coords: list[tuple[float, float]]) -> list[list[float]]:
    step = max(1, len(coords) // MAX_MAP_POINTS)
    thinned = coords[::step]
    if thinned[-1] != coords[-1]:
        thinned.append(coords[-1])
    return [[round(lat, 5), round(lon, 5)] for lat, lon in thinned]


def _plan_to_map(plan) -> dict:
    return {
        "route": _thin(plan.route.coords),
        "origin": [plan.origin.lat, plan.origin.lon],
        "destination": [plan.destination.lat, plan.destination.lon],
        "stops": [
            {"n": s.number, "lat": s.lat, "lon": s.lon, "name": s.name, "city": s.city}
            for s in plan.stops
        ],
        "nearby": [[n["lat"], n["lon"], n["name"]] for n in plan.nearby],
    }


@bp.route("/plan", methods=["GET", "POST"])
@login_required
def plan():
    form = RoutePlanForm()
    vehicles = db.session.execute(visible_vehicles_stmt()).scalars().all()
    form.vehicle_id.choices = [(0, "— Custom EV (enter specs) —")] + [
        (v.id, f"{v.plate_number} · {v.display_name}") for v in vehicles
    ]
    if request.method == "GET" and vehicles and not form.vehicle_id.data:
        form.vehicle_id.data = vehicles[0].id

    result = None
    if form.validate_on_submit():
        try:
            result = _run_plan(form)
        except RoutingError as err:
            form.form_errors.append(str(err))

    return render_template(
        "routing/plan.html",
        form=form,
        result=result,
        map_data=_plan_to_map(result.best) if result else None,
        vehicles={
            v.id: {"soc": v.last_soc_pct, "has_location": v.last_lat is not None} for v in vehicles
        },
    )


def _run_plan(form: RoutePlanForm):
    cfg = current_app.config
    vehicle = get_visible_vehicle(form.vehicle_id.data) if form.vehicle_id.data else None

    if vehicle:
        capacity, efficiency = vehicle.battery_capacity_kwh, vehicle.efficiency_km_per_kwh
        start_soc = form.start_soc_pct.data
        if start_soc is None:
            start_soc = vehicle.last_soc_pct
        if start_soc is None:
            raise RoutingError("This vehicle hasn't reported its battery level; enter it.")
    else:
        capacity, efficiency = form.battery_capacity_kwh.data, form.efficiency_km_per_kwh.data
        start_soc = form.start_soc_pct.data
        if capacity is None or efficiency is None or start_soc is None:
            raise RoutingError(
                "For a custom EV, enter the battery capacity, efficiency and current battery %."
            )

    service = {
        "base_url": cfg["NOMINATIM_URL"],
        "user_agent": cfg["ROUTING_USER_AGENT"],
        "timeout": cfg["ROUTING_TIMEOUT_SECONDS"],
    }
    if form.use_vehicle_location.data:
        if not vehicle or vehicle.last_lat is None:
            raise RoutingError("The selected vehicle has no known location; enter a start point.")
        origin = Place(
            vehicle.last_lat, vehicle.last_lon, f"{vehicle.plate_number} (current location)"
        )
    else:
        origin = geocode(form.origin.data, **service)
    destination = geocode(form.destination.data, **service)

    routes = road_routes(
        origin, destination, base_url=cfg["OSRM_URL"], timeout=cfg["ROUTING_TIMEOUT_SECONDS"]
    )
    ev = EVParams(
        capacity_kwh=capacity,
        efficiency_km_per_kwh=efficiency,
        start_soc_pct=start_soc,
        reserve_pct=form.reserve_pct.data,
        target_soc_pct=form.target_soc_pct.data,
        charger_kw=form.charger_kw.data,
    )
    stations = load_stations(cfg["CHARGING_STATIONS_PATH"])
    return choose_plan(origin, destination, routes, ev, stations, form.corridor_km.data)
