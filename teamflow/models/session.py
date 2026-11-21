"""Persistent refresh-session and refresh-token records.

A *session* represents one sign-in on one device. It is also a token family:
every refresh exchanges the current token for a replacement inside the same
family, and revoking the session ends only that family, leaving the user's
other sign-ins untouched.

Only the JWT identifier (``jti``) is stored, never the signed refresh token.
A database leak therefore exposes identifiers that cannot be replayed as
credentials, because the attacker still cannot produce a token the server's
signing key will accept.
"""

from datetime import datetime, timezone
from uuid import uuid4

from teamflow.extensions import db


def utcnow() -> datetime:
    """Return the current UTC time as a naive datetime.

    SQLite discards timezone information, so values read back from the database
    are naive. Storing naive UTC consistently keeps comparisons valid on SQLite
    (tests and local development) and on PostgreSQL.
    """
    return datetime.now(timezone.utc).replace(tzinfo=None)


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

    def is_active(self) -> bool:
        return self.revoked_at is None and self.expires_at > utcnow()

    def revoke(self, reason: str) -> None:
        """Revoke this session once; the first reason is kept for the audit trail."""
        if self.revoked_at is None:
            self.revoked_at = utcnow()
            self.revoked_reason = reason

    def to_dict(self) -> dict:
        return {
            "id": self.id,
            "created_at": self.created_at.isoformat(),
            "expires_at": self.expires_at.isoformat(),
            "revoked_at": self.revoked_at.isoformat() if self.revoked_at else None,
            "revoked_reason": self.revoked_reason,
            "last_used_at": self.last_used_at.isoformat() if self.last_used_at else None,
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
            "issued_at": self.issued_at.isoformat(),
            "expires_at": self.expires_at.isoformat(),
            "consumed_at": self.consumed_at.isoformat() if self.consumed_at else None,
            "replaced_by_id": self.replaced_by_id,
        }
