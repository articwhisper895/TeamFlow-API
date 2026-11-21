"""Rate limiting and brute-force protection on the credential endpoints."""

import threading
from datetime import timedelta

import pytest

from teamflow import create_app
from teamflow.config import TestingConfig
from teamflow.extensions import db
from teamflow.models import RateLimit
from teamflow.throttling import (
    LOGIN_ACCOUNT,
    LOGIN_IP,
    REGISTER_IP,
    account_key,
    digest,
    prune_expired,
)
from teamflow.timeutils import as_utc, utcnow

PASSWORD = "CorrectHorse123!"


class RateLimitConfig(TestingConfig):
    # Small limits keep the policy readable in tests; the shape is the same as
    # the defaults, only the numbers differ.
    RATE_LIMIT_ENABLED = True
    LOGIN_IP_MAX_FAILURES = 4
    LOGIN_ACCOUNT_MAX_FAILURES = 3
    LOGIN_FAILURE_WINDOW_SECONDS = 900
    LOGIN_BACKOFF_BASE_SECONDS = 60
    LOGIN_BACKOFF_MAX_SECONDS = 240
    REGISTER_IP_MAX_ATTEMPTS = 5
    REGISTER_WINDOW_SECONDS = 3600


@pytest.fixture()
def limited_app():
    application = create_app(RateLimitConfig)
    with application.app_context():
        db.create_all()
        yield application
        db.session.remove()
        db.drop_all()


@pytest.fixture()
def limited_client(limited_app):
    return limited_app.test_client()


def login(client, email="ada@example.com", password="wrong-password", ip="192.0.2.10"):
    """Attempt a sign-in from ``ip``; the test client reports one fixed address."""
    return client.post(
        "/api/v1/auth/login",
        json={"email": email, "password": password},
        environ_base={"REMOTE_ADDR": ip},
    )


def sign_up(client, email, ip="192.0.2.10"):
    return client.post(
        "/api/v1/auth/register",
        json={"name": "New User", "email": email, "password": PASSWORD},
        environ_base={"REMOTE_ADDR": ip},
    )


def account_bucket():
    return db.session.scalar(
        db.select(RateLimit).where(
            RateLimit.scope == LOGIN_ACCOUNT, RateLimit.key == account_key("ada@example.com")
        )
    )


def seconds_left(value) -> float:
    return (as_utc(value) - utcnow()).total_seconds()


def test_failed_logins_block_the_account_for_any_address(limited_client):
    sign_up(limited_client, "ada@example.com")

    for _ in range(RateLimitConfig.LOGIN_ACCOUNT_MAX_FAILURES):
        assert login(limited_client, ip="192.0.2.10").status_code == 401

    # The correct password from a fresh address is refused: the account is locked.
    blocked = login(limited_client, password=PASSWORD, ip="192.0.2.11")

    assert blocked.status_code == 429
    assert blocked.get_json()["error"]["code"] == "too_many_attempts"
    assert int(blocked.headers["Retry-After"]) > 0


def test_failed_logins_block_the_address_across_accounts(limited_client):
    for index, email in enumerate(("a@example.com", "b@example.com", "c@example.com", "d@example.com")):
        response = login(limited_client, email=email, ip="192.0.2.20")
        assert response.status_code == 401, index

    blocked = login(limited_client, email="brand-new@example.com", ip="192.0.2.20")

    assert blocked.status_code == 429
    assert blocked.get_json()["error"]["code"] == "too_many_attempts"


def test_unknown_address_is_throttled_exactly_like_a_known_one(limited_client):
    sign_up(limited_client, "ada@example.com")

    for _ in range(RateLimitConfig.LOGIN_ACCOUNT_MAX_FAILURES):
        login(limited_client, email="ada@example.com", ip="192.0.2.30")
    for _ in range(RateLimitConfig.LOGIN_ACCOUNT_MAX_FAILURES):
        login(limited_client, email="ghost@example.com", ip="192.0.2.31")

    known = login(limited_client, email="ada@example.com", ip="192.0.2.32")
    unknown = login(limited_client, email="ghost@example.com", ip="192.0.2.33")

    assert known.status_code == unknown.status_code == 429
    # Identical body: the response says nothing about whether the account exists.
    assert known.get_json() == unknown.get_json()


