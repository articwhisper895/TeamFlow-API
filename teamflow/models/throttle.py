"""Counters backing the credential-endpoint rate limits."""

from teamflow.extensions import db
from teamflow.timeutils import utcnow


class RateLimit(db.Model):
    """One counter for a (scope, key) pair.

    Scopes separate the policies: ``login-ip`` and ``register-ip`` count attempts
    per client address, while ``login-account`` counts failed sign-ins for one
    account. Keys are SHA-256 digests so an email address is never stored here.
    Counters live in the database, not in process memory, so every worker sharing
    the database enforces the same limit.
    """

    __tablename__ = "rate_limits"
    __table_args__ = (db.UniqueConstraint("scope", "key"),)

    id = db.Column(db.Integer, primary_key=True)
    scope = db.Column(db.String(32), nullable=False)
    key = db.Column(db.String(64), nullable=False)
    attempts = db.Column(db.Integer, nullable=False, default=0)
    window_started_at = db.Column(db.DateTime(timezone=True), nullable=False, default=utcnow)
    blocked_until = db.Column(db.DateTime(timezone=True), nullable=True)
    updated_at = db.Column(
        db.DateTime(timezone=True), nullable=False, default=utcnow, onupdate=utcnow
    )
