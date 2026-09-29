import enum
import hashlib
import secrets
from datetime import date, datetime, timedelta
from typing import TYPE_CHECKING

from sqlalchemy import Enum, ForeignKey, String
from sqlalchemy.orm import Mapped, mapped_column, relationship

from app.extensions import db
from app.utils import utcnow

if TYPE_CHECKING:
    from app.models.user import User


def _enum(cls: type[enum.Enum], name: str) -> Enum:
    """Store enums by their string value (readable in the DB and stable across renames)."""
    return Enum(cls, values_callable=lambda members: [m.value for m in members], name=name)


class VehicleStatus(enum.StrEnum):
    ACTIVE = "active"
    IN_SERVICE = "in_service"
    INACTIVE = "inactive"

    @property
    def label(self) -> str:
        return self.value.replace("_", " ").title()


class ServiceType(enum.StrEnum):
    ROUTINE = "routine"
    BATTERY = "battery"
    TYRES = "tyres"
    BRAKES = "brakes"
    REPAIR = "repair"
    OTHER = "other"

    @property
    def label(self) -> str:
        return self.value.title()


class Vehicle(db.Model):
    __tablename__ = "vehicles"

    id: Mapped[int] = mapped_column(primary_key=True)
    make: Mapped[str] = mapped_column(String(60))
    model: Mapped[str] = mapped_column(String(60))
    year: Mapped[int | None]
    plate_number: Mapped[str] = mapped_column(String(15), unique=True, index=True)
    vin: Mapped[str | None] = mapped_column(String(17), unique=True)
    battery_capacity_kwh: Mapped[float]
    efficiency_km_per_kwh: Mapped[float] = mapped_column(default=6.5)
    status: Mapped[VehicleStatus] = mapped_column(
        _enum(VehicleStatus, "vehicle_status"), default=VehicleStatus.ACTIVE
    )
    driver_id: Mapped[int | None] = mapped_column(ForeignKey("users.id", ondelete="SET NULL"))
    # True while no real device is fitted: the built-in simulator generates its telemetry.
    simulated: Mapped[bool] = mapped_column(default=True)

    # Device credentials for the telemetry API. Only a SHA-256 hash of the key is stored.
    api_key_hash: Mapped[str | None] = mapped_column(String(64), unique=True, index=True)
    api_key_prefix: Mapped[str | None] = mapped_column(String(8))

    # Latest known state, copied from the newest telemetry reading so the live map and
    # dashboards don't have to scan the telemetry table.
    last_lat: Mapped[float | None]
    last_lon: Mapped[float | None]
    last_speed_kmh: Mapped[float | None]
    last_soc_pct: Mapped[float | None]
    last_is_charging: Mapped[bool | None]
    last_battery_temp_c: Mapped[float | None]
    last_seen_at: Mapped[datetime | None]
    odometer_km: Mapped[float] = mapped_column(default=0.0)

    created_at: Mapped[datetime] = mapped_column(default=utcnow)

    driver: Mapped["User | None"] = relationship(back_populates="vehicles")
    service_records: Mapped[list["ServiceRecord"]] = relationship(
        back_populates="vehicle",
        cascade="all, delete-orphan",
        passive_deletes=True,
        order_by="ServiceRecord.service_date.desc()",
    )

    def __repr__(self) -> str:
        return f"<Vehicle {self.plate_number}>"

    @property
    def display_name(self) -> str:
        return f"{self.make} {self.model}"

    @property
    def range_km(self) -> float | None:
        """Estimated remaining range from the current charge."""
        if self.last_soc_pct is None:
            return None
        return self.battery_capacity_kwh * self.last_soc_pct / 100 * self.efficiency_km_per_kwh

    def live_state(self, offline_after_seconds: int) -> str:
        """One of: no_data, offline, charging, moving, idle."""
        if self.last_seen_at is None:
            return "no_data"
        if utcnow() - self.last_seen_at > timedelta(seconds=offline_after_seconds):
            return "offline"
        if self.last_is_charging:
            return "charging"
        return "moving" if (self.last_speed_kmh or 0) > 1 else "idle"

    # --- device API key --------------------------------------------------------------------
    @staticmethod
    def hash_api_key(key: str) -> str:
        # Keys are 256-bit random values, so a fast hash is safe here (no brute-forcing a
        # human-chosen password) and allows an indexed lookup on every request.
        return hashlib.sha256(key.encode()).hexdigest()

    def issue_api_key(self) -> str:
        """Create a new device key, replacing any previous one. Returns the plain key once."""
        key = "evk_" + secrets.token_urlsafe(32)
        self.api_key_hash = self.hash_api_key(key)
        self.api_key_prefix = key[:8]
        return key


class ServiceRecord(db.Model):
    __tablename__ = "service_records"

    id: Mapped[int] = mapped_column(primary_key=True)
    vehicle_id: Mapped[int] = mapped_column(
        ForeignKey("vehicles.id", ondelete="CASCADE"), index=True
    )
    service_date: Mapped[date]
    service_type: Mapped[ServiceType] = mapped_column(_enum(ServiceType, "service_type"))
    odometer_km: Mapped[float | None]
    description: Mapped[str] = mapped_column(String(500), default="")
    cost_inr: Mapped[float | None]
    created_at: Mapped[datetime] = mapped_column(default=utcnow)

    vehicle: Mapped[Vehicle] = relationship(back_populates="service_records")