def test_a_successful_login_resets_the_account_counter(limited_client):
    sign_up(limited_client, "ada@example.com")

    assert login(limited_client, ip="192.0.2.40").status_code == 401
    assert login(limited_client, password=PASSWORD, ip="192.0.2.41").status_code == 200

    # Without the reset these two failures would reach the limit of three.
    assert login(limited_client, ip="192.0.2.42").status_code == 401
    assert login(limited_client, ip="192.0.2.43").status_code == 401
    assert login(limited_client, password=PASSWORD, ip="192.0.2.44").status_code == 200


def test_block_duration_doubles_while_the_window_stays_open(limited_client):
    sign_up(limited_client, "ada@example.com")
    for _ in range(RateLimitConfig.LOGIN_ACCOUNT_MAX_FAILURES):
        login(limited_client, ip="192.0.2.50")

    first = seconds_left(account_bucket().blocked_until)

    # Simulate the first block elapsing without expiring the counting window.
    bucket = account_bucket()
    bucket.blocked_until = utcnow() - timedelta(seconds=1)
    db.session.commit()
    assert login(limited_client, ip="192.0.2.51").status_code == 401
    second = seconds_left(account_bucket().blocked_until)

    assert second > first


def test_registration_is_limited_per_address(limited_client):
    for index in range(RateLimitConfig.REGISTER_IP_MAX_ATTEMPTS):
        assert sign_up(limited_client, f"user{index}@example.com", ip="192.0.2.60").status_code == 201

    blocked = sign_up(limited_client, "one-too-many@example.com", ip="192.0.2.60")

    assert blocked.status_code == 429
    assert blocked.get_json()["error"]["code"] == "too_many_attempts"
    assert int(blocked.headers["Retry-After"]) > 0

    # Another address is unaffected.
    assert sign_up(limited_client, "elsewhere@example.com", ip="192.0.2.61").status_code == 201


def test_separate_addresses_keep_separate_counters(limited_client):
    # One failure per account, so only the address counter can reach its limit.
    for index in range(RateLimitConfig.LOGIN_IP_MAX_FAILURES):
        response = login(limited_client, email=f"user{index}@example.com", ip="192.0.2.70")
        assert response.status_code == 401, index

    assert login(limited_client, email="blocked@example.com", ip="192.0.2.70").status_code == 429
    assert login(limited_client, email="elsewhere@example.com", ip="192.0.2.71").status_code == 401


def test_limits_are_inactive_when_disabled(client):
    # The default test configuration switches the limits off; this confirms the
    # switch is honoured and that the rest of the suite is not silently throttled.
    for _ in range(25):
        assert login(client, ip="192.0.2.80").status_code == 401


PARALLEL_FAILURES = 8


@pytest.fixture()
def racing_app(tmp_path):
    """A file-backed database, so each thread opens its own connection."""
    application = create_app(
        {
            "TESTING": True,
            "SQLALCHEMY_DATABASE_URI": f"sqlite:///{(tmp_path / 'throttle.db').as_posix()}",
            # Let SQLite serialise writers instead of failing fast, so the test
            # measures the counter statements rather than a busy timeout.
            "SQLALCHEMY_ENGINE_OPTIONS": {"connect_args": {"timeout": 30}},
            "JWT_SECRET_KEY": "teamflow-throttle-jwt-secret-key-32-bytes",
            "SECRET_KEY": "teamflow-throttle-flask-secret-key-32-bytes",
            "RATE_LIMIT_ENABLED": True,
            # High limits keep both scopes unblocked, so every failure is counted.
            "LOGIN_IP_MAX_FAILURES": 100,
            "LOGIN_ACCOUNT_MAX_FAILURES": 100,
            "LOGIN_FAILURE_WINDOW_SECONDS": 900,
            "LOGIN_BACKOFF_BASE_SECONDS": 60,
            "LOGIN_BACKOFF_MAX_SECONDS": 240,
            "REGISTER_IP_MAX_ATTEMPTS": 100,
            "REGISTER_WINDOW_SECONDS": 3600,
        }
    )
    with application.app_context():
        db.create_all()
    yield application
    with application.app_context():
        db.session.remove()
        # Windows keeps the file handle open until the pool is disposed.
        db.engine.dispose()


