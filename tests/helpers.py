"""Reusable test helpers."""

PASSWORD = "CorrectHorse123!"


def register(client, name="Ada Lovelace", email="ada@example.com", password=PASSWORD):
    response = client.post(
        "/api/v1/auth/register",
        json={"name": name, "email": email, "password": password},
    )
    assert response.status_code == 201, response.get_json()
    return response.get_json()["data"]


def auth_headers(access_token: str) -> dict[str, str]:
    return {"Authorization": f"Bearer {access_token}"}


def create_project(client, token, name="Launch website"):
    return client.post(
        "/api/v1/projects",
        headers=auth_headers(token),
        json={"name": name, "description": "A project used to test the API."},
    )
