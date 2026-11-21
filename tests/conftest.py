"""Shared pytest fixtures."""

import pytest

from teamflow import create_app
from teamflow.config import TestingConfig
from teamflow.extensions import db
from tests.helpers import auth_headers, register


@pytest.fixture()
def app():
    application = create_app(TestingConfig)
    with application.app_context():
        db.create_all()
        yield application
        db.session.remove()
        db.drop_all()


@pytest.fixture()
def client(app):
    return app.test_client()


@pytest.fixture()
def create_user():
    return register


@pytest.fixture()
def headers():
    return auth_headers
