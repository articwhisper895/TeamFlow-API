"""Environment-driven application configuration."""

import os
from datetime import timedelta


class Config:
    SECRET_KEY = os.getenv("SECRET_KEY", "local-development-secret-change-me")
    JWT_SECRET_KEY = os.getenv("JWT_SECRET_KEY") or SECRET_KEY
    SQLALCHEMY_DATABASE_URI = os.getenv("DATABASE_URL", "sqlite:///teamflow.db")
    SQLALCHEMY_TRACK_MODIFICATIONS = False
    JWT_ACCESS_TOKEN_EXPIRES = timedelta(minutes=int(os.getenv("ACCESS_TOKEN_MINUTES", "15")))
    JWT_REFRESH_TOKEN_EXPIRES = timedelta(days=int(os.getenv("REFRESH_TOKEN_DAYS", "14")))

    # Failed-sign-in and registration limits. The window bounds how far back
    # failures are counted; reaching a limit starts a backoff that doubles per
    # further failure up to LOGIN_BACKOFF_MAX_SECONDS.
    RATE_LIMIT_ENABLED = os.getenv("RATE_LIMIT_ENABLED", "true").lower() != "false"
    LOGIN_IP_MAX_FAILURES = int(os.getenv("LOGIN_IP_MAX_FAILURES", "10"))
    LOGIN_ACCOUNT_MAX_FAILURES = int(os.getenv("LOGIN_ACCOUNT_MAX_FAILURES", "5"))
    LOGIN_FAILURE_WINDOW_SECONDS = int(os.getenv("LOGIN_FAILURE_WINDOW_SECONDS", "900"))
    LOGIN_BACKOFF_BASE_SECONDS = int(os.getenv("LOGIN_BACKOFF_BASE_SECONDS", "30"))
    LOGIN_BACKOFF_MAX_SECONDS = int(os.getenv("LOGIN_BACKOFF_MAX_SECONDS", "900"))
    REGISTER_IP_MAX_ATTEMPTS = int(os.getenv("REGISTER_IP_MAX_ATTEMPTS", "20"))
    REGISTER_WINDOW_SECONDS = int(os.getenv("REGISTER_WINDOW_SECONDS", "3600"))


class TestingConfig(Config):
    TESTING = True
    SQLALCHEMY_DATABASE_URI = "sqlite://"
    # Separate, sufficiently long test-only keys silence PyJWT's weak-key warning.
    # These fixed values are strictly for automated tests, never production.
    JWT_SECRET_KEY = "teamflow-test-jwt-secret-key-32-bytes-minimum"
    SECRET_KEY = "teamflow-test-flask-secret-key-32-bytes-minimum"
    JWT_ACCESS_TOKEN_EXPIRES = timedelta(minutes=15)
    JWT_REFRESH_TOKEN_EXPIRES = timedelta(days=1)
    # Endpoint tests sign in many times from one address, so the limits are off
    # here and exercised explicitly by tests/test_rate_limiting.py.
    RATE_LIMIT_ENABLED = False
