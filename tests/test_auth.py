from datetime import timedelta

from flask_jwt_extended import create_access_token

from teamflow.extensions import db
from teamflow.models import User
from tests.helpers import auth_headers, register


def delete_account(user_id):
    """Remove an account directly, standing in for a future account-deletion feature."""
    user = db.session.get(User, user_id)
    assert user is not None
    db.session.delete(user)
    db.session.commit()


def create_project(client, token, name="Launch website"):
    return client.post(
        "/api/v1/projects",
        headers=auth_headers(token),
        json={"name": name, "description": "A project used to test the API."},
    )


def test_register_returns_user_and_token_pair(client):
    payload = register(client)

    assert payload["user"]["email"] == "ada@example.com"
    assert payload["access_token"]
    assert payload["refresh_token"]
    assert "password_hash" not in payload["user"]


def test_register_normalizes_email(client):
    payload = register(client, email=" ADA@EXAMPLE.COM ")

    assert payload["user"]["email"] == "ada@example.com"


def test_register_rejects_weak_password(client):
    response = client.post(
        "/api/v1/auth/register",
        json={"name": "Ada", "email": "ada@example.com", "password": "short"},
    )

    assert response.status_code == 400
    assert response.get_json()["error"]["code"] == "validation_error"


def test_register_rejects_duplicate_email(client):
    register(client)
    response = client.post(
        "/api/v1/auth/register",
        json={"name": "Another Ada", "email": "ada@example.com", "password": "CorrectHorse123!"},
    )

    assert response.status_code == 409


def test_login_and_current_user(client):
    register(client)
    response = client.post(
        "/api/v1/auth/login",
        json={"email": "ada@example.com", "password": "CorrectHorse123!"},
    )
    token = response.get_json()["data"]["access_token"]
    me = client.get("/api/v1/auth/me", headers=auth_headers(token))

    assert response.status_code == 200
    assert me.status_code == 200
    assert me.get_json()["data"]["user"]["name"] == "Ada Lovelace"


def test_login_uses_generic_invalid_credentials_error(client):
    register(client)
    response = client.post(
        "/api/v1/auth/login",
        json={"email": "ada@example.com", "password": "wrong-password"},
    )

    assert response.status_code == 401
    assert response.get_json()["error"]["code"] == "invalid_credentials"


def test_protected_route_requires_token(client):
    response = client.get("/api/v1/projects")

    assert response.status_code == 401


def test_refresh_token_returns_new_access_token(client):
    payload = register(client)
    response = client.post(
        "/api/v1/auth/refresh",
        headers=auth_headers(payload["refresh_token"]),
    )

    assert response.status_code == 200
    assert response.get_json()["data"]["access_token"]


def test_access_token_cannot_be_used_to_refresh(client):
    payload = register(client)

    response = client.post(
        "/api/v1/auth/refresh",
        headers=auth_headers(payload["access_token"]),
    )

    assert response.status_code != 200
    assert response.get_json()["error"]["code"] == "invalid_token"


def test_refresh_token_cannot_be_used_as_access_token(client):
    payload = register(client)

    response = client.get("/api/v1/projects", headers=auth_headers(payload["refresh_token"]))

    assert response.status_code == 401
    assert response.get_json()["error"]["code"] == "invalid_token"


def test_login_rejects_unknown_fields(client):
    register(client)

    response = client.post(
        "/api/v1/auth/login",
        json={"email": "ada@example.com", "password": "CorrectHorse123!", "remember_me": True},
    )

    assert response.status_code == 400
    assert response.get_json()["error"]["code"] == "validation_error"
    assert "remember_me" in response.get_json()["error"]["message"]


def test_login_email_is_case_insensitive(client):
    register(client, email="ada@example.com")

    response = client.post(
        "/api/v1/auth/login",
        json={"email": "  ADA@Example.COM ", "password": "CorrectHorse123!"},
    )

    assert response.status_code == 200
    assert response.get_json()["data"]["user"]["email"] == "ada@example.com"


def test_register_rejects_invalid_email(client):
    response = client.post(
        "/api/v1/auth/register",
        json={"name": "Ada", "email": "not-an-email", "password": "CorrectHorse123!"},
    )

    assert response.status_code == 400


def test_login_does_not_reveal_whether_an_email_exists(client):
    register(client)

    wrong_password = client.post(
        "/api/v1/auth/login",
        json={"email": "ada@example.com", "password": "not-the-password"},
    )
    unknown_email = client.post(
        "/api/v1/auth/login",
        json={"email": "nobody@example.com", "password": "CorrectHorse123!"},
    )

    assert wrong_password.status_code == unknown_email.status_code == 401
    assert wrong_password.get_json() == unknown_email.get_json()


