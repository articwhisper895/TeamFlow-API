"""TeamFlow API application package."""

import os

from flask import Flask, jsonify
from sqlalchemy import event

from teamflow.config import Config
from teamflow.extensions import db, jwt, migrate


def _enable_sqlite_foreign_keys(app: Flask) -> None:
    """Enable SQLite FK enforcement on this app's engine."""
    if not app.config["SQLALCHEMY_DATABASE_URI"].startswith("sqlite"):
        return

    # Resolve the engine inside this app's context and attach only to that engine.
    with app.app_context():
        engine = db.engine

    @event.listens_for(engine, "connect")
    def set_sqlite_pragma(connection, _connection_record):
        cursor = connection.cursor()
        cursor.execute("PRAGMA foreign_keys=ON")
        cursor.close()


def create_app(config_object: type[Config] | dict | None = None) -> Flask:
    """Create and configure a TeamFlow application instance."""
    app = Flask(__name__)
    app.config.from_object(Config)

    if isinstance(config_object, dict):
        app.config.update(config_object)
    elif config_object is not None:
        app.config.from_object(config_object)

    if os.getenv("APP_ENV", "development").lower() == "production":
        if app.config["SECRET_KEY"] == "local-development-secret-change-me":
            raise RuntimeError("Set a unique SECRET_KEY before running in production.")
        if not os.getenv("JWT_SECRET_KEY"):
            raise RuntimeError("Set a separate JWT_SECRET_KEY before running in production.")
        app.config["SESSION_COOKIE_SECURE"] = True
        app.config["SESSION_COOKIE_HTTPONLY"] = True
        app.config["SESSION_COOKIE_SAMESITE"] = "Lax"

    db.init_app(app)
    migrate.init_app(app, db, compare_type=True)
    jwt.init_app(app)
    _enable_sqlite_foreign_keys(app)

    # Import models before migrations inspect db.metadata.
    from teamflow import models
    from teamflow.api.auth import auth_bp
    from teamflow.api.errors import register_error_handlers
    from teamflow.api.projects import projects_bp
    from teamflow.api.tasks import tasks_bp

    app.register_blueprint(auth_bp, url_prefix="/api/v1/auth")
    app.register_blueprint(projects_bp, url_prefix="/api/v1/projects")
    app.register_blueprint(tasks_bp, url_prefix="/api/v1")
    register_error_handlers(app)

    @app.get("/api/health")
    def health():
        return jsonify({"status": "ok", "service": "teamflow-api"}), 200

    @jwt.user_lookup_loader
    def load_user(_jwt_header, jwt_data):
        # This is the single place a token becomes a user. Returning None tells
        # Flask-JWT-Extended the account is gone, and the error loader below turns
        # that into 401 account_not_found before any route body runs.
        try:
            user_id = int(jwt_data["sub"])
        except (KeyError, TypeError, ValueError):
            return None
        return db.session.get(models.User, user_id)

    @jwt.user_lookup_error_loader
    def user_not_found(_jwt_header, _jwt_payload):
        return jsonify(error={"code": "account_not_found", "message": "The account associated with this token no longer exists."}), 401

    @jwt.unauthorized_loader
    def missing_token(_reason):
        return jsonify(error={"code": "unauthorized", "message": "Authentication is required."}), 401

    @jwt.invalid_token_loader
    def invalid_token(_reason):
        return jsonify(error={"code": "invalid_token", "message": "The supplied token is invalid."}), 401

    @jwt.expired_token_loader
    def expired_token(_jwt_header, _jwt_payload):
        return jsonify(error={"code": "expired_token", "message": "The supplied token has expired."}), 401

    return app
