"""Reusable test helpers."""

def register(client, name="Ada Lovelace", email="ada@example.com", password="CorrectHorse123!"):
    response = client.post(
        "/api/v1/auth/register",
        json={"name": name, "email": email, "password": password},
    )
    assert response.status_code == 201, response.get_json()
    return response.get_json()["data"]


def auth_headers(access_token: str) -> dict[str, str]:
    return {"Authorization": f"Bearer {access_token}"}
