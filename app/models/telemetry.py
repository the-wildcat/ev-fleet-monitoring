from datetime import datetime

from sqlalchemy import ForeignKey, Index
from sqlalchemy.orm import Mapped, mapped_column

from app.extensions import db


class Telemetry(db.Model):
    """One reading sent by a vehicle (real device or simulator)."""

    __tablename__ = "telemetry"
    # Almost every query is "readings for vehicle X in time range Y".
    __table_args__ = (Index("ix_telemetry_vehicle_time", "vehicle_id", "recorded_at"),)

    id: Mapped[int] = mapped_column(primary_key=True)
    vehicle_id: Mapped[int] = mapped_column(ForeignKey("vehicles.id", ondelete="CASCADE"))
    recorded_at: Mapped[datetime] = mapped_column(index=True)
    lat: Mapped[float]
    lon: Mapped[float]
    speed_kmh: Mapped[float]
    soc_pct: Mapped[float]
    is_charging: Mapped[bool] = mapped_column(default=False)
    battery_temp_c: Mapped[float | None]
    odometer_km: Mapped[float | None]
    acceleration_mps2: Mapped[float | None]
    power_kw: Mapped[float | None]  # + while driving (drawn), - while charging (added)

    def to_dict(self) -> dict:
        return {
            "recorded_at": self.recorded_at.isoformat() + "Z",
            "lat": self.lat,
            "lon": self.lon,
            "speed_kmh": self.speed_kmh,
            "soc_pct": self.soc_pct,
            "is_charging": self.is_charging,
            "battery_temp_c": self.battery_temp_c,
            "odometer_km": self.odometer_km,
            "acceleration_mps2": self.acceleration_mps2,
            "power_kw": self.power_kw,
        }
