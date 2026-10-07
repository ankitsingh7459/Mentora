# Shared API conventions and Naman handoff

Business routers use `app.main.API_PREFIX`, fixed to `/api/v1`. A1 checkpoints 1-3 implement own-profile and membership-request endpoints; process probes remain outside this prefix. Register typed routers inside `create_app`; use FastAPI's generated OpenAPI as the implemented schema. Document `ErrorResponse` statuses on new routes, including validation errors; reconcile FastAPI's default 422 schema whenever request validation is introduced.

| Endpoint | Auth | Success | Failure |
| --- | --- | --- | --- |
| `GET /health/live` | None | 200 `{"status":"alive"}`; does not use a session | Process cannot respond if stopped |
| `GET /health/ready` | None | 200 `{"status":"ready"}` after `SELECT 1` | 503 `DATABASE_UNAVAILABLE` |

Readiness checks connectivity, not schema version, business permissions or all downstream services. Both probes accept no request payload. No configuration or database identity is returned.

Every application response has a server-generated UUID `X-Request-ID`; client IDs are not trusted. Error example:

```json
{
  "error": {
    "code": "DATABASE_UNAVAILABLE",
    "message": "Database unavailable.",
    "requestId": "e3bbeb3e-851d-48bf-b889-d546b7bfd2ba",
    "details": []
  }
}
```

The body request ID matches the response header. Shared handlers cover 404 `NOT_FOUND`, 405 `METHOD_NOT_ALLOWED`, 422 `VALIDATION_ERROR`, and 500 `INTERNAL_ERROR`. Validation details contain only field location and machine error type, never submitted values or validator exception text. Other HTTP exceptions use `HTTP_ERROR` and a generic safe message. Later domain tasks must define approved public codes/messages with `error_response` instead of putting sensitive data in exception details. Auth and concealed-resource status policies remain for A1.

Naman: reuse `app.config.Settings`/`load_settings`, `app.db.Base`, `create_db_engine`, `get_session`, and `app.errors.error_response`/`ErrorResponse`. Do not create another settings loader, database metadata, migration history or error format. HTTP handlers obtain `Session` with `Depends(get_session)`; sessions close after each request and uncommitted work rolls back. Services explicitly own transactions or commits, accounting for authentication reads as described below. Do not assume dependency exit commits. API startup owns and disposes its engine; a future worker must own and dispose its separately started engine using the same helper. Synchronous database work must run in synchronous routes/threadpool, not block async endpoints. Coordinate all shared schema changes with Ankit. Identity context now exists; institution permissions and the publication service still require later Ankit handoffs before moderation implementation.

This is a repository handoff for Naman; no external message was sent.

## A1 checkpoint 1 own-profile read

`GET /api/v1/me` uses `Authorization: Bearer <Supabase access token>`; OpenAPI declares HTTP Bearer security. It accepts no body and has no identity/role query parameters. An extra caller-supplied user ID never selects another profile. A verified user may read their own profile without institution membership; this permission does not permit institution content access.

200 example:

```json
{"userId":"0d739fa3-e7d5-4b27-989f-4a316caf71dd","displayName":null}
```

The first successful verified read initializes a local profile using only the verified UUID. This idempotent initialization is an explicit side effect of the onboarding read, enforced by the UUID primary key and PostgreSQL conflict-safe insert. Repeated/concurrent reads never create multiple profiles or elevate rights. Provider metadata is not copied into profile fields or permission state. No membership, contributor/moderator/admin flags are returned or created.

| Status | Code | Meaning |
| --- | --- | --- |
| 401 | INVALID_CREDENTIALS | Missing/malformed bearer, disallowed algorithm, invalid signature/issuer/audience/expiry/UUID, unknown key or provider subject mismatch; `WWW-Authenticate: Bearer` |
| 403 | EMAIL_VERIFICATION_REQUIRED | Anonymous/no current confirmed email |
| 403 | ACCOUNT_BLOCKED | Current local account state denies access |
| 503 | AUTH_UNAVAILABLE | Unconfigured provider, key/provider outage/rate limit, malformed/oversized response or unavailable key set |
| 503 | DATABASE_UNAVAILABLE | Profile persistence/read failure |

