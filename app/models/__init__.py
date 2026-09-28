"""Database models. Import them here so Flask-Migrate sees every table."""

from app.models.user import Role, User

__all__ = ["Role", "User"]
