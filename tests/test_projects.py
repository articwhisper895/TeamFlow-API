from teamflow.extensions import db
from teamflow.models import ProjectMembership, Task
from tests.helpers import auth_headers, register


def create_project(client, token, name="Launch website"):
    return client.post(
        "/api/v1/projects",
        headers=auth_headers(token),
        json={"name": name, "description": "A project used to test the API."},
    )


def test_owner_can_create_and_list_project(client):
    account = register(client)
    created = create_project(client, account["access_token"])
    listed = client.get("/api/v1/projects", headers=auth_headers(account["access_token"]))

    assert created.status_code == 201
    project = created.get_json()["data"]
    assert project["members"][0]["role"] == "owner"
    assert listed.status_code == 200
    assert listed.get_json()["data"][0]["id"] == project["id"]


def test_project_is_hidden_from_non_members(client):
    owner = register(client)
    outsider = register(client, name="Grace Hopper", email="grace@example.com")
    project = create_project(client, owner["access_token"]).get_json()["data"]

    response = client.get(
        f"/api/v1/projects/{project['id']}",
        headers=auth_headers(outsider["access_token"]),
    )

    assert response.status_code == 404


def test_owner_can_add_member_and_member_can_see_project(client):
    owner = register(client)
    member = register(client, name="Grace Hopper", email="grace@example.com")
    project = create_project(client, owner["access_token"]).get_json()["data"]

    added = client.post(
        f"/api/v1/projects/{project['id']}/members",
        headers=auth_headers(owner["access_token"]),
        json={"email": "grace@example.com"},
    )
    visible = client.get(
        f"/api/v1/projects/{project['id']}",
        headers=auth_headers(member["access_token"]),
    )

    assert added.status_code == 201
    assert visible.status_code == 200


def test_non_owner_cannot_add_members(client):
    owner = register(client)
    member = register(client, name="Grace Hopper", email="grace@example.com")
    project = create_project(client, owner["access_token"]).get_json()["data"]
    client.post(
        f"/api/v1/projects/{project['id']}/members",
        headers=auth_headers(owner["access_token"]),
        json={"email": "grace@example.com"},
    )

    response = client.post(
        f"/api/v1/projects/{project['id']}/members",
        headers=auth_headers(member["access_token"]),
        json={"email": "owner@example.com"},
    )

    assert response.status_code == 403


def test_non_owner_cannot_update_project(client):
    owner = register(client)
    member = register(client, name="Grace Hopper", email="grace@example.com")
    project = create_project(client, owner["access_token"]).get_json()["data"]
    client.post(
        f"/api/v1/projects/{project['id']}/members",
        headers=auth_headers(owner["access_token"]),
        json={"email": "grace@example.com"},
    )

    response = client.patch(
        f"/api/v1/projects/{project['id']}",
        headers=auth_headers(member["access_token"]),
        json={"name": "Hijacked project"},
    )

    assert response.status_code == 403


def test_project_pagination_rejects_invalid_limit(client):
    owner = register(client)
    response = client.get(
        "/api/v1/projects?limit=500",
        headers=auth_headers(owner["access_token"]),
    )

    assert response.status_code == 400


def test_removing_member_clears_their_task_assignments(client):
    owner = register(client)
    member = register(client, name="Grace Hopper", email="grace@example.com")
    project = create_project(client, owner["access_token"]).get_json()["data"]
    client.post(
        f"/api/v1/projects/{project['id']}/members",
        headers=auth_headers(owner["access_token"]),
        json={"email": "grace@example.com"},
    )
    task = client.post(
        f"/api/v1/projects/{project['id']}/tasks",
        headers=auth_headers(owner["access_token"]),
        json={"title": "Write report", "assigned_to_id": member["user"]["id"]},
    ).get_json()["data"]

    removed = client.delete(
        f"/api/v1/projects/{project['id']}/members/{member['user']['id']}",
        headers=auth_headers(owner["access_token"]),
    )
    updated_task = client.get(
        f"/api/v1/tasks/{task['id']}",
        headers=auth_headers(owner["access_token"]),
    )

    assert removed.status_code == 204
    assert updated_task.get_json()["data"]["assigned_to_id"] is None



