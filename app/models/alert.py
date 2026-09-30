import enum
from datetime import datetime
from typing import TYPE_CHECKING

from sqlalchemy import Enum, ForeignKey, Index, String
from sqlalchemy.orm import Mapped, mapped_column, relationship

from app.extensions import db
from app.utils import utcnow

if TYPE_CHECKING:
    from app.models.user import User
    from app.models.vehicle import Vehicle


class AlertType(enum.StrEnum):
    LOW_BATTERY = "low_battery"
    BATTERY_OVERHEAT = "battery_overheat"
    BATTERY_DEGRADED = "battery_degraded"

    @property
    def label(self) -> str:
        return {
            "low_battery": "Low battery",
            "battery_overheat": "Battery overheating",
            "battery_degraded": "Battery wear",
        }[self.value]


class AlertSeverity(enum.StrEnum):
    WARNING = "warning"
    CRITICAL = "critical"


class Alert(db.Model):
    """A problem detected on a vehicle.

    While the condition lasts the alert stays open (resolved_at is NULL) and is updated in
    place; it is resolved automatically when the condition clears.
    """

    __tablename__ = "alerts"
    __table_args__ = (
        # At most one open alert per vehicle and type (partial unique index).
        Index(
            "uq_alerts_open_vehicle_type",
            "vehicle_id",
            "alert_type",
            unique=True,
            sqlite_where=db.text("resolved_at IS NULL"),
            postgresql_where=db.text("resolved_at IS NULL"),
        ),
    )

    id: Mapped[int] = mapped_column(primary_key=True)
    vehicle_id: Mapped[int] = mapped_column(
        ForeignKey("vehicles.id", ondelete="CASCADE"), index=True
    )
    alert_type: Mapped[AlertType] = mapped_column(
        Enum(AlertType, values_callable=lambda m: [x.value for x in m], name="alert_type")
    )
    severity: Mapped[AlertSeverity] = mapped_column(
        Enum(AlertSeverity, values_callable=lambda m: [x.value for x in m], name="alert_severity")
    )
    message: Mapped[str] = mapped_column(String(255))
    created_at: Mapped[datetime] = mapped_column(default=utcnow, index=True)
    last_seen_at: Mapped[datetime] = mapped_column(default=utcnow)
    resolved_at: Mapped[datetime | None]
    acknowledged_at: Mapped[datetime | None]
    acknowledged_by_id: Mapped[int | None] = mapped_column(
        ForeignKey("users.id", ondelete="SET NULL")
    )

    vehicle: Mapped["Vehicle"] = relationship()
    acknowledged_by: Mapped["User | None"] = relationship()

    @property
    def is_open(self) -> bool:
        return self.resolved_at is None
