# TeamFlow API

A portfolio-sized project and task-management REST API built with **Python, Flask, Flask-SQLAlchemy, SQLAlchemy 2.x, Flask-Migrate/Alembic and pytest**.

TeamFlow is intentionally small enough to understand end-to-end, but substantial enough to demonstrate the backend skills expected of an early-career developer: API design, relational modelling, authentication, authorization, validation, migrations, automated tests and clear technical documentation.

> **Portfolio project, not a production deployment.** It has a useful security baseline, but before real users or sensitive data are involved it still needs operational controls such as request rate limits, monitoring, backups, a managed secret store, deployment hardening and a reviewed threat model.

## What it does

- Register users, securely hash passwords and log in.
- Issue short-lived JWT access tokens and single-use, rotating refresh tokens.
- Detect refresh-token replay, revoke the affected session family, and let each session log out independently.
- Create, view, update and delete projects.
- Add and remove registered users from a project.
- Create, assign, filter, update and delete tasks.
- Enforce project membership so unrelated users cannot read project or task data.
- Validate inputs and return consistent JSON errors.
- Paginate project and task lists.
- Run automated tests against an isolated in-memory SQLite database.
- Manage schema changes with Flask-Migrate and Alembic.

## Technology choices

| Technology | Why it is here |
|---|---|
| Flask | HTTP application, routing and application factory |
| Flask-SQLAlchemy | Flask integration and scoped SQLAlchemy session |
| SQLAlchemy 2.x | Typed ORM models, relationships and queries |
| Flask-Migrate / Alembic | Version-controlled database schema changes |
| Flask-JWT-Extended | Bearer-token authentication and refresh-token flow |
| Werkzeug security | Password hashing and verification |
| pytest | Automated endpoint and permission tests |

The application uses Flask's application-factory and blueprint patterns so configuration, extensions and route groups stay separated. These are established Flask patterns; see the [Flask application factory guide](https://flask.palletsprojects.com/en/stable/patterns/appfactories/) and [blueprints guide](https://flask.palletsprojects.com/en/stable/blueprints/).

## Architecture

```text
teamflow/
├── __init__.py          # create_app(), extension setup, blueprint registration
├── config.py            # environment-based configuration
├── extensions.py        # shared SQLAlchemy, JWT and migration extensions
├── sessions.py          # refresh-session lifecycle: start, rotate, revoke
├── api/
│   ├── auth.py           # register, login, refresh, logout, current user
│   ├── projects.py       # project CRUD and membership management
│   ├── tasks.py          # task CRUD, assignment and filtering
│   ├── errors.py         # centralized JSON HTTP errors
│   └── utils.py          # shared request, response and access helpers
└── models/
    ├── user.py
    ├── session.py        # refresh_sessions and refresh_tokens records
    ├── project.py
    └── task.py

tests/                   # API, authentication and authorization tests
migrations/              # Alembic migration history (create on first setup)
```

### Data model

```text
User 1 ─── * ProjectMembership * ─── 1 Project 1 ─── * Task
 │                                         │              │
 └──────────────── assigned tasks ─────────┴──────────────┘
```

- A user can belong to several projects.
- A project has one owner and one or more memberships. The owner is also recorded as a project member.
- A task belongs to one project and records who created it.
- A task may be assigned to a project member. The API checks membership before allowing an assignment.

The project-membership table is explicit rather than a bare many-to-many join because the relationship carries a role (`owner` or `member`) and a join timestamp.

## Run locally (Windows PowerShell)

Python 3.12 or newer is recommended.

```powershell
# 1. Create and activate a virtual environment
py -3.12 -m venv .venv
.\.venv\Scripts\Activate.ps1

# 2. Install dependencies
python -m pip install --upgrade pip
pip install -r requirements-dev.txt

# 3. Create your local environment file and edit its secrets
Copy-Item .env.example .env
```

Generate two different secrets rather than keeping the example values:

```powershell
python -c "import secrets; print(secrets.token_urlsafe(48))"
```

Run that command twice. Put one value in `SECRET_KEY` and the other in `JWT_SECRET_KEY` inside `.env`.

```powershell
# 4. Create the migration repository and first schema revision (first setup only)
flask --app 'teamflow:create_app' db init
flask --app 'teamflow:create_app' db migrate -m "Create initial schema"
flask --app 'teamflow:create_app' db upgrade

# 5. Start the API
flask --app 'teamflow:create_app' run --debug
```

