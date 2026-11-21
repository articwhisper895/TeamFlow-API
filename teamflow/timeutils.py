"""Timestamp conventions shared by models and the session layer.

Values are stored and compared in UTC. SQLite drops the timezone offset and
returns naive datetimes, while PostgreSQL returns aware ones, so stored values
are normalized with :func:`as_utc` before any comparison.
"""

from datetime import datetime, timezone


def utcnow() -> datetime:
    """Return the current time as a timezone-aware UTC datetime."""
    return datetime.now(timezone.utc)


def as_utc(value: datetime) -> datetime:
    """Normalize a stored timestamp to timezone-aware UTC.

    A naive value is interpreted as UTC because that is how it was written; an
    aware value is converted rather than reinterpreted.
    """
    if value.tzinfo is None:
        return value.replace(tzinfo=timezone.utc)
    return value.astimezone(timezone.utc)


def isoformat_utc(value: datetime) -> str:
    """Serialize a timestamp as ISO 8601 with an explicit UTC designator."""
    return as_utc(value).isoformat().replace("+00:00", "Z")
