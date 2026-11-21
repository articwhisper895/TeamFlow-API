"""Alembic environment for Flask-Migrate.

The current application supplies the engine and metadata, so no URL guessing or
multi-bind fallbacks are needed.
"""

import logging
from logging.config import fileConfig

from alembic import context
from flask import current_app

config = context.config
fileConfig(config.config_file_name)
logger = logging.getLogger("alembic.env")

db = current_app.extensions["migrate"].db

# Escape '%' so ConfigParser interpolation cannot corrupt a password in the URL.
config.set_main_option(
    "sqlalchemy.url", db.engine.url.render_as_string(hide_password=False).replace("%", "%%")
)


def run_migrations_offline() -> None:
    """Run migrations against a URL without creating an engine."""
    context.configure(
        url=config.get_main_option("sqlalchemy.url"),
        target_metadata=db.metadata,
        literal_binds=True,
    )

    with context.begin_transaction():
        context.run_migrations()


def run_migrations_online() -> None:
    """Run migrations on the application's engine."""

    def process_revision_directives(context, revision, directives):
        # Skip writing an empty revision when autogenerate finds no changes.
        if getattr(config.cmd_opts, "autogenerate", False):
            script = directives[0]
            if script.upgrade_ops.is_empty():
                directives[:] = []
                logger.info("No changes in schema detected.")

    conf_args = current_app.extensions["migrate"].configure_args
    if conf_args.get("process_revision_directives") is None:
        conf_args["process_revision_directives"] = process_revision_directives

    with db.engine.connect() as connection:
        context.configure(
            connection=connection, target_metadata=db.metadata, **conf_args
        )

        with context.begin_transaction():
            context.run_migrations()


if context.is_offline_mode():
    run_migrations_offline()
else:
    run_migrations_online()
