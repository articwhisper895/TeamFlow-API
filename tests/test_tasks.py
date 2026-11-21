from tests.helpers import auth_headers, register


def setup_project(client):
    owner = register(client)
    member = register(client, name="Grace Hopper", email="grace@example.com")
    project_response = client.post(
        "/api/v1/projects",
        headers=auth_headers(owner["access_token"]),
        json={"name": "Launch website"},
    )
    project = project_response.get_json()["data"]
    client.post(
        f"/api/v1/projects/{project['id']}/members",
        headers=auth_headers(owner["access_token"]),
        json={"email": "grace@example.com"},
    )
    return owner, member, project


def test_member_can_create_task_and_owner_can_list_it(client):
    owner, member, project = setup_project(client)
    created = client.post(
        f"/api/v1/projects/{project['id']}/tasks",
        headers=auth_headers(member["access_token"]),
        json={
            "title": "Prepare landing page copy",
            "priority": "high",
            "assigned_to_id": member["user"]["id"],
            "due_date": "2026-12-01",
        },
    )
    listed = client.get(
        f"/api/v1/projects/{project['id']}/tasks",
        headers=auth_headers(owner["access_token"]),
    )

    assert created.status_code == 201
    assert created.get_json()["data"]["status"] == "todo"
    assert created.get_json()["data"]["assigned_to_id"] == member["user"]["id"]
    assert listed.status_code == 200
    assert listed.get_json()["meta"]["total"] == 1


def test_task_cannot_be_assigned_to_non_member(client):
    owner, _member, project = setup_project(client)
    outsider = register(client, name="Katherine Johnson", email="kat@example.com")
    response = client.post(
        f"/api/v1/projects/{project['id']}/tasks",
        headers=auth_headers(owner["access_token"]),
        json={"title": "Prepare report", "assigned_to_id": outsider["user"]["id"]},
    )

    assert response.status_code == 422
    assert response.get_json()["error"]["code"] == "invalid_assignee"


def test_task_status_and_priority_validation(client):
    owner, _member, project = setup_project(client)
    response = client.post(
        f"/api/v1/projects/{project['id']}/tasks",
        headers=auth_headers(owner["access_token"]),
        json={"title": "Prepare report", "status": "finished-ish"},
    )

    assert response.status_code == 400


def test_member_can_update_task_status(client):
    owner, member, project = setup_project(client)
    created = client.post(
        f"/api/v1/projects/{project['id']}/tasks",
        headers=auth_headers(owner["access_token"]),
        json={"title": "Prepare report"},
    ).get_json()["data"]
    updated = client.patch(
        f"/api/v1/tasks/{created['id']}",
        headers=auth_headers(member["access_token"]),
        json={"status": "in_progress"},
    )

    assert updated.status_code == 200
    assert updated.get_json()["data"]["status"] == "in_progress"


def test_non_member_cannot_read_task(client):
    owner, _member, project = setup_project(client)
    outsider = register(client, name="Katherine Johnson", email="kat@example.com")
    task = client.post(
        f"/api/v1/projects/{project['id']}/tasks",
        headers=auth_headers(owner["access_token"]),
        json={"title": "Private task"},
    ).get_json()["data"]
    response = client.get(
        f"/api/v1/tasks/{task['id']}",
        headers=auth_headers(outsider["access_token"]),
    )

    assert response.status_code == 404


def test_task_creator_or_owner_can_delete_but_other_member_cannot(client):
    owner, member, project = setup_project(client)
    created = client.post(
        f"/api/v1/projects/{project['id']}/tasks",
        headers=auth_headers(owner["access_token"]),
        json={"title": "Owner-created task"},
    ).get_json()["data"]
    denied = client.delete(
        f"/api/v1/tasks/{created['id']}",
        headers=auth_headers(member["access_token"]),
    )
    allowed = client.delete(
        f"/api/v1/tasks/{created['id']}",
        headers=auth_headers(owner["access_token"]),
    )

    assert denied.status_code == 403
    assert allowed.status_code == 204