def test_deleted_account_access_token_is_rejected_on_project_routes(client):
    account = register(client)
    delete_account(account["user"]["id"])

    response = client.get("/api/v1/projects", headers=auth_headers(account["access_token"]))

    assert response.status_code == 401
    assert response.get_json()["error"]["code"] == "account_not_found"


def test_deleted_account_access_token_is_rejected_on_me(client):
    account = register(client)
    delete_account(account["user"]["id"])

    response = client.get("/api/v1/auth/me", headers=auth_headers(account["access_token"]))

    assert response.status_code == 401
    assert response.get_json()["error"]["code"] == "account_not_found"


def test_deleted_account_refresh_token_is_rejected(client):
    account = register(client)
    delete_account(account["user"]["id"])

    response = client.post(
        "/api/v1/auth/refresh", headers=auth_headers(account["refresh_token"])
    )

    assert response.status_code == 401
    assert response.get_json()["error"]["code"] == "account_not_found"


def test_expired_token_is_not_reported_as_a_missing_account(client):
    account = register(client)
    expired = create_access_token(
        identity=str(account["user"]["id"]), expires_delta=timedelta(seconds=-1)
    )

    response = client.get("/api/v1/projects", headers=auth_headers(expired))

    assert response.status_code == 401
    assert response.get_json()["error"]["code"] == "expired_token"


def test_deleted_member_token_cannot_write_or_change_data(client):
    owner = register(client)
    member = register(client, name="Grace Hopper", email="grace@example.com")
    project = create_project(client, owner["access_token"]).get_json()["data"]
    client.post(
        f"/api/v1/projects/{project['id']}/members",
        headers=auth_headers(owner["access_token"]),
        json={"email": "grace@example.com"},
    )
    client.post(
        f"/api/v1/projects/{project['id']}/tasks",
        headers=auth_headers(owner["access_token"]),
        json={"title": "Existing task"},
    )
    delete_account(member["user"]["id"])

    denied_create = client.post(
        f"/api/v1/projects/{project['id']}/tasks",
        headers=auth_headers(member["access_token"]),
        json={"title": "Ghost task"},
    )
    denied_update = client.patch(
        f"/api/v1/projects/{project['id']}",
        headers=auth_headers(member["access_token"]),
        json={"name": "Ghost rename"},
    )

    owner_projects = client.get(
        "/api/v1/projects", headers=auth_headers(owner["access_token"])
    )
    owner_tasks = client.get(
        f"/api/v1/projects/{project['id']}/tasks",
        headers=auth_headers(owner["access_token"]),
    )
    members = client.get(
        f"/api/v1/projects/{project['id']}/members",
        headers=auth_headers(owner["access_token"]),
    )

    assert denied_create.status_code == 401
    assert denied_create.get_json()["error"]["code"] == "account_not_found"
    assert denied_update.status_code == 401
    assert denied_update.get_json()["error"]["code"] == "account_not_found"
    assert owner_projects.status_code == 200
    assert owner_projects.get_json()["data"][0]["name"] == "Launch website"
    assert owner_tasks.get_json()["meta"]["total"] == 1
    assert [task["title"] for task in owner_tasks.get_json()["data"]] == ["Existing task"]
    assert [item["email"] for item in members.get_json()["data"]] == ["ada@example.com"]


def test_register_rejects_unknown_fields(client):
    response = client.post(
        "/api/v1/auth/register",
        json={
            "name": "Ada",
            "email": "ada@example.com",
            "password": "CorrectHorse123!",
            "nickname": "ada",
        },
    )

    assert response.status_code == 400
    assert response.get_json()["error"]["code"] == "validation_error"
    assert "nickname" in response.get_json()["error"]["message"]

    # A rejected payload must not create a partially valid account.
    login = client.post(
        "/api/v1/auth/login",
        json={"email": "ada@example.com", "password": "CorrectHorse123!"},
    )
    assert login.status_code == 401


def test_register_cannot_assign_privilege_fields(client):
    response = client.post(
        "/api/v1/auth/register",
        json={
            "name": "Ada",
            "email": "ada@example.com",
            "password": "CorrectHorse123!",
            "id": 1,
            "is_admin": True,
        },
    )

    assert response.status_code == 400
    assert response.get_json()["error"]["code"] == "validation_error"
