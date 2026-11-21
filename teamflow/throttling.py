"""Database-backed throttling for login and registration.

The policy is deliberately simple to reason about and to test:

* Failed sign-ins are counted per client address and per account. Reaching the
  limit blocks that scope for an exponentially growing backoff, so a sustained
  attack gets slower instead of staying free.
* Registration is counted per client address in a fixed window.
* Counters are rows in the database, so several workers behind one database
  enforce one shared view of recent failures.

Throttling never reports whether an account exists: the same limit applies to an
unknown email address, and the caller gets one generic response.
"""

import hashlib
import math
from datetime import timedelta

from flask import current_app, request

from teamflow.extensions import db
from teamflow.models import RateLimit
from teamflow.timeutils import as_utc, utcnow

LOGIN_IP = "login-ip"
LOGIN_ACCOUNT = "login-account"
REGISTER_IP = "register-ip"


def client_key() -> str:
    """Return the throttling key for the requesting client.

    ``X-Forwarded-For`` is deliberately ignored: unless a trusted proxy rewrites
    it, the header is client-controlled and would let an attacker rotate keys.
    Behind a proxy the deployment must pass the real address, which this project
    does not configure because it is not deployed.
    """
    return digest(request.remote_addr or "unknown")


def account_key(email: str) -> str:
    """Return a throttling key for an email address without storing the address."""
    return digest(email)


def digest(value: str) -> str:
    return hashlib.sha256(value.strip().lower().encode("utf-8")).hexdigest()


def enabled() -> bool:
    return bool(current_app.config["RATE_LIMIT_ENABLED"])


def login_wait(ip_key: str, account: str | None) -> int | None:
    """Return the seconds a login attempt must wait, or None when it may proceed."""
    if not enabled():
        return None
    wait = _wait(LOGIN_IP, ip_key)
    if wait is not None or account is None:
        return wait
    return _wait(LOGIN_ACCOUNT, account)


def record_login_failure(ip_key: str, account: str | None) -> None:
    """Count one failed sign-in against the client and, when known, the account."""
    if not enabled():
        return
    now = utcnow()
    window = current_app.config["LOGIN_FAILURE_WINDOW_SECONDS"]
    base = current_app.config["LOGIN_BACKOFF_BASE_SECONDS"]
    cap = current_app.config["LOGIN_BACKOFF_MAX_SECONDS"]
    _count(LOGIN_IP, ip_key, current_app.config["LOGIN_IP_MAX_FAILURES"], window, base, cap, now)
    if account is not None:
        _count(
            LOGIN_ACCOUNT,
            account,
            current_app.config["LOGIN_ACCOUNT_MAX_FAILURES"],
            window,
            base,
            cap,
            now,
        )
    db.session.commit()


def clear_login_failures(account: str | None) -> None:
    """Forget the account's failures after a successful sign-in."""
    if not enabled() or account is None:
        return
    bucket = _bucket(LOGIN_ACCOUNT, account)
    if bucket is not None:
        db.session.delete(bucket)
        db.session.commit()


def record_registration(ip_key: str) -> int | None:
    """Count one registration attempt; return a retry delay when it is refused."""
    if not enabled():
        return None
    now = utcnow()
    window = current_app.config["REGISTER_WINDOW_SECONDS"]
    limit = current_app.config["REGISTER_IP_MAX_ATTEMPTS"]
    bucket = _bucket(REGISTER_IP, ip_key)
    if bucket is None:
        bucket = RateLimit(
            scope=REGISTER_IP, key=ip_key, attempts=0, window_started_at=now, updated_at=now
        )
        db.session.add(bucket)
    elif _expired(bucket, window, now):
        bucket.attempts = 0
        bucket.window_started_at = now
        bucket.blocked_until = None

    wait = _remaining(bucket, now)
    if wait is not None:
        db.session.commit()
        return wait
    if bucket.attempts >= limit:
        # The window is exhausted but its block has not started: refuse until it ends.
        bucket.blocked_until = as_utc(bucket.window_started_at) + timedelta(seconds=window)
        db.session.commit()
        return _remaining(bucket, now)

    bucket.attempts += 1
    bucket.updated_at = now
    if bucket.attempts >= limit:
        bucket.blocked_until = as_utc(bucket.window_started_at) + timedelta(seconds=window)
    db.session.commit()
    return None


def _bucket(scope: str, key: str) -> RateLimit | None:
    return db.session.scalar(
        db.select(RateLimit).where(RateLimit.scope == scope, RateLimit.key == key)
    )


def _expired(bucket: RateLimit, window: int, now) -> bool:
    return as_utc(bucket.window_started_at) + timedelta(seconds=window) <= now


def _remaining(bucket: RateLimit, now) -> int | None:
    if bucket.blocked_until is None:
        return None
    seconds = (as_utc(bucket.blocked_until) - now).total_seconds()
    return max(1, math.ceil(seconds)) if seconds > 0 else None


def _wait(scope: str, key: str) -> int | None:
    bucket = _bucket(scope, key)
    if bucket is None:
        return None
    return _remaining(bucket, utcnow())


def _count(scope: str, key: str, limit: int, window: int, base: int, cap: int, now) -> None:
    """Count one failure, blocking the scope once ``limit`` is reached."""
    bucket = _bucket(scope, key)
    if bucket is None:
        bucket = RateLimit(scope=scope, key=key, attempts=0, window_started_at=now, updated_at=now)
        db.session.add(bucket)
    elif _expired(bucket, window, now):
        bucket.attempts = 0
        bucket.window_started_at = now
        bucket.blocked_until = None

    bucket.attempts += 1
    bucket.updated_at = now
    if bucket.attempts >= limit:
        # Each failure past the limit doubles the wait up to the configured cap.
        backoff = base * (2 ** (bucket.attempts - limit))
        bucket.blocked_until = now + timedelta(seconds=min(backoff, cap))
