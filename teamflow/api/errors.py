"""Centralized JSON error responses."""

from flask import Flask, jsonify
from werkzeug.exceptions import HTTPException


def register_error_handlers(app: Flask) -> None:
    @app.errorhandler(HTTPException)
    def handle_http_exception(exc: HTTPException):
        return jsonify(error={"code": exc.name.lower().replace(" ", "_"), "message": exc.description}), exc.code

    @app.errorhandler(500)
    def handle_internal_error(_exc):
        app.logger.exception("Unhandled application error")
        return jsonify(error={"code": "internal_server_error", "message": "An unexpected error occurred."}), 500