def test_non_member_cannot_update_task(client):
    owner, _member, project = setup_project(client)

    outsider = register(
        client,
        name="Katherine Johnson",
        email="katherine@example.com",
    )

    created = client.post(
        f"/api/v1/projects/{project['id']}/tasks",
        headers=auth_headers(owner["access_token"]),
        json={"title": "Private project task"},
    ).get_json()["data"]

    # Outsider attempts to change the task title.
    denied = client.patch(
        f"/api/v1/tasks/{created['id']}",
        headers=auth_headers(outsider["access_token"]),
        json={"title": "Unauthorized modification"},
    )

    # Owner checks that the original title remains unchanged.
    unchanged = client.get(
        f"/api/v1/tasks/{created['id']}",
        headers=auth_headers(owner["access_token"]),
    )

    assert denied.status_code == 404
    assert unchanged.status_code == 200
    assert unchanged.get_json()["data"]["title"] == "Private project task"


def test_non_member_cannot_create_or_list_tasks(client):
    _owner, _member, project = setup_project(client)
    outsider = register(client, name="Katherine Johnson", email="katherine@example.com")

    created = client.post(
        f"/api/v1/projects/{project['id']}/tasks",
        headers=auth_headers(outsider["access_token"]),
        json={"title": "Sneaky task"},
    )
    listed = client.get(
        f"/api/v1/projects/{project['id']}/tasks",
        headers=auth_headers(outsider["access_token"]),
    )

    assert created.status_code == 404
    assert listed.status_code == 404


def test_member_who_created_a_task_can_delete_it(client):
    owner, member, project = setup_project(client)
    created = client.post(
        f"/api/v1/projects/{project['id']}/tasks",
        headers=auth_headers(member["access_token"]),
        json={"title": "Member-owned task"},
    ).get_json()["data"]

    deleted = client.delete(
        f"/api/v1/tasks/{created['id']}",
        headers=auth_headers(member["access_token"]),
    )
    missing = client.get(
        f"/api/v1/tasks/{created['id']}",
        headers=auth_headers(owner["access_token"]),
    )

    assert deleted.status_code == 204
    assert missing.status_code == 404


def test_non_member_cannot_delete_task(client):
    owner, _member, project = setup_project(client)
    outsider = register(client, name="Katherine Johnson", email="katherine@example.com")
    created = client.post(
        f"/api/v1/projects/{project['id']}/tasks",
        headers=auth_headers(owner["access_token"]),
        json={"title": "Protected task"},
    ).get_json()["data"]

    denied = client.delete(
        f"/api/v1/tasks/{created['id']}",
        headers=auth_headers(outsider["access_token"]),
    )
    still_there = client.get(
        f"/api/v1/tasks/{created['id']}",
        headers=auth_headers(owner["access_token"]),
    )

    assert denied.status_code == 404
    assert still_there.status_code == 200


def test_reassigning_task_requires_project_membership(client):
    owner, member, project = setup_project(client)
    outsider = register(client, name="Katherine Johnson", email="katherine@example.com")
    created = client.post(
        f"/api/v1/projects/{project['id']}/tasks",
        headers=auth_headers(owner["access_token"]),
        json={"title": "Prepare report", "assigned_to_id": member["user"]["id"]},
    ).get_json()["data"]

    denied = client.patch(
        f"/api/v1/tasks/{created['id']}",
        headers=auth_headers(owner["access_token"]),
        json={"assigned_to_id": outsider["user"]["id"]},
    )
    unchanged = client.get(
        f"/api/v1/tasks/{created['id']}",
        headers=auth_headers(owner["access_token"]),
    )

    assert denied.status_code == 422
    assert denied.get_json()["error"]["code"] == "invalid_assignee"
    assert unchanged.get_json()["data"]["assigned_to_id"] == member["user"]["id"]