The API runs at `http://127.0.0.1:5000`. Keep `.env` private; it is excluded by `.gitignore`.

### macOS / Linux

```bash
python3 -m venv .venv
source .venv/bin/activate
python -m pip install --upgrade pip
pip install -r requirements-dev.txt
cp .env.example .env
# Replace both secret values in .env before continuing.
flask --app 'teamflow:create_app' db init
flask --app 'teamflow:create_app' db migrate -m "Create initial schema"
flask --app 'teamflow:create_app' db upgrade
flask --app 'teamflow:create_app' run --debug
```

After the initial migration is created, commit the entire `migrations/` directory. When models change later, use `flask db migrate -m "Describe the schema change"`, review the generated script, and apply it with `flask db upgrade`. **Autogenerated migrations must be reviewed before they are trusted.**

## Try the API in Postman

Import `postman/TeamFlow.postman_collection.json` into Postman. Start the API, run **Register** and **Create project**, then run **Register teammate (run once)** to create the sample second account. Next run **Add project member**, **Create task** and **List tasks**. If the teammate account already exists, skip its registration request. The collection stores the access token and created project/task IDs in collection variables. **Refresh tokens (rotates)** replaces both stored tokens, and **Logout (revoke this session)** is last because it revokes the session and clears them.

## Run tests

```bash
pytest -q
pytest --cov=teamflow --cov-report=term-missing
ruff check .
```

The tests use an in-memory SQLite database and do not need a separate database server. They cover the main happy paths as well as important negative cases: invalid credentials, weak passwords, duplicate accounts, missing authentication, non-member access, non-owner project changes, and assignment to someone outside the project. Session tests add rotation single-use behaviour, replay-driven family revocation, per-device isolation, logout, expiry, deleted accounts, tokens presented against another user's session, timestamp normalization, and rollback safety when a rotation, revocation or logout write fails.

`tests/test_refresh_concurrency.py` is a genuine race test: it uses a temporary file-backed SQLite database, two real threads and two separate Flask test clients released by a barrier, then calls the real `/api/v1/auth/refresh` endpoint ten times over. It asserts that exactly one request wins, the loser gets `401 refresh_token_reused`, the family is revoked in the database and the winner's replacement token is therefore unusable.

### PostgreSQL integration tests

A second, opt-in suite exercises the same session flow against PostgreSQL:

```bash
export TEAMFLOW_POSTGRES_URL=postgresql+psycopg://teamflow:teamflow@localhost:5432/teamflow_test
pytest tests/test_postgres_integration.py -q
```

Point it at a **disposable** database: the fixture runs the real Alembic migrations and downgrades to base afterwards. It covers migration execution, timezone-aware timestamp comparisons, two concurrent refreshes on separate connections and foreign-key cascades. The suite skips when the variable is unset, so the normal SQLite run is unaffected. `.github/workflows/tests.yml` adds a `postgres-integration` job with a PostgreSQL 16 service; that job has not run yet, so PostgreSQL compatibility is prepared but **unverified**.

## API reference

All application endpoints except `/api/health`, registration and login require an access token, unless otherwise stated. Send it as:

```http
Authorization: Bearer <access_token>
```

Successful responses use a `data` envelope. Paginated list responses also include `meta` with `page`, `limit` and `total`. Errors use an `error` object with a stable `code` and human-readable `message`.

**Strict request bodies.** Every write endpoint accepts only the JSON properties documented for it. Unknown properties are rejected with `400` and `error.code == "validation_error"`; they are never silently ignored. Login follows the same rule and accepts only `email` and `password`. Values the server controls — a user's `id`, a project's `owner_id`, a membership `role`, tokens and timestamps — cannot be supplied by clients. Ownership comes from the authenticated user who creates the project, and a new member's role is always assigned by the server. The message names the rejected properties so a client can correct its request.

### Health

| Method | Endpoint | Purpose | Auth |
|---|---|---|---|
| GET | `/api/health` | Check service health | No |

### Authentication

