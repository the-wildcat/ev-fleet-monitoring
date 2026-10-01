from datetime import datetime
from typing import TYPE_CHECKING

from sqlalchemy import ForeignKey, String
from sqlalchemy.orm import Mapped, mapped_column, relationship

from app.extensions import db
from app.utils import utcnow

if TYPE_CHECKING:
    from app.models.user import User
    from app.models.vehicle import Vehicle


class BatteryCheck(db.Model):
    """A saved battery-health prediction for a vehicle (its SoH history)."""

    __tablename__ = "battery_checks"

    id: Mapped[int] = mapped_column(primary_key=True)
    vehicle_id: Mapped[int] = mapped_column(
        ForeignKey("vehicles.id", ondelete="CASCADE"), index=True
    )
    # Diagnostic inputs (see app/services/battery_model.FEATURES)
    cycle_count: Mapped[float]
    ambient_temperature_c: Mapped[float]
    discharge_current_a: Mapped[float]
    avg_voltage_v: Mapped[float]
    max_temperature_c: Mapped[float]
    internal_resistance_mohm: Mapped[float]
    predicted_soh_pct: Mapped[float]
    status: Mapped[str] = mapped_column(String(20))
    model_name: Mapped[str] = mapped_column(String(40))
    created_by_id: Mapped[int | None] = mapped_column(ForeignKey("users.id", ondelete="SET NULL"))
    created_at: Mapped[datetime] = mapped_column(default=utcnow, index=True)

    vehicle: Mapped["Vehicle"] = relationship()
    created_by: Mapped["User | None"] = relationship()
