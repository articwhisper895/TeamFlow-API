"""Tests for persistent refresh sessions, rotation, replay detection and logout."""

from datetime import timedelta

from flask_jwt_extended import create_refresh_token

from teamflow import sessions
from teamflow.extensions import db
from teamflow.models import RefreshSession, RefreshToken, User
from teamflow.models.session import utcnow
from tests.helpers import auth_headers, register

PASSWORD = "CorrectHorse123!"


def refresh(client, token):
    return client.post("/api/v1/auth/refresh", headers=auth_headers(token))


def logout(client, token):
    return client.post("/api/v1/auth/logout", headers=auth_headers(token))


def login(client, email="ada@example.com", password=PASSWORD):
    response = client.post("/api/v1/auth/login", json={"email": email, "password": password})
    assert response.status_code == 200, response.get_json()
    return response.get_json()["data"]


def sessions_for(user_id):
    return db.session.scalars(
        db.select(RefreshSession).where(RefreshSession.user_id == user_id)
    ).all()


def token_rows_for(session_id):
    return db.session.scalars(
        db.select(RefreshToken).where(RefreshToken.session_id == session_id)
    ).all()


def delete_account(user_id):
    user = db.session.get(User, user_id)
    assert user is not None
    db.session.delete(user)
    db.session.commit()


def test_registration_creates_an_initial_refresh_session(client):
    account = register(client)

    session = db.session.get(RefreshSession, account["session_id"])
    tokens = token_rows_for(account["session_id"])

    assert session is not None
    assert session.user_id == account["user"]["id"]
    assert session.revoked_at is None
    assert len(tokens) == 1
    assert tokens[0].consumed_at is None
    # The stored value is an identifier, never the token the client received.
    assert tokens[0].jti not in account["refresh_token"]
    # The issued token is usable, which proves it matches the stored identifier.
    assert refresh(client, account["refresh_token"]).status_code == 200


def test_login_creates_an_independent_session(client):
    registered = register(client)
    logged_in = login(client)

    assert registered["session_id"] != logged_in["session_id"]
    assert len(sessions_for(registered["user"]["id"])) == 2
    # Both sessions can refresh without invalidating each other.
    assert refresh(client, registered["refresh_token"]).status_code == 200
    assert refresh(client, logged_in["refresh_token"]).status_code == 200


def test_valid_refresh_rotates_the_token_pair(client):
    account = register(client)

    response = refresh(client, account["refresh_token"])

    assert response.status_code == 200
    data = response.get_json()["data"]
    assert data["refresh_token"] and data["refresh_token"] != account["refresh_token"]
    assert data["refresh_token_rotated"] is True
    assert data["session_id"] == account["session_id"]
    me = client.get("/api/v1/auth/me", headers=auth_headers(data["access_token"]))
    assert me.status_code == 200

    rows = token_rows_for(account["session_id"])
    assert len(rows) == 2
    assert sum(1 for row in rows if row.consumed_at is not None) == 1
    assert [row for row in rows if row.replaced_by_id is not None][0].replaced_by_id


def test_old_refresh_token_is_rejected_after_rotation(client):
    account = register(client)
    rotated = refresh(client, account["refresh_token"]).get_json()["data"]

    replay = refresh(client, account["refresh_token"])

    assert replay.status_code == 401
    assert replay.get_json()["error"]["code"] == "refresh_token_reused"
    # The replacement remains the only working credential until the family is revoked.
    assert refresh(client, rotated["refresh_token"]).status_code == 401


def test_replaying_a_consumed_token_revokes_its_family(client):
    account = register(client)
    rotated = refresh(client, account["refresh_token"]).get_json()["data"]

    refresh(client, account["refresh_token"])  # replay of the consumed token
    blocked = refresh(client, rotated["refresh_token"])

    session = db.session.get(RefreshSession, account["session_id"])
    assert session.revoked_at is not None
    assert session.revoked_reason == "refresh_token_reuse"
    assert blocked.status_code == 401
    assert blocked.get_json()["error"]["code"] == "session_revoked"


def test_replay_only_revokes_the_affected_session(client):
    account = register(client)
    other_device = login(client)
    rotated = refresh(client, account["refresh_token"]).get_json()["data"]

    refresh(client, account["refresh_token"])
    assert refresh(client, rotated["refresh_token"]).status_code == 401

    still_valid = refresh(client, other_device["refresh_token"])
    assert still_valid.status_code == 200
    assert db.session.get(RefreshSession, other_device["session_id"]).revoked_at is None


def test_logout_revokes_the_current_session(client):
    account = register(client)

    response = logout(client, account["refresh_token"])

    assert response.status_code == 200
    assert response.get_json()["data"] == {
        "session_id": account["session_id"],
        "revoked": True,
        "already_revoked": False,
    }
    blocked = refresh(client, account["refresh_token"])
    assert blocked.status_code == 401
    assert blocked.get_json()["error"]["code"] == "session_revoked"


def test_logout_leaves_other_sessions_usable(client):
    account = register(client)
    phone = login(client)
    laptop = login(client)

    assert logout(client, phone["refresh_token"]).status_code == 200

    assert refresh(client, phone["refresh_token"]).get_json()["error"]["code"] == "session_revoked"
    assert refresh(client, laptop["refresh_token"]).status_code == 200
    assert db.session.get(RefreshSession, account["session_id"]).revoked_at is None


