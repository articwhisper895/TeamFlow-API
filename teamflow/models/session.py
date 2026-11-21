"""Persistent refresh-session and refresh-token records.

A *session* represents one sign-in on one device. It is also a token family:
every refresh exchanges the current token for a replacement inside the same
family, and revoking the session ends only that family, leaving the user's
other sign-ins untouched.

Only the JWT identifier (``jti``) is stored, never the signed refresh token.
A database leak therefore exposes identifiers that cannot be replayed as
credentials, because the attacker still cannot produce a token the server's
signing key will accept.

Timestamps are handled as timezone-aware UTC. PostgreSQL returns aware values
for ``TIMESTAMP WITH TIME ZONE``, while SQLite drops the offset and returns
naive values, so stored timestamps are always normalized with :func:`as_utc`
before they are compared with :func:`utcnow`.
"""

from datetime import datetime, timezone
from uuid import uuid4

from teamflow.extensions import db


def utcnow() -> datetime:
    """Return the current time as a timezone-aware UTC datetime."""
    return datetime.now(timezone.utc)


def as_utc(value: datetime) -> datetime:
    """Normalize a timestamp to timezone-aware UTC.

    Naive values are treated as UTC because that is how they were written; an
    aware value from PostgreSQL is converted rather than reinterpreted. This
    keeps expiry comparisons correct on both database engines.
    """
    if value.tzinfo is None:
        return value.replace(tzinfo=timezone.utc)
    return value.astimezone(timezone.utc)


def isoformat_utc(value: datetime) -> str:
    """Serialize a timestamp as ISO 8601 with an explicit UTC designator."""
    return as_utc(value).isoformat().replace("+00:00", "Z")


def new_session_id() -> str:
    """Return an unguessable 128-bit session identifier."""
    return uuid4().hex


class RefreshSession(db.Model):
    """One login session and the token family that belongs to it."""

    __tablename__ = "refresh_sessions"

    id = db.Column(db.String(32), primary_key=True, default=new_session_id)
    user_id = db.Column(
        db.Integer,
        db.ForeignKey("users.id", ondelete="CASCADE"),
        nullable=False,
        index=True,
    )
    created_at = db.Column(db.DateTime(timezone=True), nullable=False, default=utcnow)
    # Absolute lifetime of this session, not extended by rotation.
    expires_at = db.Column(db.DateTime(timezone=True), nullable=False)
    revoked_at = db.Column(db.DateTime(timezone=True), nullable=True)
    revoked_reason = db.Column(db.String(40), nullable=True)
    last_used_at = db.Column(db.DateTime(timezone=True), nullable=True)

    user = db.relationship("User", back_populates="refresh_sessions")
    tokens = db.relationship(
        "RefreshToken",
        back_populates="session",
        cascade="all, delete-orphan",
        order_by="RefreshToken.issued_at",
    )

    def revoke(self, reason: str) -> None:
        """Revoke this session once; the first reason is kept for the audit trail."""
        if self.revoked_at is None:
            self.revoked_at = utcnow()
            self.revoked_reason = reason

    def to_dict(self) -> dict:
        return {
            "id": self.id,
            "created_at": isoformat_utc(self.created_at),
            "expires_at": isoformat_utc(self.expires_at),
            "revoked_at": isoformat_utc(self.revoked_at) if self.revoked_at else None,
            "revoked_reason": self.revoked_reason,
            "last_used_at": isoformat_utc(self.last_used_at) if self.last_used_at else None,
        }


class RefreshToken(db.Model):
    """One issued refresh token, tracked by JWT identifier rather than by value."""

    __tablename__ = "refresh_tokens"

    id = db.Column(db.Integer, primary_key=True)
    session_id = db.Column(
        db.String(32),
        db.ForeignKey("refresh_sessions.id", ondelete="CASCADE"),
        nullable=False,
        index=True,
    )
    jti = db.Column(db.String(64), nullable=False, unique=True, index=True)
    issued_at = db.Column(db.DateTime(timezone=True), nullable=False, default=utcnow)
    expires_at = db.Column(db.DateTime(timezone=True), nullable=False)
    # Set exactly once, by the single refresh that successfully claims this token.
    consumed_at = db.Column(db.DateTime(timezone=True), nullable=True)
    replaced_by_id = db.Column(
        db.Integer,
        db.ForeignKey("refresh_tokens.id", ondelete="SET NULL"),
        nullable=True,
    )

    session = db.relationship("RefreshSession", back_populates="tokens")
    replaced_by = db.relationship("RefreshToken", remote_side=[id], uselist=False)

    def to_dict(self) -> dict:
        return {
            "id": self.id,
            "session_id": self.session_id,
            "issued_at": isoformat_utc(self.issued_at),
            "expires_at": isoformat_utc(self.expires_at),
            "consumed_at": isoformat_utc(self.consumed_at) if self.consumed_at else None,
            "replaced_by_id": self.replaced_by_id,
        }
