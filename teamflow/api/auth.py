"""Registration, login, token refresh, logout and current-user endpoints."""

from flask import Blueprint
from flask_jwt_extended import get_jwt, jwt_required
from sqlalchemy.exc import IntegrityError

from teamflow.api.utils import (
    current_user,
    error,
    json_body,
    normalized_email,
    success,
    unsupported_field_message,
)
from teamflow.extensions import db
from teamflow.models import User
from teamflow.sessions import RefreshError, end_session, rotate, start_session

auth_bp = Blueprint("auth", __name__)

# Credential payloads accept exactly these fields; ids and account attributes are server-controlled.
REGISTER_FIELDS = frozenset({"name", "email", "password"})
LOGIN_FIELDS = frozenset({"email", "password"})


@auth_bp.post("/register")
def register():
    body = json_body()
    unsupported = unsupported_field_message(body, REGISTER_FIELDS)
    if unsupported:
        return error(unsupported)
    name = body.get("name")
    email = normalized_email(body.get("email"))
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
        # Flush assigns the user id, so the account and its first refresh session
        # are written in one transaction: a rejected registration creates neither.
        db.session.flush()
        tokens = start_session(user.id, commit=False)
        db.session.commit()
    except IntegrityError:
        db.session.rollback()
        return error("An account with this email already exists.", 409, "email_already_registered")

    return success({"user": user.to_dict(), **tokens}, 201)


@auth_bp.post("/login")
def login():
    body = json_body()
    unsupported = unsupported_field_message(body, LOGIN_FIELDS)
    if unsupported:
        return error(unsupported)
    email = normalized_email(body.get("email"))
    password = body.get("password")
    if email is None or not isinstance(password, str):
        return error("A valid email and password are required.", 401, "invalid_credentials")

    user = db.session.scalar(db.select(User).where(User.email == email))
    if user is None or not user.check_password(password):
        return error("Email or password is incorrect.", 401, "invalid_credentials")

    # Every login opens its own session, so devices can be revoked separately.
    return success({"user": user.to_dict(), **start_session(user.id)})


@auth_bp.get("/me")
@jwt_required()
def me():
    return success({"user": current_user().to_dict()})


@auth_bp.post("/refresh")
@jwt_required(refresh=True)
def refresh():
    user = current_user()
    claims = get_jwt()
    try:
        tokens = rotate(user.id, claims.get("sid"), claims.get("jti"))
    except RefreshError as exc:
        return error(exc.message, exc.status, exc.code)
    return success(tokens)


@auth_bp.post("/logout")
@jwt_required(refresh=True)
def logout():
    user = current_user()
    try:
        result = end_session(user.id, get_jwt().get("sid"))
    except RefreshError as exc:
        return error(exc.message, exc.status, exc.code)
    return success(result)
