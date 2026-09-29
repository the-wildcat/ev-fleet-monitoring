"""Database models. Import them here so Flask-Migrate sees every table."""

from app.models.telemetry import Telemetry
from app.models.user import Role, User
from app.models.vehicle import ServiceRecord, ServiceType, Vehicle, VehicleStatus

__all__ = [
    "Role",
    "ServiceRecord",
    "ServiceType",
    "Telemetry",
    "User",
    "Vehicle",
    "VehicleStatus",
]