def test_logout_is_idempotent(client):
    account = register(client)

    first = logout(client, account["refresh_token"])
    second = logout(client, account["refresh_token"])

    assert first.status_code == 200
    assert second.status_code == 200
    assert second.get_json()["data"]["already_revoked"] is True


def test_logout_requires_a_refresh_token(client):
    account = register(client)

    missing = client.post("/api/v1/auth/logout")
    with_access_token = logout(client, account["access_token"])

    assert missing.status_code == 401
    assert with_access_token.status_code == 401
    assert with_access_token.get_json()["error"]["code"] == "invalid_token"


def test_expired_refresh_token_is_rejected(client):
    account = register(client)
    expired = create_refresh_token(
        identity=str(account["user"]["id"]),
        additional_claims={"sid": account["session_id"]},
        expires_delta=timedelta(seconds=-1),
    )

    response = refresh(client, expired)

    assert response.status_code == 401
    assert response.get_json()["error"]["code"] == "expired_token"


def test_database_expiry_ends_a_session_even_if_the_jwt_is_still_valid(client):
    account = register(client)
    session = db.session.get(RefreshSession, account["session_id"])
    session.expires_at = utcnow() - timedelta(seconds=1)
    db.session.commit()

    response = refresh(client, account["refresh_token"])

    assert response.status_code == 401
    assert response.get_json()["error"]["code"] == "expired_token"


def test_deleted_account_cannot_refresh_and_loses_its_sessions(client):
    account = register(client)
    delete_account(account["user"]["id"])

    response = refresh(client, account["refresh_token"])

    assert response.status_code == 401
    assert response.get_json()["error"]["code"] == "account_not_found"
    assert db.session.get(RefreshSession, account["session_id"]) is None


def test_access_token_cannot_refresh(client):
    account = register(client)

    response = refresh(client, account["access_token"])

    assert response.status_code == 401
    assert response.get_json()["error"]["code"] == "invalid_token"


def test_token_for_another_users_session_is_rejected(client):
    owner = register(client)
    intruder = register(client, name="Grace Hopper", email="grace@example.com")
    forged_session_claim = create_refresh_token(
        identity=str(intruder["user"]["id"]),
        additional_claims={"sid": owner["session_id"]},
    )

    response = refresh(client, forged_session_claim)

    assert response.status_code == 401
    assert response.get_json()["error"]["code"] == "invalid_refresh_token"


def test_failed_refresh_attempts_do_not_create_sessions_or_tokens(client):
    account = register(client)
    session_count = len(sessions_for(account["user"]["id"]))
    token_count = len(token_rows_for(account["session_id"]))

    invalid = refresh(client, "not-a-jwt")
    unknown_session = create_refresh_token(
        identity=str(account["user"]["id"]),
        additional_claims={"sid": "0" * 32},
    )
    unknown = refresh(client, unknown_session)
    replay = refresh(client, account["refresh_token"])
    replayed = refresh(client, account["refresh_token"])

    assert invalid.get_json()["error"]["code"] == "invalid_token"
    assert unknown.get_json()["error"]["code"] == "invalid_refresh_token"
    assert replay.status_code == 200
    assert replayed.get_json()["error"]["code"] == "refresh_token_reused"
    assert len(sessions_for(account["user"]["id"])) == session_count
    # One replacement was issued by the successful refresh; the replay added none.
    assert len(token_rows_for(account["session_id"])) == token_count + 1


def test_database_failure_during_rotation_leaves_the_old_token_usable(client, monkeypatch):
    account = register(client)
    original = token_rows_for(account["session_id"])[0]
    original_id = original.id

    # A pre-existing row with this identifier makes the replacement insert fail
    # on the unique index, exercising the rollback path inside rotate().
    db.session.add(
        RefreshToken(
            session_id=account["session_id"],
            jti="forced-duplicate-jti",
            expires_at=utcnow() + timedelta(days=1),
        )
    )
    db.session.commit()

    real_decode_token = sessions.decode_token
    monkeypatch.setattr(sessions, "decode_token", lambda _token: {"jti": "forced-duplicate-jti"})
    failed = refresh(client, account["refresh_token"])

    assert failed.status_code == 500
    assert failed.get_json()["error"]["code"] == "refresh_failed"

    monkeypatch.setattr(sessions, "decode_token", real_decode_token)
    db.session.rollback()
    restored = db.session.get(RefreshToken, original_id)
    assert restored.consumed_at is None
    assert restored.replaced_by_id is None
    # Because the claim was rolled back, the caller can retry with the same token.
    assert refresh(client, account["refresh_token"]).status_code == 200


def test_claiming_a_token_twice_only_succeeds_once(app, client):
    """The atomic guard itself: the second claim cannot win, even after a stale read."""
    account = register(client)
    token_row = token_rows_for(account["session_id"])[0]

    first = sessions.claim_refresh_token(token_row.id)
    db.session.commit()
    second = sessions.claim_refresh_token(token_row.id)
    db.session.rollback()

    assert first is True
    assert second is False


def test_revoked_session_cannot_be_revived_by_rotation(client):
    account = register(client)
    session = db.session.get(RefreshSession, account["session_id"])
    session.revoke("logout")
    db.session.commit()

    response = refresh(client, account["refresh_token"])

    assert response.status_code == 401
    assert response.get_json()["error"]["code"] == "session_revoked"
    assert len(token_rows_for(account["session_id"])) == 1
