# Mentora backend

S0 and A1 are owned by Ankit. S0's shared foundation is preserved; A1 checkpoints 1-3, 4a and 3b membership administration add verified identity, own-profile read/update, membership requests, current member/role checks, explicit administrator bootstrap and atomic pending-request review. Approved policies and verification evidence are in [docs/s0.md](docs/s0.md); the API/error handoff is in [docs/api.md](docs/api.md). Remaining A1 checkpoints are in [tasks/plan.md](tasks/plan.md).

## Local setup on Windows PowerShell

Prerequisites: Python 3.12, Docker Desktop running Linux containers, and Git for team version control. Run from the repository root. The completed handoff is on ankit/backend-foundation in the team repository. Clone https://github.com/ankitsingh7459/Mentora.git and check out that branch before setup. The original project overview is preserved in [docs/project-overview.md](docs/project-overview.md).

```powershell
py -3.12 -m venv .venv
.\.venv\Scripts\python.exe -m pip install -r backend/requirements.lock
Set-Location backend
Copy-Item .env.example .env
docker run --detach --rm --name mentora-s0-local --publish 127.0.0.1:55432:5432 --env POSTGRES_USER=mentora --env POSTGRES_PASSWORD=local-test-only --env POSTGRES_DB=mentora_s0_test postgres:16
docker exec mentora-s0-local pg_isready -U mentora -d mentora_s0_test
# Repeat pg_isready until it reports accepting connections.
..\.venv\Scripts\python.exe -m alembic upgrade head
..\.venv\Scripts\python.exe -m uvicorn app.main:app --host 127.0.0.1 --port 8000
```

The example credentials are synthetic and only for the disposable local container. Do not reuse them on a shared database. The `--rm` container has no persistent volume; stopping it deletes its data. Docker is used here only for isolated PostgreSQL; API/worker deployment and CI belong to later tasks.

In another terminal:

```powershell
Invoke-RestMethod http://127.0.0.1:8000/health/live
Invoke-RestMethod http://127.0.0.1:8000/health/ready
# After finishing the local demo, remove only the container you created:
docker stop mentora-s0-local
```

OpenAPI: `http://127.0.0.1:8000/api/v1/openapi.json`; interactive docs: `http://127.0.0.1:8000/api/v1/docs`. Stopping PostgreSQL leaves liveness at 200 and changes readiness to 503. Configuration is checked at app startup, without connecting to PostgreSQL. Missing/invalid settings report only affected variable names. `.env` is loaded from the backend working directory; environment variables override it.

## Tests

From `backend/`:

```powershell
# Fast checks; database failure is injected, not a real integration claim.
..\.venv\Scripts\python.exe -m pytest -m 'not postgres' -q
# Complete suite: automatically creates a randomly named disposable PostgreSQL
# container on a dynamic localhost port, upgrades/downgrades, probes sessions,
# stops its own database to verify outage handling, and cleans up in finally.
..\.venv\Scripts\python.exe -m pytest -q
```

Full tests require Docker and permission to pull the PostgreSQL image. They never use `DATABASE_URL` from your environment for integration tests. A Docker failure fails the suite rather than silently skipping PostgreSQL. `TEST_POSTGRES_IMAGE` may specify an approved PostgreSQL 16 image digest for reproducible CI. Python dependencies are fully resolved in `backend/requirements.lock` for Python 3.12; when intentionally updating dependencies, install `.[test]` in a clean virtual environment and regenerate the lock with `python -m pip freeze --exclude mentora-backend`. Do not substitute the bundled artifact Python runtime for normal teammate setup.

The tested image digest and exact verification results are in [docs/s0.md](docs/s0.md). In this agent's Windows sandbox, pytest's usual temp/cache directories had permission conflicts. The verified alternative uses a fresh directory under the ignored virtual environment:

```powershell
$testTemp = Join-Path (Resolve-Path '..\.venv') ('s0-tests-' + [guid]::NewGuid().ToString('N'))
..\.venv\Scripts\python.exe -m pytest -q -p no:cacheprovider --basetemp=$testTemp
```

## Configuration and migrations

| Variable | Requirement |
| --- | --- |
| `DATABASE_URL` | Required `postgresql+psycopg://user:password@host:port/database`; URL-encode special characters in credentials; only optional `sslmode` URL parameter is supported |
| `DB_CONNECT_TIMEOUT_SECONDS` | Integer 1–30; proposed local default 3; also bounds pool checkout |
| `DB_STATEMENT_TIMEOUT_MS` | Integer 1–30000; proposed local default 3000 |

Timeouts are foundation defaults pending measurements, not achieved performance guarantees. Connection and statement deadlines cover ordinary local readiness probes; DNS/network failures may have OS-dependent timing. `.env*`, private keys, caches and virtual environments are ignored. Never print settings, connection strings, raw exception traces, tokens or SQL parameters. SQL echo is disabled. Database and unexpected HTTP failures return safe messages without raw exception logging.

