"""Project CRUD and team membership endpoints."""

from flask import Blueprint
from flask_jwt_extended import jwt_required
from sqlalchemy import func, update
from sqlalchemy.exc import IntegrityError

from teamflow.api.utils import (
    current_user,
    error,
    json_body,
    normalized_email,
    pagination_args,
    project_for_member,
    success,
    unsupported_field_message,
)
from teamflow.extensions import db
from teamflow.models import Project, ProjectMembership, Task, User

projects_bp = Blueprint("projects", __name__)

# Clients choose a project's name and description; ownership and membership are server-controlled.
PROJECT_FIELDS = frozenset({"name", "description"})
MEMBERSHIP_FIELDS = frozenset({"email"})


@projects_bp.get("")
@jwt_required()
def list_projects():
    user = current_user()
    paging = pagination_args()
    if paging is None:
        return error("page must be positive and limit must be between 1 and 100.")
    page, limit = paging
    query = (
        db.select(Project)
        .join(ProjectMembership, ProjectMembership.project_id == Project.id)
        .where(ProjectMembership.user_id == user.id)
        .order_by(Project.updated_at.desc(), Project.id.desc())
    )
    total = db.session.scalar(db.select(func.count()).select_from(query.subquery())) or 0
    projects = db.session.scalars(query.offset((page - 1) * limit).limit(limit)).all()
    return success([project.to_dict() for project in projects], page=page, limit=limit, total=total)


@projects_bp.post("")
@jwt_required()
def create_project():
    user = current_user()
    body = json_body()
    unsupported = unsupported_field_message(body, PROJECT_FIELDS)
    if unsupported:
        return error(unsupported)
    name = body.get("name")
    description = body.get("description", "")
    if not isinstance(name, str) or not name.strip() or len(name.strip()) > 160:
        return error("Project name is required and must be 160 characters or fewer.")
    if not isinstance(description, str) or len(description) > 5000:
        return error("Description must be text and must be 5,000 characters or fewer.")

    project = Project(name=name.strip(), description=description.strip(), owner_id=user.id)
    project.memberships.append(ProjectMembership(user=user, role="owner"))
    db.session.add(project)
    db.session.commit()
    return success(project.to_dict(include_members=True), 201)


@projects_bp.get("/<int:project_id>")
@jwt_required()
def get_project(project_id: int):
    user = current_user()
    project = project_for_member(project_id, user.id)
    if project is None:
        return error("Project not found.", 404, "not_found")
    return success(project.to_dict(include_members=True))


@projects_bp.patch("/<int:project_id>")
@jwt_required()
def update_project(project_id: int):
    user = current_user()
    project = project_for_member(project_id, user.id)
    if project is None:
        return error("Project not found.", 404, "not_found")
    if project.owner_id != user.id:
        return error("Only the project owner can update project details.", 403, "forbidden")

    body = json_body()
    if not body:
        return error("Provide name and/or description only.")
    unsupported = unsupported_field_message(body, PROJECT_FIELDS)
    if unsupported:
        return error(unsupported)
    if "name" in body:
        name = body["name"]
        if not isinstance(name, str) or not name.strip() or len(name.strip()) > 160:
            return error("Project name must be non-empty and 160 characters or fewer.")
        project.name = name.strip()
    if "description" in body:
        description = body["description"]
        if not isinstance(description, str) or len(description) > 5000:
            return error("Description must be text and 5,000 characters or fewer.")
        project.description = description.strip()

    db.session.commit()
    return success(project.to_dict(include_members=True))


@projects_bp.delete("/<int:project_id>")
@jwt_required()
def delete_project(project_id: int):
    user = current_user()
    project = project_for_member(project_id, user.id)
    if project is None:
        return error("Project not found.", 404, "not_found")
    if project.owner_id != user.id:
        return error("Only the project owner can delete a project.", 403, "forbidden")
    db.session.delete(project)
    db.session.commit()
    return "", 204


@projects_bp.get("/<int:project_id>/members")
@jwt_required()
def list_members(project_id: int):
    user = current_user()
    project = project_for_member(project_id, user.id)
    if project is None:
        return error("Project not found.", 404, "not_found")
    return success([membership.to_dict() for membership in project.memberships])


@projects_bp.post("/<int:project_id>/members")
@jwt_required()
def add_member(project_id: int):
    user = current_user()
    project = project_for_member(project_id, user.id)
    if project is None:
        return error("Project not found.", 404, "not_found")
    if project.owner_id != user.id:
        return error("Only the project owner can add members.", 403, "forbidden")

    body = json_body()
    unsupported = unsupported_field_message(body, MEMBERSHIP_FIELDS)
    if unsupported:
        return error(unsupported)
    email = normalized_email(body.get("email"))
    if email is None:
        return error("A valid member email address is required.")
    member = db.session.scalar(db.select(User).where(User.email == email))
    if member is None:
        return error("No registered user was found with that email.", 404, "user_not_found")
    if project.has_member(member.id):
        return error("This user is already a project member.", 409, "already_a_member")

    membership = ProjectMembership(project=project, user=member, role="member")
    db.session.add(membership)
    try:
        db.session.commit()
    except IntegrityError:
        db.session.rollback()
        return error("This user is already a project member.", 409, "already_a_member")
    return success(membership.to_dict(), 201)


@projects_bp.delete("/<int:project_id>/members/<int:member_id>")
@jwt_required()
def remove_member(project_id: int, member_id: int):
    user = current_user()
    project = project_for_member(project_id, user.id)
    if project is None:
        return error("Project not found.", 404, "not_found")
    if project.owner_id != user.id:
        return error("Only the project owner can remove members.", 403, "forbidden")
    if member_id == project.owner_id:
        return error("The project owner cannot be removed.", 400, "owner_removal_not_allowed")
    membership = db.session.scalar(
        db.select(ProjectMembership).where(
            ProjectMembership.project_id == project.id,
            ProjectMembership.user_id == member_id,
        )
    )
    if membership is None:
        return error("Project member not found.", 404, "not_found")
    # A removed member must not remain assigned to tasks in a project they can no longer access.
    db.session.execute(
        update(Task)
        .where(Task.project_id == project.id, Task.assigned_to_id == member_id)
        .values(assigned_to_id=None)
    )
    db.session.delete(membership)
    db.session.commit()
    return "", 204
