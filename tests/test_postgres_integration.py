"""PostgreSQL integration checks, opt-in via ``TEAMFLOW_POSTGRES_URL``.

These tests need a disposable PostgreSQL database, because they run the real
Alembic migrations and drop the schema again afterwards:

    set TEAMFLOW_POSTGRES_URL=postgresql+psycopg://user:pass@localhost:5432/teamflow_test
    pytest tests/test_postgres_integration.py -q

They are skipped when the variable is unset, so the default SQLite suite is
unchanged. CI runs them in the ``postgres-integration`` job. They have not been
executed on the development machine (no PostgreSQL server or container runtime
was available), so PostgreSQL compatibility is unverified until that job runs.
"""

import os
import threading
from datetime import timedelta
from pathlib import Path

import pytest
from flask_migrate import downgrade, upgrade
from sqlalchemy import text

from teamflow import create_app
from teamflow.extensions import db
from teamflow.models import RefreshSession, RefreshToken
from tests.helpers import auth_headers

POSTGRES_URL = os.getenv("TEAMFLOW_POSTGRES_URL")
MIGRATIONS = str(Path(__file__).resolve().parents[1] / "migrations")

pytestmark = pytest.mark.skipif(
    not POSTGRES_URL,
    reason="Set TEAMFLOW_POSTGRES_URL to run the PostgreSQL integration tests.",
)


@pytest.fixture()
def pg_app():
    """Migrate a disposable PostgreSQL database and remove the schema afterwards."""
    application = create_app(
        {
            "TESTING": True,
            "SQLALCHEMY_DATABASE_URI": POSTGRES_URL,
            "JWT_SECRET_KEY": "teamflow-postgres-jwt-secret-key-32-bytes-min",
            "SECRET_KEY": "teamflow-postgres-flask-secret-key-32-bytes",
            "JWT_REFRESH_TOKEN_EXPIRES": timedelta(days=1),
        }
    )
    with application.app_context():
        upgrade(directory=MIGRATIONS)
        try:
            yield application
        finally:
            db.session.remove()
            downgrade(directory=MIGRATIONS, revision="base")


def _register(client, suffix):
    response = client.post(
        "/api/v1/auth/register",
        json={
            "name": "Postgres User",
            "email": f"pg-{suffix}@example.com",
            "password": "CorrectHorse123!",
        },
    )
    assert response.status_code == 201, response.get_json()
    return response.get_json()["data"]


def test_migrations_create_the_session_schema(pg_app):
    with pg_app.app_context():
        tables = set(
            db.session.execute(
                text("SELECT tablename FROM pg_tables WHERE schemaname = 'public'")
            ).scalars()
        )
        version = db.session.execute(text("SELECT version_num FROM alembic_version")).scalar_one()

    assert {"users", "refresh_sessions", "refresh_tokens"} <= tables
    assert version


def test_refresh_uses_aware_timestamps_and_rotates(pg_app):
    client = pg_app.test_client()
    account = _register(client, "aware")

    first = client.post("/api/v1/auth/refresh", headers=auth_headers(account["refresh_token"]))
    assert first.status_code == 200, first.get_json()
    data = first.get_json()["data"]
    assert data["refresh_token_expires_at"].endswith("Z")

    with pg_app.app_context():
        session = db.session.get(RefreshSession, account["session_id"])
        tokens = db.session.scalars(
            db.select(RefreshToken).where(RefreshToken.session_id == account["session_id"])
        ).all()

        # PostgreSQL returns timezone-aware values; naive/aware comparisons in
        # the session code would raise here if the normalization were missing.
        assert session.expires_at.tzinfo is not None
        assert len(tokens) == 2
        consumed = [token for token in tokens if token.consumed_at is not None]
        assert len(consumed) == 1
        assert consumed[0].consumed_at.tzinfo is not None
        db.session.remove()

    second = client.post(
        "/api/v1/auth/refresh", headers=auth_headers(data["refresh_token"])
    )
    assert second.status_code == 200, second.get_json()


@pytest.mark.parametrize("round_number", range(5))
def test_concurrent_refresh_allows_one_winner(pg_app, round_number):
    account = _register(pg_app.test_client(), f"race-{round_number}")
    barrier = threading.Barrier(2)
    results = []
    failures = []

    def attempt():
        client = pg_app.test_client()
        try:
            barrier.wait(timeout=15)
            response = client.post(
                "/api/v1/auth/refresh",
                headers=auth_headers(account["refresh_token"]),
            )
            body = response.get_json()
            results.append((response.status_code, (body.get("error") or {}).get("code")))
        except Exception as exc:  # noqa: BLE001 - surface thread failures in the test
            failures.append(repr(exc))

    threads = [threading.Thread(target=attempt) for _ in range(2)]
    for thread in threads:
        thread.start()
    for thread in threads:
        thread.join(timeout=30)

    assert failures == []
    assert sorted(status for status, _code in results) == [200, 401]
    assert next(code for status, code in results if status == 401) == "refresh_token_reused"


def test_deleting_a_user_cascades_refresh_sessions(pg_app):
    account = _register(pg_app.test_client(), "cascade")

    with pg_app.app_context():
        db.session.execute(
            text("DELETE FROM users WHERE id = :user_id"),
            {"user_id": account["user"]["id"]},
        )
        db.session.commit()

        sessions = db.session.execute(text("SELECT count(*) FROM refresh_sessions")).scalar_one()
        tokens = db.session.execute(text("SELECT count(*) FROM refresh_tokens")).scalar_one()

    # A raw SQL delete bypasses the ORM cascade, so this proves the database
    # foreign keys are doing the work.
    assert (sessions, tokens) == (0, 0)
