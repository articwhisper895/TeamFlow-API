"""Task creation, assignment, filtering and updates."""

from datetime import date

from flask import Blueprint, request
from flask_jwt_extended import jwt_required
from sqlalchemy import func

from teamflow.api.utils import (
    current_user,
    error,
    json_body,
    pagination_args,
    project_for_member,
    success,
    unsupported_field_message,
)
from teamflow.extensions import db
from teamflow.models import Project, ProjectMembership, Task

tasks_bp = Blueprint("tasks", __name__)
VALID_STATUSES = {"todo", "in_progress", "done"}
VALID_PRIORITIES = {"low", "medium", "high"}


def _validated_due_date(value: object):
    if value in (None, ""):
        return None, None
    if not isinstance(value, str):
        return None, "due_date must use YYYY-MM-DD format."
    try:
        return date.fromisoformat(value), None
    except ValueError:
        return None, "due_date must use YYYY-MM-DD format."


def _validate_task_fields(body: dict, creating: bool = False):
    allowed = {"title", "description", "status", "priority", "due_date", "assigned_to_id"}
    unsupported = unsupported_field_message(body, allowed)
    if unsupported:
        return None, unsupported
    result = {}
    if creating or "title" in body:
        title = body.get("title")
        if not isinstance(title, str) or not title.strip() or len(title.strip()) > 180:
            return None, "Task title is required and must be 180 characters or fewer."
        result["title"] = title.strip()
    if "description" in body:
        description = body["description"]
        if not isinstance(description, str) or len(description) > 5000:
            return None, "Description must be text and 5,000 characters or fewer."
        result["description"] = description.strip()
    if "status" in body:
        if not isinstance(body["status"], str) or body["status"] not in VALID_STATUSES:
            return None, "status must be todo, in_progress or done."
        result["status"] = body["status"]
    if "priority" in body:
        if not isinstance(body["priority"], str) or body["priority"] not in VALID_PRIORITIES:
            return None, "priority must be low, medium or high."
        result["priority"] = body["priority"]
    if "due_date" in body:
        due_date, message = _validated_due_date(body["due_date"])
        if message:
            return None, message
        result["due_date"] = due_date
    if "assigned_to_id" in body:
        assigned_to_id = body["assigned_to_id"]
        if assigned_to_id is not None and (not isinstance(assigned_to_id, int) or isinstance(assigned_to_id, bool)):
            return None, "assigned_to_id must be a user ID or null."
        result["assigned_to_id"] = assigned_to_id
    return result, None


def _is_project_member(project_id: int, user_id: int) -> bool:
    """Return whether a user belongs to a project."""
    membership_id = db.session.scalar(
        db.select(ProjectMembership.id).where(
            ProjectMembership.project_id == project_id,
            ProjectMembership.user_id == user_id,
        )
    )
    return membership_id is not None


def _assign_if_member(task: Task, project: Project, values: dict) -> str | None:
    if "assigned_to_id" not in values:
        return None
    assignee_id = values.pop("assigned_to_id")
    if assignee_id is None:
        task.assigned_to_id = None
        return None
    if not _is_project_member(project.id, assignee_id):
        return "The assignee must already be a member of this project."
    task.assigned_to_id = assignee_id
    return None


@tasks_bp.get("/projects/<int:project_id>/tasks")
@jwt_required()
def list_tasks(project_id: int):
    user = current_user()
    project = project_for_member(project_id, user.id)
    if project is None:
        return error("Project not found.", 404, "not_found")
    paging = pagination_args()
    if paging is None:
        return error("page must be positive and limit must be between 1 and 100.")
    page, limit = paging
    query = db.select(Task).where(Task.project_id == project.id)
    status = request.args.get("status")
    if status:
        if status not in VALID_STATUSES:
            return error("status must be todo, in_progress or done.")
        query = query.where(Task.status == status)
    assigned_to_me = request.args.get("assigned_to_me", "false")
    if assigned_to_me not in {"true", "false"}:
        return error("assigned_to_me must be true or false.")
    if assigned_to_me == "true":
        query = query.where(Task.assigned_to_id == user.id)
    total = db.session.scalar(db.select(func.count()).select_from(query.subquery())) or 0
    query = query.order_by(Task.created_at.desc(), Task.id.desc())
    tasks = db.session.scalars(query.offset((page - 1) * limit).limit(limit)).all()
    return success([task.to_dict() for task in tasks], page=page, limit=limit, total=total)


@tasks_bp.post("/projects/<int:project_id>/tasks")
@jwt_required()
def create_task(project_id: int):
    user = current_user()
    project = project_for_member(project_id, user.id)
    if project is None:
        return error("Project not found.", 404, "not_found")
    body = json_body()
    values, message = _validate_task_fields(body, creating=True)
    if message:
        return error(message)
    # Validate the assignee before attaching a new Task to ORM relationships.
    # This avoids an autoflush warning when the membership query runs.
    assignee_id = values.pop("assigned_to_id", None)
    if assignee_id is not None and not _is_project_member(project.id, assignee_id):
        return error(
            "The assignee must already be a member of this project.",
            422,
            "invalid_assignee",
        )

    task = Task(project=project, creator=user)
    task.assigned_to_id = assignee_id
    for key, value in values.items():
        setattr(task, key, value)
    db.session.add(task)
    db.session.commit()
    return success(task.to_dict(), 201)


@tasks_bp.get("/tasks/<int:task_id>")
@jwt_required()
def get_task(task_id: int):
    user = current_user()
    task = db.session.get(Task, task_id)
    if task is None or not task.project.has_member(user.id):
        return error("Task not found.", 404, "not_found")
    return success(task.to_dict())


@tasks_bp.patch("/tasks/<int:task_id>")
@jwt_required()
def update_task(task_id: int):
    user = current_user()
    task = db.session.get(Task, task_id)
    if task is None or not task.project.has_member(user.id):
        return error("Task not found.", 404, "not_found")
    body = json_body()
    values, message = _validate_task_fields(body)
    if message:
        return error(message)
    if not values:
        return error("Provide at least one supported task field to update.")
    assignment_error = _assign_if_member(task, task.project, values)
    if assignment_error:
        return error(assignment_error, 422, "invalid_assignee")
    for key, value in values.items():
        setattr(task, key, value)
    db.session.commit()
    return success(task.to_dict())


@tasks_bp.delete("/tasks/<int:task_id>")
@jwt_required()
def delete_task(task_id: int):
    user = current_user()
    task = db.session.get(Task, task_id)
    if task is None or not task.project.has_member(user.id):
        return error("Task not found.", 404, "not_found")
    if task.created_by_id != user.id and task.project.owner_id != user.id:
        return error("Only the task creator or project owner can delete this task.", 403, "forbidden")
    db.session.delete(task)
    db.session.commit()
    return "", 204