| Method | Endpoint | Purpose | Auth |
|---|---|---|---|
| POST | `/api/v1/auth/register` | Create an account | No |
| POST | `/api/v1/auth/login` | Verify credentials and issue tokens | No |
| GET | `/api/v1/auth/me` | Return the authenticated user | Access token |
| POST | `/api/v1/auth/refresh` | Rotate the refresh token and issue a new pair | Refresh token |
| POST | `/api/v1/auth/logout` | Revoke this session's refresh family | Refresh token |

Register body (`name`, `email` and `password` are the only accepted fields):

```json
{
  "name": "Ada Lovelace",
  "email": "ada@example.com",
  "password": "a-long-unique-password"
}
```

### Sessions, rotation and logout

The API does not treat a refresh token as a reusable key. Each successful `POST /api/v1/auth/refresh` **consumes** the token that was sent and returns a replacement:

```json
{
  "access_token": "…",
  "refresh_token": "…",
  "token_type": "Bearer",
  "session_id": "4d1f…",
  "refresh_token_expires_at": "2026-12-05T12:00:00Z",
  "refresh_token_rotated": true
}
```

Timestamps in responses are ISO 8601 in UTC with an explicit `Z`, so clients never have to guess which timezone an expiry belongs to.

**Strict rotation rules.** The server allows one refresh per session at a time, and the rules are enforced rather than advisory:

- Store both returned tokens immediately and discard the previous refresh token; the response is the only place the replacement exists.
- Sending a refresh token twice — two overlapping requests, or a retry after a lost response — is treated as reuse. The API answers `401 refresh_token_reused` and revokes that session's whole token family, so the client must sign in again on that device. The user's other sessions stay signed in.
- Do not run parallel refreshes for one session. A single-flight refresh queue on the client is the correct design.

Errors on this path are `invalid_refresh_token`, `session_revoked`, `expired_token` and `refresh_token_reused`, plus service errors that revoke nothing: `500 refresh_failed` (the rotation could not be completed; the same token can be retried), `500 revocation_failed` (reuse was detected but the revocation could not be committed) and `500 logout_failed` (logout did not take effect). Database failures are never reported as token reuse, and error responses never include SQL, connection strings or driver details.

Every registration and every login opens a **separate session**. A session groups the refresh tokens that rotate into each other (a token family). Revoking one session therefore leaves the user's other devices signed in:

- `POST /api/v1/auth/logout` with the current **refresh token** revokes that session. It is idempotent and returns `{"session_id": "…", "revoked": true, "already_revoked": false}`.
- Subsequent refreshes with that session return `401 session_revoked`.
- **Access tokens are not revoked.** The server does not check session state when it validates an access token, so an already-issued access token stays usable until it expires (15 minutes by default). Short access-token lifetimes are currently the only limit on that window; a deliberate access-token revocation list would be the next step.
- Logout requires a refresh token. If the refresh token has already expired, the client cannot call logout; discard the tokens and sign in again.

What is stored: the database never contains a refresh token or its signature. `refresh_sessions` holds the session id (128 random bits), owner, creation and absolute expiry, revocation timestamp and reason, and last rotation time. `refresh_tokens` holds the JWT identifier (`jti`), issue/expiry timestamps, the single `consumed_at` timestamp and a link to the token that replaced it. Rotation consumes a token with one conditional `UPDATE … WHERE consumed_at IS NULL`, so two concurrent requests carrying the same token cannot both succeed; the loser changes zero rows and is handled as replay.

### Projects and members

| Method | Endpoint | Purpose | Permission |
|---|---|---|---|
| GET | `/api/v1/projects?page=1&limit=20` | List projects the user belongs to | Member |
| POST | `/api/v1/projects` | Create a project | Any authenticated user |
| GET | `/api/v1/projects/{project_id}` | Get project details and members | Member |
| PATCH | `/api/v1/projects/{project_id}` | Update project details | Owner |
| DELETE | `/api/v1/projects/{project_id}` | Delete a project | Owner |
| GET | `/api/v1/projects/{project_id}/members` | List project members | Member |
| POST | `/api/v1/projects/{project_id}/members` | Add a registered member by email | Owner |
| DELETE | `/api/v1/projects/{project_id}/members/{user_id}` | Remove a member | Owner |

Create project body (`name` and `description` are the only accepted fields; the project owner is the authenticated user, so `owner_id` is rejected):

```json
{
  "name": "Website launch",
  "description": "Coordinate the new company website."
}
```