All use the existing `error: {code,message,requestId,details}` shape with empty details and matching `X-Request-ID`. No request body/parameter schema exists here, so this endpoint has no normal 422 response. Future PATCH/membership routes must declare the shared validation error model. HTTP failures never return raw JWT, provider, SQL or credential data. The same safe `PublicError` mechanism is available for reviewed application codes/messages; never pass raw exceptions/input into it.

Naman may reuse `identity.auth.get_verified_subject` (verified UUID) and `identity.router.get_current_profile` (current active profile) with `Depends`. These are identity/own-profile checks, **not membership/moderator authorization**. Use the current membership/role dependencies documented below only under an explicit approved workflow policy; no protected moderation workflow is implemented here. No publication service exists yet. Current-profile lookup shares the S0 session and autobegins a transaction. Since checkpoint 2 it may initialize a profile but never commits: GET/PATCH own their commits so failed/invalid PATCH requests also roll back initialization. Later service transactions must account for that lifecycle and commit state/audit together rather than blindly nesting `session.begin()` after authentication reads. Session cleanup still never commits.

## A1 checkpoint 2 permitted own-profile update

User-approved allowlist: **only `displayName`**. `PATCH /api/v1/me` uses the same bearer verification and current active-account dependency as GET. The UUID is selected from verified identity; query/body IDs never select another user's record. Names convey no verification/roles/permissions.

```json
{"displayName":"  Ankit Singh  "}
```

200 response (also returned by subsequent GET):

```json
{"userId":"0d739fa3-e7d5-4b27-989f-4a316caf71dd","displayName":"Ankit Singh"}
```

Strings are trimmed before validating 2–80 Unicode characters. Empty/whitespace-only names, other types and lengths outside that range return 422. Explicit `{"displayName":null}` clears the field and returns a nullable name. Omitted fields remain unchanged; because this is the sole editable field, omitting it yields an empty permitted request and `{}` returns 422 without changing anything. Missing/non-object/malformed bodies also fail validation. Unknown fields and protected fields (including IDs, email/verification/account status, roles, memberships, contributor/admin state) are forbidden, never ignored; mixing them with a valid name rejects the whole payload.

Responses use the same safe `OwnProfile` fields (`userId`, nullable `displayName`) and errors/request IDs as GET. Additional response: **422 VALIDATION_ERROR** with only field location/machine error type in `details`, never submitted values. Generated OpenAPI declares the shared `ErrorResponse` for 422, HTTP Bearer security, required-but-nullable bounded `displayName` and `additionalProperties:false`. Required presence enforces the approved empty-request rejection; omission never becomes an implicit clear.

Transactions: validation or failure leaves no partial changes, including no committed first-use profile initialization. PATCH owns one commit covering any initialization and the update. The UPDATE also requires current `account_status='active'` at write time; a block committed since lookup is denied. Concurrent successful updates follow database write order (last committed update wins); no new revision field/precondition is introduced in this checkpoint. Failures roll back and return a sanitized DATABASE_UNAVAILABLE 503.

Frontend handoff for Dhruv/shared profile UI: send only changed permitted fields; do not send `{}`. Send a string to set the name or null for an explicit clear action. Replace cached own-profile data with the returned `{userId,displayName}` only after success; preserve unsaved input on a recoverable error. Handle 401 (authentication), 403 (verified-email/current blocked-account denial), 422 (field validation) and 503 (provider/database unavailable). Membership approval or role display must not be inferred from the name. No frontend code or external teammate message was sent in this task.


## A1 checkpoint 3 membership requests and active-member checks

Only the explicitly configured/provisioned pilot institution is supported. Institution IDs are UUID path values; no public institution-provisioning endpoint exists. Missing PILOT_INSTITUTION_ID returns 503 MEMBERSHIP_UNAVAILABLE. Nonexistent or unsupported UUID returns 404 INSTITUTION_NOT_FOUND, without records. Invalid UUID returns shared 422. All routes require the same verified identity and active-profile checks; token roles never authorize a request.

`POST /api/v1/institutions/{institution_id}/membership-requests` requires JSON `{}`. Unknown/protected fields (user IDs, approval state, roles, reviewer IDs, timestamps) and missing/non-object bodies return shared 422; identity comes only from the verified subject. Query user IDs/roles have no authority. A first request is 201:

```json
{"requestId":"00000000-0000-4000-8000-000000000010","institutionId":"00000000-0000-4000-8000-000000000020","status":"pending","requestedAt":"2026-10-07T12:00:00Z"}
```