def test_parallel_failed_logins_are_all_counted(racing_app):
    """Simultaneous failures must not read one total and overwrite each other."""
    barrier = threading.Barrier(PARALLEL_FAILURES)
    results = []
    failures = []

    def attempt(index):
        client = racing_app.test_client()
        try:
            barrier.wait(timeout=20)
            response = client.post(
                "/api/v1/auth/login",
                json={"email": "ada@example.com", "password": "wrong-password"},
                # One account attacked from many addresses, so only the account
                # counter can be affected by the lost-update race.
                environ_base={"REMOTE_ADDR": f"198.51.100.{index}"},
            )
            results.append(response.status_code)
        except Exception as exc:  # noqa: BLE001 - a thread exception must fail the test
            failures.append(repr(exc))

    threads = [
        threading.Thread(target=attempt, args=(index,), name=f"login-{index}")
        for index in range(PARALLEL_FAILURES)
    ]
    for thread in threads:
        thread.start()
    for thread in threads:
        thread.join(timeout=30)

    assert not any(thread.is_alive() for thread in threads), "a login thread did not terminate"
    assert failures == []
    assert results == [401] * PARALLEL_FAILURES

    with racing_app.app_context():
        attempts = db.session.scalar(
            db.select(RateLimit.attempts).where(
                RateLimit.scope == LOGIN_ACCOUNT, RateLimit.key == account_key("ada@example.com")
            )
        )
        db.session.remove()

    assert attempts == PARALLEL_FAILURES


def add_counter(scope, key, window_started_at, blocked_until=None):
    db.session.add(
        RateLimit(
            scope=scope,
            key=key,
            attempts=3,
            window_started_at=window_started_at,
            blocked_until=blocked_until,
            updated_at=window_started_at,
        )
    )
    db.session.commit()


def test_pruning_removes_only_counters_that_can_no_longer_matter(limited_app):
    now = utcnow()
    add_counter(LOGIN_IP, digest("expired-address"), now - timedelta(hours=2))
    add_counter(
        LOGIN_ACCOUNT,
        digest("expired@example.com"),
        now - timedelta(hours=2),
        now - timedelta(minutes=30),
    )
    add_counter(
        LOGIN_ACCOUNT,
        digest("locked@example.com"),
        now - timedelta(hours=2),
        now + timedelta(minutes=30),
    )
    add_counter(LOGIN_ACCOUNT, digest("recent@example.com"), now - timedelta(minutes=1))
    add_counter(REGISTER_IP, digest("expired-register"), now - timedelta(hours=2))
    add_counter(REGISTER_IP, digest("live-register"), now - timedelta(minutes=30))

    assert prune_expired() == 3

    remaining = {
        (bucket.scope, bucket.key) for bucket in db.session.scalars(db.select(RateLimit)).all()
    }

    # A block that has not ended outlives its counting window; a live window is
    # kept even when its row is old enough for the shorter login window.
    assert remaining == {
        (LOGIN_ACCOUNT, digest("locked@example.com")),
        (LOGIN_ACCOUNT, digest("recent@example.com")),
        (REGISTER_IP, digest("live-register")),
    }


def test_prune_command_reports_removed_counters(limited_app):
    add_counter(LOGIN_IP, digest("expired-address"), utcnow() - timedelta(hours=2))

    result = limited_app.test_cli_runner().invoke(args=["prune-rate-limits"])

    assert result.exit_code == 0, result.output
    assert "Removed 1" in result.output
    assert db.session.scalar(db.select(db.func.count()).select_from(RateLimit)) == 0
