"""Who can see which vehicles.

Managers and admins see the whole fleet; drivers only see vehicles assigned to them.
A driver asking for someone else's vehicle gets 404 (not 403), so IDs of other vehicles
can't be discovered by trying them.
"""

from flask import abort
from flask_login import current_user
from sqlalchemy import Select

from app.extensions import db
from app.models import Vehicle


def visible_vehicles_stmt() -> Select:
    stmt = db.select(Vehicle).order_by(Vehicle.plate_number)
    if not current_user.is_manager:
        stmt = stmt.where(Vehicle.driver_id == current_user.id)
    return stmt


def get_visible_vehicle(vehicle_id: int) -> Vehicle:
    vehicle = db.session.get(Vehicle, vehicle_id)
    if vehicle is None or (not current_user.is_manager and vehicle.driver_id != current_user.id):
        abort(404)
    return vehicle
