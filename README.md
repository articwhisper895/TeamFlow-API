# TeamFlow API

TeamFlow is a Flask REST API for coordinating work in small teams. It manages
user accounts, projects, project membership and tasks, and uses JWT
authentication with single-use refresh tokens.

Written with Python 3.12, Flask 3, SQLAlchemy 2, Flask-SQLAlchemy,
Flask-Migrate/Alembic and pytest.

## Capabilities

- Account registration, hashed passwords and login.
- Short-lived JWT access tokens and rotating, single-use refresh tokens.
- Refresh-token replay detection: reuse revokes that session's token family.
- One session per sign-in, so each device can be signed out independently.
- Project CRUD with an explicit membership table and owner/member roles.
- Project membership management by the project owner.
- Task CRUD with assignment restricted to project members.
- Pagination and task filtering by status or current assignee.
- Membership checks on every project and task endpoint. Non-members receive
  `404` so the API does not confirm that another user's resource exists.
- Strict request validation: unknown JSON fields are rejected, and
  server-controlled fields cannot be supplied by clients.
- Versioned schema changes with Alembic, and automated tests for both the
  SQLite test database and PostgreSQL.

## Project structure

```text
teamflow/
├── __init__.py          # create_app(), extension setup, JWT hooks, health route
├── config.py            # environment-driven configuration
├── extensions.py        # shared SQLAlchemy, JWT and migration objects
├── sessions.py          # refresh-session lifecycle: start, rotate, revoke
├── timeutils.py         # UTC timestamp helpers shared by models and sessions
├── api/
│   ├── auth.py          # register, login, refresh, logout, current user
│   ├── projects.py      # project CRUD and membership management
│   ├── tasks.py         # task CRUD, assignment and filtering
│   ├── errors.py        # JSON handlers for HTTP and unhandled errors
│   └── utils.py         # request, response, validation and access helpers
└── models/
    ├── user.py
    ├── session.py       # refresh_sessions and refresh_tokens
    ├── project.py
    └── task.py

tests/                   # endpoint, authorization, session and concurrency tests
migrations/              # Alembic migration history
docs/                    # maintained technical documentation
postman/                 # manual API collection
```

The application uses the Flask application-factory and blueprint patterns, so
configuration, extensions and route groups stay separate.
`docs/DEVELOPMENT.md` documents the domain rules, module responsibilities and
maintenance conventions.

### Data model

```text
User 1 ─── * ProjectMembership * ─── 1 Project 1 ─── * Task
 │                                         │              │
 └──────────────── assigned tasks ─────────┴──────────────┘
```

- A project has one owner, who is also recorded as a member.
- Membership is an explicit table because the relationship carries a role
  (`owner` or `member`) and a join timestamp.
- A task belongs to one project and records its creator. It may be assigned to
  a project member.

## Requirements

- Python 3.12 or newer.
- SQLite for local development, which needs no server.
- Optional: PostgreSQL 16, used by the opt-in integration suite and CI.

## Installation

PowerShell:

```powershell
py -3.12 -m venv .venv
.\.venv\Scripts\Activate.ps1
python -m pip install --upgrade pip
pip install -r requirements-dev.txt
Copy-Item .env.example .env
```

macOS / Linux:

```bash
python3 -m venv .venv
source .venv/bin/activate
python -m pip install --upgrade pip
pip install -r requirements-dev.txt
cp .env.example .env
```

Generate two different secrets and put them in `.env` as `SECRET_KEY` and
`JWT_SECRET_KEY`:

```bash
python -c "import secrets; print(secrets.token_urlsafe(48))"
```

Then create the schema from the committed migrations and start the server:

```bash
flask --app teamflow:create_app db upgrade
flask --app teamflow:create_app run --debug
```

The API listens on `http://127.0.0.1:5000`. The migrations are already in the
repository; a fresh clone only needs `db upgrade`. Running `flask db init` or
`flask db migrate` on a clone creates conflicting history and is not part of
setup.

## Configuration

