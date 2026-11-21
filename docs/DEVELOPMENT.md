# Development notes

Conventions for working on TeamFlow. The API contract and setup steps are in
the [README](../README.md).

## Domain rules

These invariants define the product; change them deliberately, not as a side
effect of an unrelated edit.

1. A project creator is its owner and its first member.
2. Only the owner can update or delete a project or manage its membership.
3. Only project members can read a project and its tasks.
4. A task can be assigned only to a current member of its project.
5. A task can be deleted by its creator or the project owner.
6. Removing a member clears that user's assignments in the project.
7. Passwords are stored as hashes; secrets are never committed.
8. Each sign-in is a separate session. A refresh token is single-use, and reuse
   revokes only that session's token family.

## Module responsibilities

- `teamflow/__init__.py` builds the application, registers extensions and
  blueprints, and owns the JWT loaders that turn a token into a user.
- `teamflow/api/*` handle HTTP only: parse, validate, authorize, then delegate.
- `teamflow/sessions.py` owns the refresh-session lifecycle and the strict
  rotation policy. Route handlers do not manipulate token rows directly.
- `teamflow/models/*` define persistence; `teamflow/timeutils.py` holds the
  shared UTC convention.
- `teamflow/api/errors.py` produces the JSON envelope for HTTP and unhandled
  errors, so individual routes never build error responses by hand.

## Engineering constraints

- Keep the application-factory pattern and the blueprint split.
- Use SQLAlchemy 2.x query style (`db.session.scalars(db.select(...))`).
- Validate payloads before writing, and reject unknown fields rather than
  ignoring them.
- Check authentication and resource authorization on every protected route.
  Return `404` instead of `403` when the caller is not a project member, so the
  existence of another user's project is not disclosed.
- Preserve the `data` / `error` response envelope and existing error codes.
- Do not use `db.create_all()` outside test fixtures; schema changes belong in
  reviewed Alembic revisions.
- Keep dependencies minimal and explain any new runtime dependency in the
  README.
- Database failures must never be reported as token reuse, and error responses
  must not include SQL, connection strings or driver detail.

## Adding a model change

1. Update the model.
2. Generate a revision with `flask --app teamflow:create_app db migrate -m "..."`.
3. Read the generated script; verify column types, constraints, indexes,
   cascades and any data migration.
4. Apply it to a fresh database, then to one with existing data, and check the
   downgrade where it is meaningful.
5. Run `flask --app teamflow:create_app db check` to confirm there is no drift.

## Verification before completing a change

```bash
python -m compileall -q teamflow tests
pytest -q -W error::sqlalchemy.exc.SAWarning
ruff check .
```

Behaviour changes need tests. Authorization changes need both the allowed and
the denied case. Session changes should keep the concurrency test meaningful: it
exercises the real endpoint from two threads and asserts a single winner.

PostgreSQL behaviour is verified by the `postgres-integration` CI job. Run it
locally with `TEAMFLOW_POSTGRES_URL` set to a disposable database. Do not report
skipped tests as passing.

## Repository hygiene

- `.env`, virtual environments, SQLite files and backups are gitignored.
- Do not weaken authentication, authorization or password handling to make a
  test pass.
- Keep the README, this document and the Postman collection aligned with the
  code when the contract changes.
