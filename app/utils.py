from datetime import UTC, datetime
from urllib.parse import urlsplit


def is_safe_redirect(target: str | None) -> bool:
    """Only allow redirects to paths on this site (blocks open-redirect attacks)."""
    if not target or not target.startswith("/"):
        return False
    # "//evil.com" is protocol-relative, and browsers read "/\evil.com" the same way.
    if target.startswith(("//", "/\\")):
        return False
    parts = urlsplit(target)
    return not parts.scheme and not parts.netloc


def utcnow() -> datetime:
    """Current UTC time as a naive datetime.

    SQLite drops timezone info, so all timestamps are stored as naive UTC for consistent
    comparisons.
    """
    return datetime.now(UTC).replace(tzinfo=None)