def test_removed_member_cannot_access_project_or_task(client):
    owner = register(client)
    member = register(
        client,
        name="Grace Hopper",
        email="grace@example.com",
    )

    project = create_project(
        client,
        owner["access_token"],
    ).get_json()["data"]

    # Add Grace to the project.
    added = client.post(
        f"/api/v1/projects/{project['id']}/members",
        headers=auth_headers(owner["access_token"]),
        json={"email": "grace@example.com"},
    )
    assert added.status_code == 201

    # Create a task in the project.
    task = client.post(
        f"/api/v1/projects/{project['id']}/tasks",
        headers=auth_headers(owner["access_token"]),
        json={"title": "Prepare project report"},
    ).get_json()["data"]

    # Remove Grace from the project.
    removed = client.delete(
        f"/api/v1/projects/{project['id']}/members/{member['user']['id']}",
        headers=auth_headers(owner["access_token"]),
    )

    # Grace tries to access the project and task again.
    project_access = client.get(
        f"/api/v1/projects/{project['id']}",
        headers=auth_headers(member["access_token"]),
    )
    task_access = client.get(
        f"/api/v1/tasks/{task['id']}",
        headers=auth_headers(member["access_token"]),
    )

    assert removed.status_code == 204
    assert project_access.status_code == 404
    assert task_access.status_code == 404


def test_non_owner_member_cannot_delete_project(client):
    owner = register(client)
    member = register(
        client,
        name="Grace Hopper",
        email="grace@example.com",
    )

    project = create_project(
        client,
        owner["access_token"],
    ).get_json()["data"]

    # Add Grace as a project member.
    client.post(
        f"/api/v1/projects/{project['id']}/members",
        headers=auth_headers(owner["access_token"]),
        json={"email": "grace@example.com"},
    )

    # Confirm Grace can access the project.
    visible = client.get(
        f"/api/v1/projects/{project['id']}",
        headers=auth_headers(member["access_token"]),
    )

    # Grace attempts to delete the project.
    denied = client.delete(
        f"/api/v1/projects/{project['id']}",
        headers=auth_headers(member["access_token"]),
    )

    # The owner checks that the project still exists.
    still_exists = client.get(
        f"/api/v1/projects/{project['id']}",
        headers=auth_headers(owner["access_token"]),
    )

    assert visible.status_code == 200
    assert denied.status_code == 403
    assert still_exists.status_code == 200


def test_owner_can_delete_project(client):
    owner = register(client)
    project = create_project(
        client,
        owner["access_token"],
    ).get_json()["data"]

    deleted = client.delete(
        f"/api/v1/projects/{project['id']}",
        headers=auth_headers(owner["access_token"]),
    )

    missing = client.get(
        f"/api/v1/projects/{project['id']}",
        headers=auth_headers(owner["access_token"]),
    )

    assert deleted.status_code == 204
    assert missing.status_code == 404

def test_owner_and_member_can_list_project_members(client):
    owner = register(client)
    member = register(
        client,
        name="Grace Hopper",
        email="grace@example.com",
    )

    project = create_project(
        client,
        owner["access_token"],
    ).get_json()["data"]

    added = client.post(
        f"/api/v1/projects/{project['id']}/members",
        headers=auth_headers(owner["access_token"]),
        json={"email": "grace@example.com"},
    )

    assert added.status_code == 201

    # Both the owner and member request the member list.
    owner_response = client.get(
        f"/api/v1/projects/{project['id']}/members",
        headers=auth_headers(owner["access_token"]),
    )

    member_response = client.get(
        f"/api/v1/projects/{project['id']}/members",
        headers=auth_headers(member["access_token"]),
    )

    assert owner_response.status_code == 200
    assert member_response.status_code == 200

    members = owner_response.get_json()["data"]
    emails = {item["email"] for item in members}

    assert emails == {"ada@example.com", "grace@example.com"}


def test_outsider_cannot_list_project_members(client):
    owner = register(client)
    outsider = register(
        client,
        name="Katherine Johnson",
        email="katherine@example.com",
    )

    project = create_project(
        client,
        owner["access_token"],
    ).get_json()["data"]

    response = client.get(
        f"/api/v1/projects/{project['id']}/members",
        headers=auth_headers(outsider["access_token"]),
    )

    assert response.status_code == 404


def add_member(client, token, project_id, email):
    return client.post(
        f"/api/v1/projects/{project_id}/members",
        headers=auth_headers(token),
        json={"email": email},
    )


