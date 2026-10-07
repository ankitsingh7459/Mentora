# Completed backend handoff for Naman

Owner: Ankit. Team repository: https://github.com/ankitsingh7459/Mentora.git.
Handoff branch: ankit/backend-foundation. This handoff follows remote main at 8a0e08aae9c394a5e02511e4ad4dbddd02da93ba; no force push or merge is part of this task. Original remote README is preserved in project-overview.md; original remote ignore rules are retained.

Included: S0; A1 checkpoints 1, 2, 3, 4a and all of 3b (listing/detail, approve/reject, revoke/reinstate). Checkpoint 4b and final A1 completion remain unstarted. No contributor or moderator appointment workflow was found to separate. Naman retains content moderation, reports, worker and roadmap ownership.

All 42 backend handoff files (source, migrations, tests, dependency/configuration and safe environment example) matched the completed implementation byte-for-byte before Git staging. Reuse the recorded 124 passed, 1 warning, 223.93-second full regression evidence in s0.md: real disposable PostgreSQL; synthetic Supabase HTTP with signed local tokens, not live provider integration. Source/tests were not rebuilt or modified for publishing. Changes here are packaging, README/agent Git coordination and combined ignore rules.

Publishing hygiene: copied an explicit completed-file allowlist; no real .env, virtual environment, caches, temporary test output or database exports were copied. All candidate text files were examined for credential signatures without printing secret values; no private keys, literal signed access tokens, GitHub tokens, AWS keys or production provider secret signatures were found. .env.example contains only documented synthetic localhost credentials and placeholders. Test keys/tokens are generated fixtures. Combined ignore rules exclude private environments/keys/tooling/dumps and preserve .env.example.

## Setup

1. Clone the team repository and switch to ankit/backend-foundation.
2. Follow ../README.md from the repository root: Python 3.12, create .venv, install backend/requirements.lock, copy backend/.env.example to a private backend/.env.
3. Start the documented disposable localhost PostgreSQL 16 container; wait for pg_isready; from backend run Alembic upgrade head (0006_membership_transitions), then Uvicorn app.main:app.
4. Check /health/live, /health/ready and /api/v1/docs.
5. Identity requires a separately configured isolated Supabase project with email confirmation and RS256/ES256 signing. Configure both provider settings privately. Membership requires an explicitly selected/provisioned pilot UUID; do not invent a shared institution or automatically seed.
6. Run the complete suite from backend using ../.venv/Scripts/python.exe -m pytest -q -p no:cacheprovider --basetemp=<fresh-local-test-directory>. Fixtures create/remove their own disposable PostgreSQL containers.
7. Coordinate the single migration history with Ankit before adding revisions. Never downgrade/reset shared data or invoke administrator bootstrap as routine startup.

## Dependency boundary

See api.md for exact Depends signatures and contracts. require_active_membership and require_institution_moderator read current database state; revoked membership and removed grants fail subsequent requests even with an unexpired token. Reinstatement returns student access only. require_platform_administrator is administrative authority, not student-content membership bypass. Contributor checks and appointment APIs are unavailable. No public self-promotion or default administrator.

The original implementation folder is preserved. This separate .handoff-repo checkout was created only after the user supplied and authorized the exact team URL. Historical missing-Git/no-push reports in s0.md describe their earlier task state, not this publication. Deployment and merging remain outside this handoff.

## Publication verification

From the prepared backend checkout, using the existing Python 3.12 virtual environment:
C:/Users/ankit/OneDrive/Desktop/Mentora/.venv/Scripts/python.exe -m pytest -q -p no:cacheprovider --basetemp=.test-tmp-handoff-ignore tests/test_foundation.py
Result: 15 passed, 1 existing Starlette adapter warning, 2.61 seconds. This additionally verifies the merged team ignore rules/configuration. A trailing blank line at EOF in conftest.py was removed for Git whitespace hygiene; no behaviour or assertions changed. Existing full PostgreSQL evidence remains applicable.

git diff --cached --check passes after that whitespace-only cleanup. No local/global Git trust configuration was changed: Git ownership exceptions were command-scoped to this exact cloned checkout. Safe .env.example is included; generated caches/temp files remain ignored.
