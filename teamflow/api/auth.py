"""Registration, login, token refresh, logout and current-user endpoints."""

from flask import Blueprint
from flask_jwt_extended import get_jwt, jwt_required
from sqlalchemy.exc import IntegrityError
from werkzeug.security import check_password_hash, generate_password_hash

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
from teamflow.throttling import (
    account_key,
    clear_login_failures,
    client_key,
    login_wait,
    record_login_failure,
    record_registration,
)

auth_bp = Blueprint("auth", __name__)

# Credential payloads accept exactly these fields; ids and account attributes are server-controlled.
REGISTER_FIELDS = frozenset({"name", "email", "password"})
LOGIN_FIELDS = frozenset({"email", "password"})

INVALID_CREDENTIALS = "Email or password is incorrect."

# An unknown account still pays for one hash verification, so a missing account
# does not answer noticeably faster than a wrong password. The value is never a
# real password and is only ever compared against, never matched.
_UNKNOWN_ACCOUNT_HASH = generate_password_hash("teamflow-timing-placeholder")


def _reject_unknown_account(password: str) -> None:
    check_password_hash(_UNKNOWN_ACCOUNT_HASH, password)


def _throttled(retry_after: int):
    """Return the one generic response for any throttled credential request."""
    response, status = error("Too many attempts. Try again later.", 429, "too_many_attempts")
    response.headers["Retry-After"] = str(retry_after)
    return response, status


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

    ip = client_key()
    wait = record_registration(ip)
    if wait is not None:
        return _throttled(wait)

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
    ip = client_key()
    account = account_key(email) if email is not None else None

    wait = login_wait(ip, account)
    if wait is not None:
        return _throttled(wait)

    if email is None or not isinstance(password, str):
        record_login_failure(ip, account)
        return error(INVALID_CREDENTIALS, 401, "invalid_credentials")

    user = db.session.scalar(db.select(User).where(User.email == email))
    if user is None:
        _reject_unknown_account(password)
        record_login_failure(ip, account)
        return error(INVALID_CREDENTIALS, 401, "invalid_credentials")
    if not user.check_password(password):
        record_login_failure(ip, account)
        return error(INVALID_CREDENTIALS, 401, "invalid_credentials")

    clear_login_failures(account)
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