Add member body (`email` is the only accepted field; the server assigns the `member` role):

```json
{ "email": "grace@example.com" }
```

A member must register before being added. The project creator is automatically recorded as its owner and as a member. The owner cannot remove themselves.

### Tasks

| Method | Endpoint | Purpose | Permission |
|---|---|---|---|
| GET | `/api/v1/projects/{project_id}/tasks?page=1&limit=20` | List project tasks | Member |
| GET | `/api/v1/projects/{project_id}/tasks?status=in_progress` | Filter by status | Member |
| GET | `/api/v1/projects/{project_id}/tasks?assigned_to_me=true` | List tasks assigned to the current user | Member |
| POST | `/api/v1/projects/{project_id}/tasks` | Create a task | Member |
| GET | `/api/v1/tasks/{task_id}` | Get task details | Project member |
| PATCH | `/api/v1/tasks/{task_id}` | Update task fields | Project member |
| DELETE | `/api/v1/tasks/{task_id}` | Delete a task | Creator or project owner |

Create task body:

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

Allowed task statuses: `todo`, `in_progress`, `done`. Allowed priorities: `low`, `medium`, `high`. `due_date` is optional and uses `YYYY-MM-DD`. `assigned_to_id` may be omitted, set to a project member's user ID, or set to `null` to unassign.

## Try it with curl

After registering or logging in, copy the access token from the response into `TOKEN` (PowerShell) or `TOKEN` (bash):

```bash
curl -X POST http://127.0.0.1:5000/api/v1/projects \
  -H "Authorization: Bearer $TOKEN" \
  -H "Content-Type: application/json" \
  -d '{"name":"Website launch","description":"A sample project"}'
```

Then use the returned project `id` to create and list tasks. On Windows, `curl.exe` is available in current versions of Windows; PowerShell's `curl` alias behaves differently, so call `curl.exe` explicitly.

## Engineering notes and known next steps

The code deliberately keeps request validation near each endpoint and keeps database entities in model classes. The application factory makes it possible to create a separate testing app with an isolated database. Project and task queries always check membership before returning records; unauthorized project IDs return `404` to avoid confirming that another user's project exists. The caller's account is resolved once, in a single JWT user-lookup hook: a correctly signed token whose account has been deleted is rejected with `401 account_not_found`, while an invalid or expired token keeps its own error code.

Before treating this as production-ready, the next improvements should include:

1. Add rate limiting to registration and login, plus audit logging for authentication events.
2. Add access-token revocation (for example a short denylist or a per-session token version) so logout takes effect immediately instead of after the access token expires.
3. Finish PostgreSQL verification: the opt-in suite and CI service are prepared, but they have not executed against a real PostgreSQL server yet.
4. Add structured request logging, metrics, health checks for database connectivity and error monitoring.
5. Add deployment configuration, managed secrets, database backups and a documented recovery procedure.
6. Review authorization rules with a threat model, including member removal and task reassignment edge cases.

## How to explain this project in an interview

> “I built TeamFlow API to practise backend engineering with Flask and SQLAlchemy. It provides authentication, projects, team membership and task management. I modelled users, projects, memberships and tasks relationally, used JWTs for API authentication, and enforced membership checks before users can read or change project data. I used Flask-Migrate for schema history and pytest to test both normal workflows and access-control failures. I also kept the application factory and blueprints separate so the code is easier to test and extend.”

Be ready to explain one request from start to finish: **HTTP request → route/blueprint → input validation → authorization check → SQLAlchemy query/transaction → JSON response → test covering the behavior.**

## References

- [Flask documentation](https://flask.palletsprojects.com/en/stable/)
- [Flask application factories](https://flask.palletsprojects.com/en/stable/patterns/appfactories/)
- [Flask-SQLAlchemy quickstart](https://flask-sqlalchemy.palletsprojects.com/en/stable/quickstart/)
- [SQLAlchemy ORM documentation](https://docs.sqlalchemy.org/en/21/orm/)
- [Flask-Migrate documentation](https://flask-migrate.readthedocs.io/en/latest/)
- [Flask-JWT-Extended documentation](https://flask-jwt-extended.readthedocs.io/en/stable/)
- [pytest documentation](https://docs.pytest.org/en/stable/)
