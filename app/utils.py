from datetime import UTC, datetime


def utcnow() -> datetime:
    """Current UTC time as a naive datetime.

    SQLite drops timezone info, so all timestamps are stored as naive UTC for consistent
    comparisons.
    """
    return datetime.now(UTC).replace(tzinfo=None)
