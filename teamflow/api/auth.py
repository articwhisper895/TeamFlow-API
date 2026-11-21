"""Registration, login, token refresh and current-user endpoints."""

from email_validator import EmailNotValidError, validate_email
from flask import Blueprint
from flask_jwt_extended import create_access_token, create_refresh_token, jwt_required
from sqlalchemy.exc import IntegrityError

from teamflow.api.utils import (
    current_user,
    error,
    json_body,
    success,
    unsupported_field_message,
)
from teamflow.extensions import db
from teamflow.models import User

auth_bp = Blueprint("auth", __name__)

# Credential payloads accept exactly these fields; ids and account attributes are server-controlled.
REGISTER_FIELDS = frozenset({"name", "email", "password"})
LOGIN_FIELDS = frozenset({"email", "password"})


def _normalized_email(value: object) -> str | None:
    if not isinstance(value, str):
        return None
    try:
        return validate_email(value.strip(), check_deliverability=False).normalized.lower()
    except EmailNotValidError:
        return None


def _token_pair(user: User) -> dict:
    identity = str(user.id)
    return {
        "access_token": create_access_token(identity=identity),
        "refresh_token": create_refresh_token(identity=identity),
        "token_type": "Bearer",
    }


@auth_bp.post("/register")
def register():
    body = json_body()
    unsupported = unsupported_field_message(body, REGISTER_FIELDS)
    if unsupported:
        return error(unsupported)
    name = body.get("name")
    email = _normalized_email(body.get("email"))
    password = body.get("password")

    if not isinstance(name, str) or not name.strip() or len(name.strip()) > 120:
        return error("Name is required and must be 120 characters or fewer.")
    if email is None:
        return error("A valid email address is required.")
    if not isinstance(password, str) or len(password) < 10:
        return error("Password must contain at least 10 characters.")
    if len(password) > 256:
        return error("Password must be 256 characters or fewer.")
    if db.session.scalar(db.select(User).where(User.email == email)):
        return error("An account with this email already exists.", 409, "email_already_registered")

    user = User(name=name.strip(), email=email)
    user.set_password(password)
    db.session.add(user)
    try:
        db.session.commit()
    except IntegrityError:
        db.session.rollback()
        return error("An account with this email already exists.", 409, "email_already_registered")

    return success({"user": user.to_dict(), **_token_pair(user)}, 201)


@auth_bp.post("/login")
def login():
    body = json_body()
    unsupported = unsupported_field_message(body, LOGIN_FIELDS)
    if unsupported:
        return error(unsupported)
    email = _normalized_email(body.get("email"))
    password = body.get("password")
    if email is None or not isinstance(password, str):
        return error("A valid email and password are required.", 401, "invalid_credentials")

    user = db.session.scalar(db.select(User).where(User.email == email))
    if user is None or not user.check_password(password):
        return error("Email or password is incorrect.", 401, "invalid_credentials")

    return success({"user": user.to_dict(), **_token_pair(user)})


@auth_bp.get("/me")
@jwt_required()
def me():
    return success({"user": current_user().to_dict()})


@auth_bp.post("/refresh")
@jwt_required(refresh=True)
def refresh():
    user = current_user()
    return success({"access_token": create_access_token(identity=str(user.id)), "token_type": "Bearer"})