def test_owner_can_update_project_details(client):
    owner = register(client)
    project = create_project(client, owner["access_token"]).get_json()["data"]

    response = client.patch(
        f"/api/v1/projects/{project['id']}",
        headers=auth_headers(owner["access_token"]),
        json={"name": "  Renamed project  ", "description": "Updated scope."},
    )

    assert response.status_code == 200
    updated = response.get_json()["data"]
    assert updated["name"] == "Renamed project"
    assert updated["description"] == "Updated scope."


def test_project_update_cannot_reassign_owner(client):
    owner = register(client)
    project = create_project(client, owner["access_token"]).get_json()["data"]

    # owner_id is not an accepted field, so it must not be applied as a mass-assignment.
    response = client.patch(
        f"/api/v1/projects/{project['id']}",
        headers=auth_headers(owner["access_token"]),
        json={"owner_id": owner["user"]["id"] + 1},
    )
    still_owned = client.get(
        f"/api/v1/projects/{project['id']}",
        headers=auth_headers(owner["access_token"]),
    )

    assert response.status_code == 400
    assert still_owned.get_json()["data"]["owner_id"] == owner["user"]["id"]


def test_project_list_excludes_projects_without_membership(client):
    owner = register(client)
    outsider = register(client, name="Katherine Johnson", email="katherine@example.com")
    create_project(client, owner["access_token"])

    response = client.get("/api/v1/projects", headers=auth_headers(outsider["access_token"]))

    assert response.status_code == 200
    assert response.get_json()["data"] == []
    assert response.get_json()["meta"] == {"page": 1, "limit": 20, "total": 0}


def test_adding_the_same_member_twice_is_rejected(client):
    owner = register(client)
    register(client, name="Grace Hopper", email="grace@example.com")
    project = create_project(client, owner["access_token"]).get_json()["data"]
    add_member(client, owner["access_token"], project["id"], "grace@example.com")

    response = add_member(client, owner["access_token"], project["id"], "grace@example.com")

    assert response.status_code == 409
    assert response.get_json()["error"]["code"] == "already_a_member"


def test_adding_unregistered_email_is_rejected(client):
    owner = register(client)
    project = create_project(client, owner["access_token"]).get_json()["data"]

    response = add_member(client, owner["access_token"], project["id"], "nobody@example.com")

    assert response.status_code == 404
    assert response.get_json()["error"]["code"] == "user_not_found"


def test_owner_cannot_remove_themselves(client):
    owner = register(client)
    project = create_project(client, owner["access_token"]).get_json()["data"]

    response = client.delete(
        f"/api/v1/projects/{project['id']}/members/{owner['user']['id']}",
        headers=auth_headers(owner["access_token"]),
    )
    members = client.get(
        f"/api/v1/projects/{project['id']}/members",
        headers=auth_headers(owner["access_token"]),
    )

    assert response.status_code == 400
    assert response.get_json()["error"]["code"] == "owner_removal_not_allowed"
    assert [item["role"] for item in members.get_json()["data"]] == ["owner"]


def test_removing_a_non_member_is_not_found(client):
    owner = register(client)
    stranger = register(client, name="Katherine Johnson", email="katherine@example.com")
    project = create_project(client, owner["access_token"]).get_json()["data"]

    response = client.delete(
        f"/api/v1/projects/{project['id']}/members/{stranger['user']['id']}",
        headers=auth_headers(owner["access_token"]),
    )

    assert response.status_code == 404


def test_non_owner_member_cannot_remove_members(client):
    owner = register(client)
    member = register(client, name="Grace Hopper", email="grace@example.com")
    project = create_project(client, owner["access_token"]).get_json()["data"]
    add_member(client, owner["access_token"], project["id"], "grace@example.com")

    response = client.delete(
        f"/api/v1/projects/{project['id']}/members/{member['user']['id']}",
        headers=auth_headers(member["access_token"]),
    )
    members = client.get(
        f"/api/v1/projects/{project['id']}/members",
        headers=auth_headers(owner["access_token"]),
    )

    assert response.status_code == 403
    assert {item["email"] for item in members.get_json()["data"]} == {
        "ada@example.com",
        "grace@example.com",
    }