def test_task_can_be_unassigned_with_null(client):
    owner, member, project = setup_project(client)
    created = client.post(
        f"/api/v1/projects/{project['id']}/tasks",
        headers=auth_headers(owner["access_token"]),
        json={"title": "Prepare report", "assigned_to_id": member["user"]["id"]},
    ).get_json()["data"]

    response = client.patch(
        f"/api/v1/tasks/{created['id']}",
        headers=auth_headers(owner["access_token"]),
        json={"assigned_to_id": None},
    )

    assert response.status_code == 200
    assert response.get_json()["data"]["assigned_to_id"] is None


def test_task_update_rejects_unsupported_fields_and_empty_body(client):
    owner, _member, project = setup_project(client)
    created = client.post(
        f"/api/v1/projects/{project['id']}/tasks",
        headers=auth_headers(owner["access_token"]),
        json={"title": "Original title"},
    ).get_json()["data"]

    empty = client.patch(
        f"/api/v1/tasks/{created['id']}",
        headers=auth_headers(owner["access_token"]),
        json={},
    )
    unsupported = client.patch(
        f"/api/v1/tasks/{created['id']}",
        headers=auth_headers(owner["access_token"]),
        json={"project_id": project["id"], "title": "Moved task"},
    )
    unchanged = client.get(
        f"/api/v1/tasks/{created['id']}",
        headers=auth_headers(owner["access_token"]),
    )

    assert empty.status_code == 400
    assert unsupported.status_code == 400
    assert unchanged.get_json()["data"]["title"] == "Original title"
    assert unchanged.get_json()["data"]["project_id"] == project["id"]


def test_task_list_filters_by_status_and_assignee(client):
    owner, member, project = setup_project(client)
    in_progress = client.post(
        f"/api/v1/projects/{project['id']}/tasks",
        headers=auth_headers(owner["access_token"]),
        json={
            "title": "In flight",
            "status": "in_progress",
            "assigned_to_id": member["user"]["id"],
        },
    ).get_json()["data"]
    client.post(
        f"/api/v1/projects/{project['id']}/tasks",
        headers=auth_headers(owner["access_token"]),
        json={"title": "Not started"},
    )

    by_status = client.get(
        f"/api/v1/projects/{project['id']}/tasks?status=in_progress",
        headers=auth_headers(owner["access_token"]),
    )
    by_assignee = client.get(
        f"/api/v1/projects/{project['id']}/tasks?assigned_to_me=true",
        headers=auth_headers(member["access_token"]),
    )
    by_completed = client.get(
        f"/api/v1/projects/{project['id']}/tasks?status=done",
        headers=auth_headers(owner["access_token"]),
    )

    assert [task["id"] for task in by_status.get_json()["data"]] == [in_progress["id"]]
    assert [task["id"] for task in by_assignee.get_json()["data"]] == [in_progress["id"]]
    assert by_completed.get_json()["data"] == []
    assert by_completed.get_json()["meta"]["total"] == 0


def test_task_list_rejects_invalid_filters(client):
    owner, _member, project = setup_project(client)

    bad_status = client.get(
        f"/api/v1/projects/{project['id']}/tasks?status=archive",
        headers=auth_headers(owner["access_token"]),
    )
    bad_flag = client.get(
        f"/api/v1/projects/{project['id']}/tasks?assigned_to_me=yes",
        headers=auth_headers(owner["access_token"]),
    )

    assert bad_status.status_code == 400
    assert bad_flag.status_code == 400


def test_create_task_requires_title_and_iso_due_date(client):
    owner, _member, project = setup_project(client)

    no_title = client.post(
        f"/api/v1/projects/{project['id']}/tasks",
        headers=auth_headers(owner["access_token"]),
        json={"description": "A description without a title"},
    )
    bad_date = client.post(
        f"/api/v1/projects/{project['id']}/tasks",
        headers=auth_headers(owner["access_token"]),
        json={"title": "Prepare report", "due_date": "01/12/2026"},
    )
    unsupported = client.post(
        f"/api/v1/projects/{project['id']}/tasks",
        headers=auth_headers(owner["access_token"]),
        json={"title": "Prepare report", "project_id": project["id"]},
    )

    assert no_title.status_code == 400
    assert bad_date.status_code == 400
    assert unsupported.status_code == 400