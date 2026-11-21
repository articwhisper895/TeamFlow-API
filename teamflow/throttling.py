"""Database-backed throttling for login and registration.

The policy is deliberately simple to reason about and to test:

* Failed sign-ins are counted per client address and per account. Reaching the
  limit blocks that scope for an exponentially growing backoff, so a sustained
  attack gets slower instead of staying free.
* Registration is counted per client address in a fixed window.
* Counters are rows in the database, so several workers behind one database
  enforce one shared view of recent failures.
* Every change to a counter is one statement whose condition and write the
  database applies together. Reading ``attempts``, adding one in Python and
  writing the total back would let two simultaneous failures read the same value
  and each store the same total, silently losing an attempt.

Throttling never reports whether an account exists: the same limit applies to an
unknown email address, and the caller gets one generic response.
"""

import hashlib
import math
from datetime import timedelta

from flask import current_app, request
from sqlalchemy import delete, insert, or_, update
from sqlalchemy.exc import IntegrityError

from teamflow.extensions import db
from teamflow.models import RateLimit
from teamflow.timeutils import as_utc, utcnow

LOGIN_IP = "login-ip"
LOGIN_ACCOUNT = "login-account"
REGISTER_IP = "register-ip"

LOGIN_SCOPES = (LOGIN_IP, LOGIN_ACCOUNT)
REGISTRATION_SCOPES = (REGISTER_IP,)


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
    db.session.execute(
        delete(RateLimit)
        .where(RateLimit.scope == LOGIN_ACCOUNT, RateLimit.key == account)
        .execution_options(synchronize_session=False)
    )
    db.session.commit()


def record_registration(ip_key: str) -> int | None:
    """Count one registration attempt; return a retry delay when it is refused."""
    if not enabled():
        return None
    now = utcnow()
    window = current_app.config["REGISTER_WINDOW_SECONDS"]
    limit = current_app.config["REGISTER_IP_MAX_ATTEMPTS"]
    wait = _wait(REGISTER_IP, ip_key)
    if wait is not None:
        # Already refused for this window; counting it would change nothing.
        return wait

    attempts = _increment(REGISTER_IP, ip_key, window, now)
    refused = None
    if attempts >= limit:
        # Reaching the limit closes this fixed window for the rest of its length.
        started = db.session.scalar(
            db.select(RateLimit.window_started_at).where(
                RateLimit.scope == REGISTER_IP, RateLimit.key == ip_key
            )
        )
        until = as_utc(started) + timedelta(seconds=window)
        _block(REGISTER_IP, ip_key, until, now)
        if attempts > limit:
            # Another request spent the last slot first: this one is over the limit.
            refused = max(1, math.ceil((until - now).total_seconds()))
    db.session.commit()
    return refused


def prune_expired(now=None) -> int:
    """Delete counters that can no longer count or block anything.

    A counter stops mattering once its counting window has passed and any block
    it started has ended, so the largest configured window and backoff bound its
    lifetime. Returns the number of rows deleted.
    """
    now = now or utcnow()
    login_window = max(
        current_app.config["LOGIN_FAILURE_WINDOW_SECONDS"],
        current_app.config["LOGIN_BACKOFF_MAX_SECONDS"],
    )
    removed = 0
    for scopes, window in (
        (LOGIN_SCOPES, login_window),
        (REGISTRATION_SCOPES, current_app.config["REGISTER_WINDOW_SECONDS"]),
    ):
        result = db.session.execute(
            delete(RateLimit)
            .where(
                RateLimit.scope.in_(scopes),
                RateLimit.window_started_at <= now - timedelta(seconds=window),
                or_(RateLimit.blocked_until.is_(None), RateLimit.blocked_until <= now),
            )
            .execution_options(synchronize_session=False)
        )
        removed += result.rowcount
    db.session.commit()
    return removed


def _bucket(scope: str, key: str) -> RateLimit | None:
    return db.session.scalar(
        db.select(RateLimit).where(RateLimit.scope == scope, RateLimit.key == key)
    )


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


def _increment(scope: str, key: str, window: int, now) -> int:
    """Add one to a scope's counter and return the new value.

    Two simultaneous failures always produce two increments because the database
    applies each one to the value it holds when the statement runs.
    """
    attempts = _increment_existing(scope, key, window, now)
    if attempts is not None:
        return attempts
    # No row yet. The savepoint means losing the insert race does not discard the
    # caller's work; the loser then counts against the row the winner created.
    try:
        with db.session.begin_nested():
            db.session.execute(
                insert(RateLimit).values(
                    scope=scope,
                    key=key,
                    attempts=1,
                    window_started_at=now,
                    updated_at=now,
                )
            )
        return 1
    except IntegrityError:
        counted = _increment_existing(scope, key, window, now)
        return 1 if counted is None else counted


def _increment_existing(scope: str, key: str, window: int, now) -> int | None:
    """Increment an existing row; None means the scope has no counter yet."""
    cutoff = now - timedelta(seconds=window)
    identity = (RateLimit.scope == scope, RateLimit.key == key)
    # A counter whose window has passed starts a fresh window in the same
    # statement, so no caller can act on an expired total it read earlier.
    restarted = db.session.execute(
        update(RateLimit)
        .where(*identity, RateLimit.window_started_at <= cutoff)
        .values(attempts=1, window_started_at=now, blocked_until=None, updated_at=now)
        .execution_options(synchronize_session=False)
    )
    if restarted.rowcount:
        return 1
    counted = db.session.execute(
        update(RateLimit)
        .where(*identity, RateLimit.window_started_at > cutoff)
        .values(attempts=RateLimit.attempts + 1, updated_at=now)
        .execution_options(synchronize_session=False)
    )
    if not counted.rowcount:
        return None
    return db.session.scalar(db.select(RateLimit.attempts).where(*identity))


def _block(scope: str, key: str, until, now) -> None:
    """Raise a scope's block to ``until``.

    Only ever extends it: a failure that lands while a later one is already
    recorded must not shorten the wait.
    """
    db.session.execute(
        update(RateLimit)
        .where(
            RateLimit.scope == scope,
            RateLimit.key == key,
            or_(RateLimit.blocked_until.is_(None), RateLimit.blocked_until < until),
        )
        .values(blocked_until=until, updated_at=now)
        # Nothing here reads a loaded counter back; evaluating this predicate in
        # Python would also compare a naive SQLite timestamp with an aware one.
        .execution_options(synchronize_session=False)
    )


def _count(scope: str, key: str, limit: int, window: int, base: int, cap: int, now) -> None:
    """Count one failure, blocking the scope once ``limit`` is reached."""
    attempts = _increment(scope, key, window, now)
    if attempts < limit:
        return
    # Each failure past the limit doubles the wait up to the configured cap.
    backoff = base * (2 ** (attempts - limit))
    _block(scope, key, now + timedelta(seconds=min(backoff, cap)), now)