| Variable | Default | Purpose |
|---|---|---|
| `APP_ENV` | `development` | Set to `production` to enable the production checks below |
| `SECRET_KEY` | development placeholder | Flask secret key |
| `JWT_SECRET_KEY` | falls back to `SECRET_KEY` | Signs access and refresh tokens |
| `DATABASE_URL` | `sqlite:///teamflow.db` | SQLAlchemy connection URL |
| `ACCESS_TOKEN_MINUTES` | `15` | Access-token lifetime |
| `REFRESH_TOKEN_DAYS` | `14` | Absolute session lifetime |

`.env` is read by Flask and is excluded from version control. With
`APP_ENV=production` the application refuses to start while `SECRET_KEY` still
holds the development placeholder or when `JWT_SECRET_KEY` is unset, and it
sets secure cookie flags. Development defaults are deliberately convenient and
are not suitable for a deployed instance.

## Database migrations

```bash
flask --app teamflow:create_app db upgrade      # apply pending revisions
flask --app teamflow:create_app db current      # show the applied revision
flask --app teamflow:create_app db check        # detect model/schema drift
```

After changing a model, generate and review a revision:

```bash
flask --app teamflow:create_app db migrate -m "Describe the change"
```

Read the generated script before committing it. Autogeneration does not detect
every constraint or data change, and it is not a substitute for review. Test
`upgrade` on a fresh database and `downgrade` when the revision supports it.

## Running the tests

```bash
pytest -q
pytest -q -W error::sqlalchemy.exc.SAWarning   # treat ORM warnings as failures
ruff check .
```

The default suite uses an in-memory SQLite database and needs no server. It
covers the happy paths and the failure cases that matter for access control:
invalid credentials, weak passwords, duplicate accounts, missing tokens,
non-member access, non-owner changes, and assignment to someone outside the
project.

`tests/test_refresh_concurrency.py` runs two real threads against a
file-backed SQLite database and the real refresh endpoint. It asserts that one
request wins, the other receives `401 refresh_token_reused`, the token family
is revoked, and the winner's replacement token is therefore unusable.

### PostgreSQL integration tests

A separate suite runs the real migrations and the session flow against
PostgreSQL:

```bash
export TEAMFLOW_POSTGRES_URL=postgresql+psycopg://teamflow:teamflow@localhost:5432/teamflow_test
pytest tests/test_postgres_integration.py -q
```

Use a disposable database: the fixture upgrades to head and downgrades to base
afterwards. The suite is skipped when the variable is unset, so the default run
is unaffected. CI executes it against a PostgreSQL 16 service; that job is the
verified environment for PostgreSQL behaviour.

## API

All routes are JSON. Successful responses use a `data` envelope, and list
responses add `meta` with `page`, `limit` and `total`. Errors use an `error`
object with a stable `code` and a human-readable `message`.

| Method | Endpoint | Access |
|---|---|---|
| GET | `/api/health` | Public |
| POST | `/api/v1/auth/register` | Public |
| POST | `/api/v1/auth/login` | Public |
| GET | `/api/v1/auth/me` | Access token |
| POST | `/api/v1/auth/refresh` | Refresh token |
| POST | `/api/v1/auth/logout` | Refresh token |
| GET | `/api/v1/projects` | Authenticated |
| POST | `/api/v1/projects` | Authenticated |
| GET | `/api/v1/projects/{id}` | Project member |
| PATCH | `/api/v1/projects/{id}` | Project owner |
| DELETE | `/api/v1/projects/{id}` | Project owner |
| GET | `/api/v1/projects/{id}/members` | Project member |
| POST | `/api/v1/projects/{id}/members` | Project owner |
| DELETE | `/api/v1/projects/{id}/members/{user_id}` | Project owner |
| GET | `/api/v1/projects/{id}/tasks` | Project member |
| POST | `/api/v1/projects/{id}/tasks` | Project member |
| GET | `/api/v1/tasks/{id}` | Project member |
| PATCH | `/api/v1/tasks/{id}` | Project member |
| DELETE | `/api/v1/tasks/{id}` | Task creator or project owner |

Authenticated requests send `Authorization: Bearer <access_token>`.

Register:

