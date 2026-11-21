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


class TestingConfig(Config):
    TESTING = True
    SQLALCHEMY_DATABASE_URI = "sqlite://"
    # Separate, sufficiently long test-only keys silence PyJWT's weak-key warning.
    # These fixed values are strictly for automated tests, never production.
    JWT_SECRET_KEY = "teamflow-test-jwt-secret-key-32-bytes-minimum"
    SECRET_KEY = "teamflow-test-flask-secret-key-32-bytes-minimum"
    JWT_ACCESS_TOKEN_EXPIRES = timedelta(minutes=15)
    JWT_REFRESH_TOKEN_EXPIRES = timedelta(days=1)
