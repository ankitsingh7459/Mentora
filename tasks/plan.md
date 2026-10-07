# A1 checkpoints for Ankit

Policies approved in docs/s0.md. Execute only checkpoint 3b in the current request. Preserve S0 and all completed A1 checkpoints. The original workspace lacked Git. This handoff checkout now follows the authorized team history on ankit/backend-foundation; no merge or deployment is authorized.

Checkpoints 1-3 (with checkpoint 3 limited to the selected request/status/access-check scope) are implemented and verified; see docs/s0.md for exact evidence. Checkpoint 3 request/status/access-check work is complete. Checkpoint 4a is complete. Checkpoint 3b includes listing/detail, pending-request review and explicitly approved revoke/reinstate.

## Checkpoint 1 verified identity and own-profile read

Implement GET /api/v1/me with signature, configured issuer/audience, expiry, required claims and UUID subject validation; mandatory authoritative email confirmation; and current PostgreSQL blocked-account checks. Only a local profile/account-state table is needed. A first verified read creates a minimal profile with no membership or privileges. Never copy token roles or editable metadata into authority. No profile update or institution-protected endpoint yet.

Use supported JWT verification against fixed configured Supabase asymmetric RS256/ES256 JWKS, followed by its authenticated user endpoint. Do not derive trusted URLs from token headers. Reject other algorithms; legacy HS256 requires a separately tested provider-verification design if the selected project needs it. No actual provider project was supplied: local signed tokens and synthetic provider HTTP responses are test evidence, not real Supabase integration. Configuration remains optional for S0 health; identity endpoints fail closed with 503 until configured.

Invalid credentials: safe 401. Unverified/anonymous or blocked account: safe 403. Missing provider config, provider/key service failure or database failure: safe 503. Never log tokens, keys, raw provider/SQL errors. Test malformed tokens, wrong signature/issuer/audience/expiry/subject, forged roles, provider identity mismatch, confirmation, outage and rotation. Use disposable PostgreSQL for migrations, concurrent profile initialization, rollback/no-write on rejected authentication and current blocked-state denial with a still-valid token. Retain S0 health/error checks. Stop after report.

## Checkpoint 2 own-profile update

Implement PATCH /me with the approved displayName-only contract: trim strings then require 2–80 characters; null clears; unknown/protected fields and empty requests return 422 atomically. Reuse verified identity/current active-profile dependencies and return only userId/displayName. No academic fields or verification uploads. Move initialization commit ownership to HTTP GET/PATCH operations so invalid/failed first-use PATCH rolls back initialization and update together. Verify authorization, validation, real PostgreSQL persistence/rollback and shared 422 schema; signing algorithms remain unchanged.

## Checkpoint 3 membership lifecycle

Latest user scope supersedes the earlier broad lifecycle slice: implement configured-pilot pending requests, owner-only status reads and reusable current active-membership checks. Test atomic uniqueness/concurrency, rejected re-requests, revoked refusal, identity/account denial, cross-user/institution denial and rollback. Membership administration APIs (approve/reject/revoke/reinstate) require later explicit authorization and role/audit conventions; synthetic fixtures exercise these states here. No privilege or bootstrap endpoints. This is Ankit identity work, not content moderation.

## Checkpoint 4a administration prerequisites (current selected slice)

Bring the original checkpoint 5 bootstrap prerequisite forward; implement only minimal platform-administrator and institution-moderator grants, current role dependencies and an explicit operator-only first-administrator command with atomic immutable singleton event. Reuse existing Supabase signed-token/current confirmed-email verification, require its subject to match operator-selected UUID, accept the short-lived target token only through hidden interactive input, reject blocked/unverifiable targets. No new privileged provider credential or signing algorithm. PostgreSQL serialization plus durable singleton closes repeat/concurrent bootstrap even after grant/profile removal. Administrator check is global administrative authority, not a student-membership/content bypass; moderator check requires current active target-institution membership. No administration HTTP routes.

## Checkpoint 3b membership administration (current selected task)

Listing/detail and pending-request review are complete. The user explicitly retains revoke/reinstate in 3b and authorizes only that remaining slice: current scoped authority, no self-action, administrator-only moderator targets, no platform-administrator targets, required trimmed 1-500 reason, atomic state/grant invalidation/immutable audit and version precondition. Active becomes revoked; revoked becomes active student only. Pending/rejected cannot bypass review. Contributor grants do not exist; later implementation must clear them on revocation and prevent dormant grants returning on reinstatement. Preserve re-request and revoked refusal in existing APIs. No contributor/moderator appointment or content workflows.

## Checkpoint 4b contributor and moderator administration (not started)

Implement manual senior contributor grant/revoke with active membership, no self-grant, current-state permission checks and atomic actor/time/reason audit. Only platform administrators appoint/remove institution moderators. Test role escalation and cross-institution denial and subsequent denial after privilege removal.

## Checkpoint 5 remaining A1 handoff

Original checkpoint 5 bootstrap is moved forward to 4a, not a second bootstrap implementation. After separately authorized 3b/4b work, complete remaining A1 validation and handoff; no automatic progression to A2.

## Shared conventions and dependencies

Reuse Settings, Base, get_session, shared error envelope and one Alembic history. One explicit transaction owner per operation: authentication queries autobegin, so later writers must not blindly nest session.begin(). Since checkpoint 2, profile lookup does not commit; GET/PATCH explicitly commit their operations, including initialization if required. Session cleanup never commits. Later privileged writes require current-state checks and atomic audit transactions.

Naman retains content moderation, reports, worker, roadmaps/private progress and delivery. No later-feature scaffolding, provider-account mutation, shared migrations, push or deployment. Evolve the S0-only exact-head/table assertions to test the selected current schema without weakening downgrade/rollback coverage.

Technical defaults: 3-second HTTP timeout, 8192-character bearer limit, fixed provider paths and no local JWKS caching initially. These are measured-later implementation limits, not business rules. Operational inputs still needed for real integration: isolated Supabase URL/publishable key/asymmetric signing configuration; pilot institution for checkpoint 3. Do not paste secrets into chat. See tasks/todo.md for status.

Checkpoint 3b overall complete: 124 full-suite tests passed in 223.93 seconds. Stop here. Next bounded task is 4b contributor/moderator administration, not authorized or started.
