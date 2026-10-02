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
    # Battery (raised from live telemetry and battery checks)
    LOW_BATTERY = "low_battery"
    BATTERY_OVERHEAT = "battery_overheat"
    BATTERY_DEGRADED = "battery_degraded"
    # Maintenance (raised by the scheduled maintenance rules)
    SERVICE_DUE = "service_due"
    BRAKE_INSPECTION = "brake_inspection"
    BATTERY_CHECK_DUE = "battery_check_due"
    DEVICE_OFFLINE = "device_offline"

    @property
    def label(self) -> str:
        return _ALERT_LABELS[self.value]

    @property
    def category(self) -> str:
        return "battery" if self.value.startswith(("low_battery", "battery_")) else "maintenance"


_ALERT_LABELS = {
    "low_battery": "Low battery",
    "battery_overheat": "Battery overheating",
    "battery_degraded": "Battery wear",
    "service_due": "Service due",
    "brake_inspection": "Brake inspection",
    "battery_check_due": "Battery check due",
    "device_offline": "Device offline",
}


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
    # Set when a person closes the alert by hand (automatic resolutions leave these empty).
    resolved_by_id: Mapped[int | None] = mapped_column(ForeignKey("users.id", ondelete="SET NULL"))
    resolution_note: Mapped[str | None] = mapped_column(String(500))
    # When managers were emailed about this alert (critical alerts only).
    notified_at: Mapped[datetime | None]

    vehicle: Mapped["Vehicle"] = relationship()
    acknowledged_by: Mapped["User | None"] = relationship(foreign_keys=[acknowledged_by_id])
    resolved_by: Mapped["User | None"] = relationship(foreign_keys=[resolved_by_id])

    @property
    def is_open(self) -> bool:
        return self.resolved_at is None
