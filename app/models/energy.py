from datetime import date, datetime
from typing import TYPE_CHECKING

from sqlalchemy import ForeignKey, String, UniqueConstraint
from sqlalchemy.orm import Mapped, mapped_column, relationship

from app.extensions import db
from app.utils import utcnow

if TYPE_CHECKING:
    from app.models.vehicle import Vehicle


class DailyEnergy(db.Model):
    """One vehicle's energy and cost for one local (IST) day, rolled up from telemetry.

    Raw telemetry is pruned after a few days; these rows keep the history for analysis.
    The tariff in force is stored with each day, so changing it later doesn't rewrite past
    costs.
    """

    __tablename__ = "daily_energy"
    __table_args__ = (UniqueConstraint("vehicle_id", "day", name="uq_daily_energy_vehicle_day"),)

    id: Mapped[int] = mapped_column(primary_key=True)
    vehicle_id: Mapped[int] = mapped_column(
        ForeignKey("vehicles.id", ondelete="CASCADE"), index=True
    )
    day: Mapped[date] = mapped_column(index=True)
    distance_km: Mapped[float] = mapped_column(default=0.0)
    energy_used_kwh: Mapped[float] = mapped_column(default=0.0)  # drawn from the battery
    battery_charged_kwh: Mapped[float] = mapped_column(default=0.0)  # added to the battery
    grid_energy_kwh: Mapped[float] = mapped_column(default=0.0)  # bought, incl. charging losses
    # Cost of the energy *consumed* (used / charging efficiency x tariff): the basis for
    # cost per km, stable regardless of when vehicles happen to charge.
    energy_cost_inr: Mapped[float] = mapped_column(default=0.0)
    # What was actually spent on charging that day (grid energy x tariff), as on the bill.
    charging_cost_inr: Mapped[float] = mapped_column(default=0.0)
    tariff_inr_per_kwh: Mapped[float]
    readings: Mapped[int] = mapped_column(default=0)
    updated_at: Mapped[datetime] = mapped_column(default=utcnow, onupdate=utcnow)

    vehicle: Mapped["Vehicle"] = relationship()


class Setting(db.Model):
    """Admin-editable system setting; overrides the default from config (see services.settings)."""

    __tablename__ = "settings"

    key: Mapped[str] = mapped_column(String(64), primary_key=True)
    value: Mapped[str] = mapped_column(String(255))
    updated_at: Mapped[datetime] = mapped_column(default=utcnow, onupdate=utcnow)
    updated_by_id: Mapped[int | None] = mapped_column(ForeignKey("users.id", ondelete="SET NULL"))
