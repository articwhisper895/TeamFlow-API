"""Real parallel refresh requests against a file-backed SQLite database.

``tests/test_refresh_sessions.py`` proves the conditional UPDATE claims a row
once when the statements run in sequence. That is a unit-level guarantee. This
module exercises the actual HTTP endpoint from two genuine threads, each with
its own Flask test client and its own database connection, so the single-use
guarantee is demonstrated under real contention rather than simulated.
"""

import threading
from datetime import timedelta

import pytest

from teamflow import create_app
from teamflow.extensions import db
from teamflow.models import RefreshSession, RefreshToken
from tests.helpers import auth_headers

ROUNDS = 10


@pytest.fixture()
def file_app(tmp_path):
    """An application backed by a real SQLite file, not the shared in-memory one."""
    application = create_app(
        {
            "TESTING": True,
            "SQLALCHEMY_DATABASE_URI": f"sqlite:///{(tmp_path / 'refresh-race.db').as_posix()}",
            # Give SQLite time to serialise writers instead of failing fast,
            # so the test measures the claim logic rather than a busy timeout.
            "SQLALCHEMY_ENGINE_OPTIONS": {"connect_args": {"timeout": 15}},
            "JWT_SECRET_KEY": "teamflow-race-jwt-secret-key-32-bytes-min",
            "SECRET_KEY": "teamflow-race-flask-secret-key-32-bytes",
            "JWT_REFRESH_TOKEN_EXPIRES": timedelta(days=1),
        }
    )
    with application.app_context():
        db.create_all()
    yield application
    with application.app_context():
        db.session.remove()
        # Windows keeps the file handle open until the pool is disposed.
        db.engine.dispose()


def _register(application, round_number):
    response = application.test_client().post(
        "/api/v1/auth/register",
        json={
            "name": "Race Condition",
            "email": f"race-{round_number}@example.com",
            "password": "CorrectHorse123!",
        },
    )
    assert response.status_code == 201, response.get_json()
    return response.get_json()["data"]


@pytest.mark.parametrize("round_number", range(ROUNDS))
def test_parallel_refresh_has_one_winner_and_revokes_the_family(file_app, round_number):
    account = _register(file_app, round_number)
    barrier = threading.Barrier(2)
    results = []
    failures = []

    def attempt():
        client = file_app.test_client()
        try:
            barrier.wait(timeout=15)
            response = client.post(
                "/api/v1/auth/refresh",
                headers=auth_headers(account["refresh_token"]),
            )
            body = response.get_json()
            results.append(
                (
                    response.status_code,
                    (body.get("error") or {}).get("code"),
                    (body.get("data") or {}).get("refresh_token"),
                )
            )
        except Exception as exc:  # noqa: BLE001 - a thread exception must fail the test
            failures.append(repr(exc))

    threads = [threading.Thread(target=attempt, name=f"refresh-{index}") for index in range(2)]
    for thread in threads:
        thread.start()
    for thread in threads:
        thread.join(timeout=30)

    assert not any(thread.is_alive() for thread in threads), "a refresh thread did not terminate"
    assert failures == []
    assert len(results) == 2

    statuses = sorted(status for status, _code, _token in results)
    assert statuses == [200, 401]

    winner = next(result for result in results if result[0] == 200)
    loser = next(result for result in results if result[0] == 401)
    assert loser[1] == "refresh_token_reused"

    with file_app.app_context():
        session = db.session.get(RefreshSession, account["session_id"])
        tokens = db.session.scalars(
            db.select(RefreshToken).where(RefreshToken.session_id == account["session_id"])
        ).all()

        assert session.revoked_at is not None
        assert session.revoked_reason == "refresh_token_reuse"
        # The winner's replacement exists, and exactly one token is unconsumed.
        assert len(tokens) == 2
        assert sum(1 for token in tokens if token.consumed_at is None) == 1
        db.session.remove()

    # The replacement handed to the winner is unusable because its family died.
    blocked = file_app.test_client().post(
        "/api/v1/auth/refresh",
        headers=auth_headers(winner[2]),
    )
    assert blocked.status_code == 401
    assert blocked.get_json()["error"]["code"] == "session_revoked"