One Alembic history is shared by Ankit and Naman. `0001_foundation` remains unchanged and empty. `0002_identity_profile` creates only `profiles` with a UUID primary key, optional display name, constrained active/blocked state and UTC-capable timestamp. No app startup schema creation occurs. From `backend/`, inspect `python -m alembic heads` before adding a revision; register only implemented models. Review autogenerated migrations. Run upgrades only on a known target; use disposable PostgreSQL first. Never rewrite migrations used by teammates. Downgrading `0002_identity_profile` deletes local profiles; it is tested only on disposable resources and must not be run on a shared database as a routine rollback.

For teammates using Linux/macOS, use `python3.12 -m venv .venv`, `.venv/bin/python`, and after entering `backend`, `../.venv/bin/python` for the same commands. Dependencies and platform wheels must be verified on the chosen delivery platform before deployment.

## A1 checkpoint 1 identity configuration

Health probes still run with database-only configuration. `GET /api/v1/me` fails closed with 503 until both `SUPABASE_URL` (HTTPS project origin without path/credentials) and `SUPABASE_PUBLISHABLE_KEY` are configured. Partial/invalid configuration produces the same sanitized startup error convention as S0. A privileged Supabase secret/service key is not accepted here. Optional `SUPABASE_JWT_AUDIENCE` defaults to `authenticated`; `AUTH_HTTP_TIMEOUT_SECONDS` is 1–10, default 3 per HTTP phase. This is not a measured latency/overall deadline guarantee.

For a separately authorized isolated Supabase project, configure email/password sign-in with mandatory email confirmation, disable other pilot login methods, and use RS256/ES256 asymmetric signing keys. The backend uses the fixed project's JWKS and authenticated `/auth/v1/user` endpoint: it verifies token signature/issuer/audience/expiry/subject, then requires the current matching user to be non-anonymous with confirmed email. JWT/user-metadata roles do not grant application privileges. This checkpoint deliberately rejects legacy HS256; it requires a separately tested design if the selected project uses that mode. No real Supabase project was configured or contacted during verification.

Login/signup/verification/recovery use Supabase's frontend Auth integration; this backend stores no password and exposes no duplicate login API. Do not paste access tokens or keys in chat, query strings or logs. The own-profile response contains only `userId` and `displayName`; first successful access creates a minimal profile with no membership or elevated rights. A locally blocked account is denied even with the same unexpired token. Membership administration and privilege-management APIs are not implemented; the explicitly invoked administrator bootstrap is documented below.

`PATCH /api/v1/me` accepts only `displayName`: a trimmed 2–80-character string or explicit null to clear it. Empty `{}`, blank/invalid strings, unknown and protected fields return 422 without any committed changes. It uses the same verified identity/current-profile checks as GET and returns the same safe fields. The HTTP operation owns the commit, including first-use initialization; invalid/failed updates roll back everything. See [docs/api.md](docs/api.md) for error codes, null/omission rules and the frontend handoff. No profile schema, dependency or signing-algorithm change is required.

Application tables require a trusted server-side database role. The tested API connection owns the migrated profile table; a separate delivery role requires a reviewed server-access policy/privilege setup before integration. The profile migration enables RLS with no browser-access policies and revokes PUBLIC grants; a browser role cannot read or write profiles even if it has table grants. Do not add direct Supabase browser policies that bypass backend checks. Review deployment grants before real integration. The API process uses only server-side database credentials.

The maintained tests use real locally signed ES256/RS256 JWTs, synthetic Supabase HTTP responses and disposable PostgreSQL. They verify endpoint denial, current blocked state, concurrent profile initialization, rollback, migration downgrade/upgrade and RLS denial. They are not proof of live Supabase email delivery, provider configuration or frontend login behaviour.


## Checkpoint 3 pilot membership setup

Migration head is `0003_membership_requests`, following unchanged `0002_identity_profile` and `0001_foundation`. From backend, run `..\.venv\Scripts\python.exe -m alembic upgrade head` only against your explicitly selected isolated local database. It creates institutions and current memberships; it seeds nothing. Downgrading to `0002_identity_profile` deletes all membership/institution data and preserves profiles; never use this as a shared-data reset.

An operator must select the actual pilot UUID, then provision that same UUID using a trusted database connection:

```sql
-- Replace the placeholder only with the explicitly agreed pilot UUID.
INSERT INTO institutions (id) VALUES ('<operator-selected-pilot-uuid>');
```

