"""Refresh-session lifecycle: start, rotate and revoke.

Rotation is deliberately optimistic. The old token row is consumed with a
single conditional statement::

    UPDATE refresh_tokens SET consumed_at = ?
    WHERE id = ? AND consumed_at IS NULL

The update reports how many rows it changed. The database evaluates the
condition and the write atomically, so two requests carrying the same refresh
token cannot both win: the loser changes zero rows and is treated as token
reuse. Checking ``consumed_at`` in Python is only used to report a clean
"already used" error before attempting the claim.
"""

from flask import current_app
from flask_jwt_extended import create_access_token, create_refresh_token, decode_token
from sqlalchemy import update
from sqlalchemy.exc import SQLAlchemyError

from teamflow.extensions import db
from teamflow.models import RefreshSession, RefreshToken
from teamflow.models.session import new_session_id, utcnow


class RefreshError(Exception):
    """A session operation that must be answered with the JSON error envelope."""

    def __init__(self, code: str, message: str, status: int = 401) -> None:
        super().__init__(message)
        self.code = code
        self.message = message
        self.status = status


def _session_expiry():
    return utcnow() + current_app.config["JWT_REFRESH_TOKEN_EXPIRES"]


def _token_pair(session: RefreshSession) -> tuple[str, str]:
    """Build an access/refresh pair that both name the session they belong to."""
    claims = {"sid": session.id}
    identity = str(session.user_id)
    return (
        create_access_token(identity=identity, additional_claims=claims),
        create_refresh_token(identity=identity, additional_claims=claims),
    )


def _payload(
    session: RefreshSession,
    access_token: str,
    refresh_token: str,
    *,
    rotated: bool | None = None,
) -> dict:
    payload = {
        "access_token": access_token,
        "refresh_token": refresh_token,
        "token_type": "Bearer",
        "session_id": session.id,
        "refresh_token_expires_at": session.expires_at.isoformat(),
    }
    if rotated is not None:
        payload["refresh_token_rotated"] = rotated
    return payload


def start_session(user_id: int, *, commit: bool = True) -> dict:
    """Open a new session and return its first token pair.

    ``commit=False`` lets registration create the user and the session in one
    transaction, so a failed registration cannot leave an orphan session.
    """
    session = RefreshSession(
        id=new_session_id(),
        user_id=user_id,
        expires_at=_session_expiry(),
    )
    access_token, refresh_token = _token_pair(session)
    db.session.add(session)
    db.session.add(
        RefreshToken(
            session_id=session.id,
            jti=decode_token(refresh_token)["jti"],
            expires_at=session.expires_at,
        )
    )
    if commit:
        db.session.commit()
    return _payload(session, access_token, refresh_token)


def claim_refresh_token(token_id: int) -> bool:
    """Consume a token row exactly once; True means this caller won the race."""
    result = db.session.execute(
        update(RefreshToken)
        .where(RefreshToken.id == token_id, RefreshToken.consumed_at.is_(None))
        .values(consumed_at=utcnow())
    )
    return result.rowcount == 1


def _load_session(session_id: object, user_id: int) -> RefreshSession:
    if not isinstance(session_id, str):
        raise RefreshError("invalid_refresh_token", "This refresh token is not recognised. Sign in again.")
    session = db.session.get(RefreshSession, session_id)
    # The session must belong to the account named by the token: a token signed
    # for another user must never operate on this user's session.
    if session is None or session.user_id != user_id:
        raise RefreshError("invalid_refresh_token", "This refresh token is not recognised. Sign in again.")
    return session


def _revoke_family(session: RefreshSession, reason: str) -> None:
    session.revoke(reason)
    db.session.commit()


def rotate(user_id: int, session_id: object, jti: object) -> dict:
    """Exchange a refresh token for a new pair, or raise :class:`RefreshError`."""
    session = _load_session(session_id, user_id)

    if session.revoked_at is not None:
        raise RefreshError("session_revoked", "This session has been revoked. Sign in again.")
    if session.expires_at <= utcnow():
        raise RefreshError("expired_token", "The refresh session has expired. Sign in again.")
    if not isinstance(jti, str):
        raise RefreshError("invalid_refresh_token", "This refresh token is not recognised. Sign in again.")

    token = db.session.scalar(
        db.select(RefreshToken).where(
            RefreshToken.jti == jti,
            RefreshToken.session_id == session.id,
        )
    )
    if token is None:
        raise RefreshError("invalid_refresh_token", "This refresh token is not recognised. Sign in again.")

    if token.consumed_at is not None:
        # A token that already produced a replacement is being replayed. That
        # means the credential leaked or the client reuses stale tokens, so the
        # whole family is retired. Other sessions of this user stay valid.
        _revoke_family(session, "refresh_token_reuse")
        raise RefreshError(
            "refresh_token_reused",
            "This refresh token was already used, so the session has been revoked. Sign in again.",
        )

    if token.expires_at <= utcnow():
        raise RefreshError("expired_token", "The refresh token has expired. Sign in again.")

    if not claim_refresh_token(token.id):
        # Another request consumed the row between our read and our write.
        _revoke_family(session, "refresh_token_reuse")
        raise RefreshError(
            "refresh_token_reused",
            "This refresh token was already used, so the session has been revoked. Sign in again.",
        )

    access_token, refresh_token = _token_pair(session)
    replacement = RefreshToken(
        session_id=session.id,
        jti=decode_token(refresh_token)["jti"],
        expires_at=session.expires_at,
    )
    db.session.add(replacement)
    session.last_used_at = utcnow()
    try:
        # Flush first so a duplicate identifier or broken reference fails here,
        # inside the transaction, rather than after the response is built.
        db.session.flush()
        token.replaced_by_id = replacement.id
        db.session.commit()
    except SQLAlchemyError:
        # The claim and the insert share one transaction. Rolling back returns
        # the old token to an unconsumed state, so a failed refresh cannot
        # silently burn the caller's only credential.
        db.session.rollback()
        raise RefreshError(
            "refresh_failed",
            "The session could not be refreshed. Try again.",
            500,
        ) from None

    return _payload(session, access_token, refresh_token, rotated=True)


def end_session(user_id: int, session_id: object) -> dict:
    """Revoke the session named by a refresh token; safe to call more than once."""
    session = _load_session(session_id, user_id)
    already_revoked = session.revoked_at is not None
    if not already_revoked:
        session.revoke("logout")
        db.session.commit()
    return {"session_id": session.id, "revoked": True, "already_revoked": already_revoked}
