import enum
from datetime import datetime, timedelta
from typing import TYPE_CHECKING

from flask_login import UserMixin
from sqlalchemy import Enum, String
from sqlalchemy.orm import Mapped, mapped_column, relationship
from werkzeug.security import check_password_hash, generate_password_hash

from app.extensions import db, login_manager
from app.utils import utcnow

if TYPE_CHECKING:
    from app.models.vehicle import Vehicle


class Role(enum.StrEnum):
    ADMIN = "admin"
    FLEET_MANAGER = "fleet_manager"
    DRIVER = "driver"

    @property
    def label(self) -> str:
        return self.value.replace("_", " ").title()


class User(UserMixin, db.Model):
    __tablename__ = "users"

    id: Mapped[int] = mapped_column(primary_key=True)
    name: Mapped[str] = mapped_column(String(120))
    email: Mapped[str] = mapped_column(String(254), unique=True, index=True)
    password_hash: Mapped[str] = mapped_column(String(255))
    role: Mapped[Role] = mapped_column(
        Enum(Role, values_callable=lambda roles: [r.value for r in roles], name="user_role"),
        default=Role.DRIVER,
    )
    is_verified: Mapped[bool] = mapped_column(default=False)
    # Named `active` because Flask-Login reads `is_active` (a property below).
    active: Mapped[bool] = mapped_column(default=True)
    failed_login_count: Mapped[int] = mapped_column(default=0)
    locked_until: Mapped[datetime | None]
    last_login_at: Mapped[datetime | None]
    created_at: Mapped[datetime] = mapped_column(default=utcnow)

    vehicles: Mapped[list["Vehicle"]] = relationship(back_populates="driver")

    def __repr__(self) -> str:
        return f"<User {self.email} ({self.role})>"

    @property
    def is_manager(self) -> bool:
        """Admins and fleet managers can manage the whole fleet."""
        return self.role in (Role.ADMIN, Role.FLEET_MANAGER)

    # --- passwords -------------------------------------------------------------------------
    def set_password(self, password: str) -> None:
        self.password_hash = generate_password_hash(password)

    def check_password(self, password: str) -> bool:
        return check_password_hash(self.password_hash, password)

    # --- Flask-Login -----------------------------------------------------------------------
    @property
    def is_active(self) -> bool:
        return self.active

    # --- roles -----------------------------------------------------------------------------
    def has_role(self, *roles: Role) -> bool:
        return self.role in roles

    @property
    def is_admin(self) -> bool:
        return self.role == Role.ADMIN

    # --- brute-force lockout ---------------------------------------------------------------
    def is_locked(self) -> bool:
        return self.locked_until is not None and self.locked_until > utcnow()

    def register_failed_login(self, max_attempts: int, lockout_minutes: int) -> None:
        self.failed_login_count += 1
        if self.failed_login_count >= max_attempts:
            self.locked_until = utcnow() + timedelta(minutes=lockout_minutes)
            self.failed_login_count = 0

    def register_successful_login(self) -> None:
        self.failed_login_count = 0
        self.locked_until = None
        self.last_login_at = utcnow()


@login_manager.user_loader
def load_user(user_id: str) -> User | None:
    return db.session.get(User, int(user_id))