Set `PILOT_INSTITUTION_ID` to that UUID in private local configuration, then restart the app. No real pilot UUID was supplied or invented; tests use random synthetic UUIDs only. Missing configuration leaves health/profile endpoints available and membership requests fail closed with 503. A configured but absent record returns 404. No startup seeding, institution creation API or provider mutation exists. Shared provisioning/migrations require separate authorization.

POST membership requests require an explicit empty JSON object `{}` and verified identity/current active account. Owner status is read by request ID; see docs/api.md for examples. Approval/rejection/revocation/reinstatement APIs, contributor/moderator/admin checks and bootstrap are not implemented here.


## Checkpoint 4a explicit first-administrator bootstrap

Migration head is `0004_administration_prerequisite`, following unchanged revisions 0001-0003. Upgrade only your explicitly selected isolated local database using the existing Alembic command. This adds current platform administrators, institution moderators and a singleton bootstrap event. It seeds no privileges and startup never invokes bootstrap. Downgrading this revision destroys privileges AND the durable event; re-upgrading would reopen bootstrap. Never use downgrade, TRUNCATE or marker deletion as a shared/production reset or recovery procedure. Tests reset only their own randomly named disposable database.

Create/select an existing email-confirmed Supabase user in a separately authorized isolated project. Use the same configured SUPABASE_URL/publishable key/RS256-or-ES256 signing setup as normal identity verification. The operator must obtain that target user's short-lived Supabase access token through a trusted isolated sign-in flow; no application password or privileged service key is needed. Do not put the token in shell commands, environment variables, files, logs or chat.

From backend in an interactive terminal, explicitly invoke:

```powershell
..\.venv\Scripts\python.exe -m app.modules.identity.bootstrap --user-id <existing-verified-supabase-user-uuid>
```

Replace the placeholder with the target UUID only. The command prompts for the target access token without echo and refuses terminals that require echoed fallback. It reuses signature/issuer/audience/expiry checks plus the trusted current `/auth/v1/user` lookup, requiring the exact UUID and confirmed non-anonymous email. A matching existing local profile alone is insufficient. It rejects blocked accounts. Database/provider configuration comes from the same private local configuration as the API; no credentials are accepted as command arguments.

Success returns `{"status":"bootstrapped"}` and exit 0. Denial/failure returns the safe error envelope on stderr and exit 1, with no raw token, provider or SQL detail. `--help` only displays usage. Global advisory transaction serialization checks both existing administrator grants and the durable singleton event, then commits any profile initialization, grant and event together. The event records target, time, `operator_command` origin and a fixed bootstrap reason; it does not claim to identify a human operator. Normal UPDATE/DELETE of the event is rejected. Removing a grant/profile never reopens bootstrap. Database owners can still deliberately destroy schema/data; this is not tamper-proof against a database owner.

The administrator role is global administrative authority and does not create student membership or grant arbitrary institution-content access. Moderators require active membership in their assigned institution. No public signup/promotion or moderator appointment endpoint exists. This command was tested only on disposable PostgreSQL with synthetic Supabase HTTP; no shared/production command was invoked. Live provider integration and interactive terminal behaviour need separately authorized environment verification.


## Checkpoint 3b membership review slice

Migration head is `0006_membership_transitions`, after unchanged revisions 0001-0005. Use the same documented `alembic upgrade head` only against your explicitly selected isolated local database. No new configuration/dependency or seed. This revision adds immutable request-key-unique review audit; downgrading to 0004 destroys review history but preserves memberships, profiles, privileges and bootstrap event. It is not a shared-data reset or production operation.

Institution-scoped GET list/detail and POST decision use current platform-administrator or assigned active-moderator permission. Approval grants student membership only; no automatic moderator/contributor/admin role. The existing owner status/re-request APIs keep their response shapes and rules. Frontend examples, bounded pagination, reason/null rules and conflicts are in docs/api.md. Revocation/reinstatement uses current scoped authority, strict reason and expectedVersion, atomic grant invalidation and immutable transition audit. No bootstrap was needed or invoked to seed reviewer fixtures.


Checkpoint 3b revocation/reinstatement adds revision 0006: a nonnegative membership transition version and immutable transition ledger. Run the documented migration command only on your explicitly chosen isolated local database. Downgrade to 0005 removes transition audit/version data and resets the version fence if upgraded again; it preserves current membership status, previous review records, profiles and remaining privilege/bootstrap records. It is not an operational rollback for shared data.

For reproducible Windows tests when the system pytest temporary directory or OneDrive cache is inaccessible, run from backend:
../.venv/Scripts/python.exe -m pytest -q -p no:cacheprovider --basetemp=.test-tmp-transition-check
Use a new ignored .test-tmp-* directory for a subsequent run; pytest may clear its selected basetemp. These tests provision uniquely named disposable PostgreSQL containers and remove them in teardown. Never point them at a shared database.