These UUIDs are illustrative synthetic values, not an actual provisioned pilot. Pending grants no protected institution access. The transaction serializes on the current profile row and locks existing membership; a composite primary key enforces one current membership per user/institution. It rechecks active profile after locking. Initialization and request commit together; validation, denial and SQL failure leave no partial writes. PostgreSQL READ COMMITTED row-lock behaviour is described in the [official documentation](https://www.postgresql.org/docs/16/transaction-iso.html).

| Current state | POST response | Effect |
| --- | --- | --- |
| No membership | 201 pending | Create current request |
| pending | 409 MEMBERSHIP_REQUEST_PENDING | Existing request unchanged; read its known request ID |
| active | 409 MEMBERSHIP_ALREADY_ACTIVE | Existing membership unchanged |
| rejected | 201 pending | Replace current request ID/time, retain user/institution; no access granted |
| revoked | 409 MEMBERSHIP_REINSTATEMENT_REQUIRED | No change; authorized reinstatement required |

Current state is persisted, not an application/request-history ledger. Rejected re-request replaces the previous request identity; old request IDs then return 404. No prior-review history or audit is fabricated. Administration and its required audit records are deferred. Frontend should retain requestId from 201; duplicate 409 does not reveal a request ID, and a lost response requires later recovery/discovery design. No list/recovery endpoint was added in this minimal checkpoint.

`GET /api/v1/membership-requests/{request_id}` returns 200 with the same safe fields and current state (pending/active/rejected/revoked). It selects the request AND verified owner. Another user's or nonexistent/obsolete request returns the same 404 MEMBERSHIP_REQUEST_NOT_FOUND, never owner/reviewer IDs. Current blocked/unverified users remain denied. Queries cannot choose another owner.

Both endpoints declare shared 401/403/422/503 errors; POST additionally has 409 and both have 404. SQL errors return sanitized 503 DATABASE_UNAVAILABLE, matching server request ID and no connection/SQL details. These routes implement no approval, rejection, revocation, reinstatement, contributor or moderator administration.

### Exact dependency handoff for Naman

Import `require_active_membership` from `app.modules.identity.membership` and use `membership: Membership = Depends(require_active_membership)` in synchronous handlers whose path contains `{institution_id}`. It resolves the configured institution, verified subject and active profile through existing dependencies and shared Session, then SELECTs the current user/institution membership with status active. It returns the Membership ORM row; missing, pending, rejected, revoked and wrong-user/institution state do not authorize. Removed/revoked membership denies the next request with the same token. This is the approved student-access prerequisite, not contributor/moderator/admin authority.

The dependency never commits and shares the request's transaction. Callers own commit/rollback and must recheck/lock relevant membership/account rows in the transaction of sensitive writes; a check at request time is not a guarantee against revocation racing a later write. Existing auth reads autobegin transactions; do not blindly nest session.begin(). Checks are not cached in tokens or across requests. Routes with another path-parameter name need a wrapper explicitly passing the intended institution UUID, never a client role.

Contributor checks remain unavailable. Since checkpoint 4a, assigned-institution moderator and platform-administrator checks are available as documented below. Naman must not use active membership alone for moderation/report administration or privileged worker actions. Content moderation, reports, worker and roadmap ownership remain Naman's; membership administration remains Ankit's identity module. No external teammate message was sent.


## A1 checkpoint 4a administrator and moderator prerequisite handoff

No new HTTP endpoints or response fields are added. Import dependencies from `app.modules.identity.roles`:

- `require_platform_administrator(profile=Depends(get_current_profile), session=Depends(get_session)) -> PlatformAdministrator`: verifies bearer/current email and active account through the existing identity chain, then SELECTs the current application grant joined to active account. Missing grant returns 403 PLATFORM_ADMINISTRATOR_REQUIRED. No token/user-metadata role is authority. This establishes the recorded global administrative authority; it does not create membership or implicitly bypass institution-content membership checks. Later explicitly approved administrator workflows can use it without inventing a student-membership requirement for the platform role.
- `require_institution_moderator(membership=Depends(require_active_membership), session=Depends(get_session)) -> InstitutionModerator`: for a synchronous handler containing `{institution_id}`, requires the configured/provisioned institution, verified active profile and active membership, then SELECTs that exact user's current moderator grant joined to current active membership/account. Missing grant returns 403 INSTITUTION_MODERATOR_REQUIRED. Wrong institution, revoked/pending/rejected membership and blocked account do not authorize. It is not an administrator-or-moderator combined bypass.

Use `grant: PlatformAdministrator = Depends(require_platform_administrator)` or `grant: InstitutionModerator = Depends(require_institution_moderator)` only in workflows with an explicit approved policy. These dependencies never commit; share S0 Session and PublicError/ErrorResponse conventions. Document shared 401/403/503 errors on consuming endpoints, plus moderator-chain 404/422 from UUID/institution validation. Missing current membership still returns ACTIVE_MEMBERSHIP_REQUIRED. Removed roles revoke authority on subsequent requests with the same token. Sensitive writes must recheck/lock account/membership/grant state in their write transaction and commit their audit atomically; request-time SELECT checks alone do not serialize an entire future write.

Bootstrap is only the explicitly invoked operator module in README, with a hidden target access token and matching target UUID. It reuses existing trusted `/auth/v1/user` verification rather than trusting a UUID/profile or user metadata. No Supabase Auth mutation/admin-key integration or signing change was introduced. Its successful transaction records one platform grant and immutable singleton event atomically; role/profile deletion leaves the event. Event deletion/update is rejected by a database trigger; destructive owner DDL/TRUNCATE and migration downgrade are outside that guarantee. PostgreSQL row/transaction locking and unique constraints underpin serialization; see [PostgreSQL advisory locks](https://www.postgresql.org/docs/16/explicit-locking.html#ADVISORY-LOCKS) and [Supabase trusted user retrieval](https://supabase.com/docs/reference/javascript/auth-getuser).

Naman may now reuse current administrator and assigned active-moderator checks alongside verified identity/current membership. Naman retains content moderation, reports, worker and roadmap ownership. No moderation/report workflow was implemented. Pending-request approval/rejection and scoped administrative reads are implemented in 3b below. Revocation/reinstatement APIs, contributor checks/grants, moderator appointment/removal APIs, administrator lifecycle/recovery and publication services remain unavailable. Moderator state is tested only with controlled disposable fixtures until the appointment API is authorized. No external teammate message was sent.


## Checkpoint 3b contract before implementation: pending membership review

The earlier slice implemented scoped listing/detail and approve/reject. The user subsequently explicitly approved revoke/reinstate within 3b; see the remaining-slice contract below. No previous owner/profile/public request contract changes.

All paths below are prefixed by /api/v1 and use current verified active account plus platform-administrator OR active target-institution moderator authority. Platform authority is allowed for this recorded administration policy without creating student membership. Institution validation still requires the configured/provisioned pilot. Moderator authority never crosses institutions.

| Method/path | Input | Success |
| --- | --- | --- |
| GET /institutions/{institution_id}/membership-requests | status=pending (default), active, rejected, revoked or all; limit=20 (1-100); offset=0 (0-10000) | 200 {items,limit,offset,hasMore}, current requests ordered requestedAt then requestId ascending |
| GET /institutions/{institution_id}/membership-requests/{request_id} | UUID path only | 200 current request detail with nullable review |
| POST /institutions/{institution_id}/membership-requests/{request_id}/decision | {decision:approve or reject,reason:optional string or null} | 200 decided current request detail with audit review |

List item fields: requestId, institutionId, userId, status and requestedAt. No email/profile metadata or other-institution records. Detail adds review:null before decision, otherwise {eventId,actorId,decision,reason,decidedAt}; target/institution/request are the surrounding fields. Reason is optional, null/omission records no reason; strings are trimmed then require 1-500 characters, blank/other types fail. Unknown/protected body and list-query fields return shared 422; detail/decision accept no query fields and reject extras. Reviewer identity always comes from verified authentication. No client status, reviewer, role, institution or target override is accepted.

Unauthenticated/invalid tokens: existing 401; unverified/blocked or insufficient current authority: existing safe 403. Self-approval: 403 SELF_APPROVAL_FORBIDDEN, for administrators too. No new rule prohibits an otherwise authorized self-rejection. Invalid UUID/filter/pagination/reason/input: shared 422. Unsupported/missing institution or scoped GET detail: existing institution/request 404. POST decision for absent/foreign/stale/already-decided IDs: 409 MEMBERSHIP_REQUEST_NOT_PENDING, a uniform safe conflict with no state change. Database/audit failures: sanitized 503 DATABASE_UNAVAILABLE. Existing request IDs must be submitted, never a target user ID.

Only a current pending row can be reviewed. Approval changes only membership status to active (student access); rejection changes only to rejected, preserving re-request semantics. A request-key-unique immutable audit records actor, target, institution, request ID, decision, bounded reason and database timestamp. It is independent of replaceable request/profile rows so rejected re-request does not erase history. Concurrent conflicting reviews lock the current membership row, then exactly one wins and the rest conflict; no duplicate audit effect. Reviewer profile and relevant grant/membership authority are locked/rechecked through commit. No public audit-history/role-management endpoint is introduced.

Pagination is bounded offset pagination over current rows, not a stable snapshot. If decisions/re-requests change a filtered queue, refresh from offset zero; hasMore means another matching row at the time of that SELECT. User/institution query overrides are forbidden, not accepted as authority.


### Checkpoint 3b frontend examples and transaction handoff

Use existing bearer authorization. Replace placeholder IDs with the configured pilot and the current request UUID returned/listed by the API; no real pilot is implied:

```http
GET /api/v1/institutions/{institution_id}/membership-requests?status=pending&limit=20&offset=0
GET /api/v1/institutions/{institution_id}/membership-requests/{request_id}
POST /api/v1/institutions/{institution_id}/membership-requests/{request_id}/decision
Content-Type: application/json

{"decision":"approve","reason":"Confirmed pilot student"}
```

Reject example: `{"decision":"reject","reason":"Institution eligibility could not be confirmed"}`. Omitted/null reason is allowed; present strings must trim to 1-500 characters. A decision response is 200 with {requestId,institutionId,userId,status,requestedAt,review:{eventId,actorId,decision,reason,decidedAt}}. GET detail before review has review:null. List returns only request fields plus target userId inside items; owner GET still excludes userId/reviewer/reason fields and remains owner-only. Never send actor/reviewer IDs, statuses or roles. Render review reasons as text.

On 200, refresh queue/status. On 409 MEMBERSHIP_REQUEST_NOT_PENDING, discard stale review state and refresh the list/detail; do not retry with a target UUID or change another request. On 401/403, handle login/current permission denial. On 422, show field validation without losing input; 503 leaves the decision uncommitted for a fresh retry. Refresh offset pagination from zero after a review/re-request because current queues are not snapshot stable. Unknown detail/decision query fields and protected body fields are rejected, not interpreted as authority.

`app.modules.identity.membership_admin.require_membership_reviewer` implements the approved administrator OR assigned active-moderator policy for institution_id, returning verified current Profile; it never commits. It reuses 4a role checks and 3 active membership, not token roles. This OR is for membership administration only and must not be reused as a general content-access bypass.

The decision operation owns the existing shared session transaction. It locks the reviewer profile, then the chosen administrator grant or moderator membership/grant, revalidates that locked authority, then locks the target membership and verifies exact current request ID/pending state. The chosen branch is rechecked directly, so a newly appearing different privilege cannot silently bypass the held authority locks. Target status and immutable unique audit flush/commit together. No nested transaction after authentication reads, no implicit dependency commit, no audit commit in a separate session. SQL/audit failures roll back the whole operation and return existing sanitized errors.

Audit identifiers intentionally have no foreign key to replaceable current requests/profile/institution rows: request resubmission or later cleanup cannot erase decision history, and audit inserts do not create a profile/membership lock inversion with the existing request-submission path. The service derives and validates all audit IDs. Normal audit UPDATE/DELETE are rejected; database owners can still deliberately destroy data/schema. No historical-audit browsing API is added. The event timestamp is PostgreSQL transaction time, not a claimed measured commit wall clock.

Naman retains content moderation, reports, worker and roadmap ownership; none of those modules is implemented here. Existing role/membership interfaces remain available. Only Ankit's membership review module gains this scoped OR policy and audit. Contributor/moderator appointment, recovery and publication workflows remain unavailable; no external teammate message was sent.


## Checkpoint 3b remaining slice: approved revoke/reinstate contract

The user confirms revoke/reinstate remains in 3b; no scope clarification remains. Only these remaining transitions are authorized; preserve completed listing/detail/approve/reject. Contributor grants are not implemented. Later contributor work must invalidate institution grants on revocation and never reactivate dormant grants on reinstatement; no contributor workflow/table is created here.

| Method/path (prefix /api/v1) | Input | Success |
| --- | --- | --- |
| GET /institutions/{institution_id}/memberships/{user_id} | UUID paths, no query fields | 200 {institutionId,userId,requestId,status,version} |
| POST /institutions/{institution_id}/memberships/{user_id}/revoke | {reason,expectedVersion} | 200 membership state plus transition |
| POST /institutions/{institution_id}/memberships/{user_id}/reinstate | {reason,expectedVersion} | 200 membership state plus transition |

Actor is the verified current profile, never body input. Target user_id is the authorised institution-scoped path target. Unknown/protected body/query fields return shared 422. Reason is required, strict string, trimmed to 1-500 characters; null/empty/whitespace/other types fail. expectedVersion is a required nonnegative strict integer read from GET. A new internal transition_version starts at zero; only successful revoke/reinstate increments it. No prior response shape changes. This precondition prevents stale requests/ABA cycles and duplicate effects; refresh GET after conflicts rather than retrying an obsolete operation.

Current active platform administrator or assigned active institution moderator may administer under existing policy. Both self-actions are 403 SELF_MEMBERSHIP_ACTION_FORBIDDEN. A target with any current platform-administrator grant is 403 PLATFORM_ADMINISTRATOR_TARGET_FORBIDDEN, outside these endpoints. A target with a current moderator grant in this institution requires current platform-administrator authority (existing 403 PLATFORM_ADMINISTRATOR_REQUIRED otherwise). Roles in other institutions are untouched. An already removed moderator grant is not restored or treated as current authority from historical metadata.

Revoke requires active -> revoked. Reinstate requires revoked -> active student only; it cannot create membership or approve pending/rejected requests. Missing scoped membership is 404 MEMBERSHIP_NOT_FOUND. Stale expectedVersion is 409 MEMBERSHIP_VERSION_CONFLICT; invalid/repeated same-state transition is 409 MEMBERSHIP_TRANSITION_CONFLICT. Existing 401/403/404/422/503 authentication/institution/database errors remain. For a repeat using the old version, version conflict takes precedence. No user/profile/experience/progress deletion occurs.

Transition response adds {transition:{eventId,actorId,operation,reason,occurredAt,moderatorGrantInvalidated}} to membership state. Atomically remove any current institution moderator grant on revoke; reinstate also clears any stale grant on an already revoked fixture/legacy row, guaranteeing student-only access. Audit retains the invalidated moderator grant's timestamp, along with target/institution/request, before/after status, new version, actor and reason. A separate immutable transition ledger preserves prior request-review audit and permits repeated explicitly authorised cycles; unique institution/user/version prevents duplicate effects. No audit-history endpoint.

Lock actor and target profile rows in UUID order before reusing the locked reviewer-authority check. Lock target membership and check version/state; target admin/moderator privileges are checked under locks. This order coordinates with existing request submission/review and standalone bootstrap; later appointment/role changes must lock target profile/membership and recheck current state in their audited transaction. Only one valid transition can consume a given version; opposite concurrent operations with that same version cannot silently both succeed. State, removed grant and audit commit/roll back together using the existing session owner convention.


Frontend transition flow (synthetic UUID placeholders; use the configured pilot and real authorized target):

```http
GET /api/v1/institutions/{institutionId}/memberships/{userId}
POST /api/v1/institutions/{institutionId}/memberships/{userId}/revoke
Authorization: Bearer <access-token>
Content-Type: application/json
{"reason":"Eligibility suspended after review","expectedVersion":0}
```

The successful response has status revoked, version 1 and a transition audit summary.
POST the same base path /reinstate with {"reason":"Eligibility confirmed again","expectedVersion":1}.
Refresh state after 409; do not automatically repeat an obsolete intended transition.

Naman handoff: existing require_active_membership and require_institution_moderator interfaces are unchanged and consult current database state on subsequent requests, even with the same token. require_platform_administrator remains administrative authority, not protected-content membership bypass. Reinstatement restores student membership only. No contributor dependency, appointment, contributor application or content moderation workflow is added. Later privilege writers must use target-profile/member locking, current active membership and their own explicit immutable grant audit; no dormant elevated grant may be restored by reinstatement.