def test_outsider_cannot_update_or_delete_project(client):
    owner = register(client)
    outsider = register(client, name="Katherine Johnson", email="katherine@example.com")
    project = create_project(client, owner["access_token"]).get_json()["data"]

    patched = client.patch(
        f"/api/v1/projects/{project['id']}",
        headers=auth_headers(outsider["access_token"]),
        json={"name": "Hijacked project"},
    )
    deleted = client.delete(
        f"/api/v1/projects/{project['id']}",
        headers=auth_headers(outsider["access_token"]),
    )
    still_there = client.get(
        f"/api/v1/projects/{project['id']}",
        headers=auth_headers(owner["access_token"]),
    )

    assert patched.status_code == 404
    assert deleted.status_code == 404
    assert still_there.status_code == 200
    assert still_there.get_json()["data"]["name"] == "Launch website"


def test_deleting_project_removes_its_tasks_and_memberships(app, client):
    owner = register(client)
    member = register(client, name="Grace Hopper", email="grace@example.com")
    project = create_project(client, owner["access_token"]).get_json()["data"]
    add_member(client, owner["access_token"], project["id"], "grace@example.com")
    client.post(
        f"/api/v1/projects/{project['id']}/tasks",
        headers=auth_headers(member["access_token"]),
        json={"title": "Task that must disappear"},
    )

    response = client.delete(
        f"/api/v1/projects/{project['id']}",
        headers=auth_headers(owner["access_token"]),
    )

    remaining_tasks = db.session.scalars(
        db.select(Task).where(Task.project_id == project["id"])
    ).all()
    remaining_memberships = db.session.scalars(
        db.select(ProjectMembership).where(ProjectMembership.project_id == project["id"])
    ).all()

    assert response.status_code == 204
    assert remaining_tasks == []
    assert remaining_memberships == []


def test_project_creation_rejects_unknown_fields(client):
    owner = register(client)

    response = client.post(
        "/api/v1/projects",
        headers=auth_headers(owner["access_token"]),
        json={"name": "Launch website", "archived": True},
    )

    assert response.status_code == 400
    assert response.get_json()["error"]["code"] == "validation_error"
    assert "archived" in response.get_json()["error"]["message"]

    listed = client.get("/api/v1/projects", headers=auth_headers(owner["access_token"]))
    assert listed.get_json()["meta"]["total"] == 0


def test_project_creation_cannot_assign_another_owner(client):
    attacker = register(client)
    victim = register(client, name="Grace Hopper", email="grace@example.com")

    response = client.post(
        "/api/v1/projects",
        headers=auth_headers(attacker["access_token"]),
        json={"name": "Launch website", "owner_id": victim["user"]["id"]},
    )
    victims_projects = client.get(
        "/api/v1/projects", headers=auth_headers(victim["access_token"])
    )

    assert response.status_code == 400
    assert response.get_json()["error"]["code"] == "validation_error"
    assert victims_projects.get_json()["data"] == []


def test_adding_member_rejects_unknown_fields(client):
    owner = register(client)
    register(client, name="Grace Hopper", email="grace@example.com")
    project = create_project(client, owner["access_token"]).get_json()["data"]

    response = client.post(
        f"/api/v1/projects/{project['id']}/members",
        headers=auth_headers(owner["access_token"]),
        json={"email": "grace@example.com", "invited_by": owner["user"]["id"]},
    )
    members = client.get(
        f"/api/v1/projects/{project['id']}/members",
        headers=auth_headers(owner["access_token"]),
    )

    assert response.status_code == 400
    assert response.get_json()["error"]["code"] == "validation_error"
    assert [item["role"] for item in members.get_json()["data"]] == ["owner"]


def test_adding_member_cannot_assign_role(client):
    owner = register(client)
    register(client, name="Grace Hopper", email="grace@example.com")
    project = create_project(client, owner["access_token"]).get_json()["data"]

    response = client.post(
        f"/api/v1/projects/{project['id']}/members",
        headers=auth_headers(owner["access_token"]),
        json={"email": "grace@example.com", "role": "owner"},
    )
    members = client.get(
        f"/api/v1/projects/{project['id']}/members",
        headers=auth_headers(owner["access_token"]),
    )

    assert response.status_code == 400
    assert response.get_json()["error"]["code"] == "validation_error"
    assert [(item["email"], item["role"]) for item in members.get_json()["data"]] == [
        ("ada@example.com", "owner")
    ]