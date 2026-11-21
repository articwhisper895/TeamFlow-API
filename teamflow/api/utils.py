"""Small shared helpers for predictable JSON APIs."""

from flask import jsonify, request
from flask_jwt_extended import get_current_user

from teamflow.extensions import db
from teamflow.models import Project, User


def json_body() -> dict:
    """Return a JSON object or an empty object for a missing/invalid body."""
    body = request.get_json(silent=True)
    return body if isinstance(body, dict) else {}


def error(message: str, status: int = 400, code: str = "validation_error"):
    return jsonify(error={"code": code, "message": message}), status


def unsupported_field_message(body: dict, allowed: set[str] | frozenset[str]) -> str | None:
    """Describe JSON fields this endpoint does not accept, or return None if all are known.

    Silently ignoring unknown properties makes a client believe that server-controlled
    fields (owner_id, role, ids) were applied, so write endpoints reject them instead.
    """
    unknown = sorted(set(body) - allowed)
    if not unknown:
        return None
    return f"Unsupported field(s): {', '.join(unknown)}."


def success(data, status: int = 200, **meta):
    payload = {"data": data}
    if meta:
        payload["meta"] = meta
    return jsonify(payload), status


def current_user() -> User:
    """Return the account resolved by the JWT user-lookup loader.

    Flask-JWT-Extended runs that loader during jwt_required() and rejects a token whose
    account no longer exists with 401 account_not_found (see create_app), so routes always
    receive a real user instead of re-querying and handling None themselves.
    """
    return get_current_user()


def project_for_member(project_id: int, user_id: int) -> Project | None:
    project = db.session.get(Project, project_id)
    if project is None or not project.has_member(user_id):
        return None
    return project


def pagination_args(default_limit: int = 20, max_limit: int = 100) -> tuple[int, int] | None:
    """Read and validate page/limit query parameters; return (page, limit)."""
    try:
        page = int(request.args.get("page", 1))
        limit = int(request.args.get("limit", default_limit))
    except ValueError:
        return None
    if page < 1 or limit < 1 or limit > max_limit:
        return None
    return page, limit