```json
{ "name": "Ada Lovelace", "email": "ada@example.com", "password": "a-long-unique-password" }
```

Create project:

```json
{ "name": "Website launch", "description": "Coordinate the new company website." }
```

Add member (the user must already have an account; the server assigns the
`member` role):

```json
{ "email": "grace@example.com" }
```

Create task:

```json
{
  "title": "Prepare landing page copy",
  "description": "Draft and review the first version.",
  "priority": "high",
  "status": "todo",
  "assigned_to_id": 2,
  "due_date": "2026-12-01"
}
```

Status is one of `todo`, `in_progress`, `done`; priority is one of `low`,
`medium`, `high`; `due_date` uses `YYYY-MM-DD`; `assigned_to_id` may be omitted,
set to a project member, or `null` to unassign.

List endpoints accept `page` (default 1) and `limit` (default 20, maximum 100).
Task lists also accept `status` and `assigned_to_me=true`.

### Timestamps

Every timestamp in a response is ISO 8601 UTC with an explicit `Z`, for
example `2026-12-05T12:00:00Z`.

## Authentication and sessions

Each registration and each login creates a session. A session is also a token
family: every refresh consumes the current refresh token and returns a
replacement in the same family.

- Store both tokens from every response and discard the previous refresh token;
  the response is the only place the replacement exists.
- One refresh per session at a time. Two overlapping refreshes, or a retry after
  a lost response, is treated as reuse: the API returns
  `401 refresh_token_reused` and revokes that session's family. The user's other
  sessions stay valid.
- `POST /api/v1/auth/logout` takes the refresh token, revokes that session and
  is safe to call again. Later refreshes return `401 session_revoked`.

Refresh-path error codes: `invalid_token`, `invalid_refresh_token`,
`session_revoked`, `expired_token`, `refresh_token_reused`, plus `500`
`refresh_failed`, `revocation_failed` and `logout_failed`. Database failures are
never reported as token reuse, and error messages never include SQL, connection
strings or driver details.

Stored data: the database keeps the session id, owner, timestamps and
revocation reason, and per token only the JWT identifier (`jti`), issue and
expiry timestamps, the single `consumed_at` value and a link to its replacement.
Refresh tokens themselves are never stored. Rotation consumes a token with one
conditional `UPDATE ... WHERE consumed_at IS NULL`, so two concurrent requests
carrying the same token cannot both succeed.

## Postman

Import `postman/TeamFlow.postman_collection.json` and start the API. Run the
requests in order on a fresh database: **Register**, **Create project**,
**Register teammate**, **Add project member**, **List project members**,
**Create task**, **List tasks**, **Refresh tokens**, **Logout**. The collection
stores the tokens and created ids as collection variables, and each test script
saves what the next request needs. Logout is last because it revokes the session
and clears the stored tokens. The accounts and passwords in the collection are
test data only.

## Known limitations

- No rate limiting or brute-force lockout on login and registration.
- Access tokens are not revoked on logout; they remain valid until they expire
  (15 minutes by default). Revoking them immediately would require checking
  session state on every request.
- No audit log of authentication events and no endpoint for listing or revoking
  other sessions.
- No email verification, password reset or account-deletion endpoint.
- SQLite is the default local database. PostgreSQL is supported and covered by
  the integration suite, but the application has not been run in a deployed
  environment.
- No deployment configuration, managed secret store, backup procedure or
  monitoring; those are operational concerns outside this repository.

## References

- [Flask documentation](https://flask.palletsprojects.com/en/stable/)
- [Flask application factories](https://flask.palletsprojects.com/en/stable/patterns/appfactories/)
- [Flask-SQLAlchemy documentation](https://flask-sqlalchemy.palletsprojects.com/en/stable/)
- [SQLAlchemy ORM documentation](https://docs.sqlalchemy.org/en/21/orm/)
- [Flask-Migrate documentation](https://flask-migrate.readthedocs.io/en/latest/)
- [Flask-JWT-Extended documentation](https://flask-jwt-extended.readthedocs.io/en/stable/)
- [pytest documentation](https://docs.pytest.org/en/stable/)
