# A1 task list

Owner: Ankit. Policies approved. Checkpoints 1 and 2 are complete. Checkpoint 3 request/status/access-check portion is complete; checkpoint 4a is complete. Checkpoint 3b revoke/reinstate is explicitly selected; prior review implementation is preserved.

| Checkpoint | Scope | Acceptance | Status |
| --- | --- | --- | --- |
| 1 | Verified identity, minimal profile/account state, GET /me | Signature/claims/email, sanitized failures, PostgreSQL migrations/concurrency/blocked state; S0 regression checks | Complete: 61 full-suite tests passed |
| 2 | PATCH /me | Approved displayName-only contract, no privilege mutation, atomic validation and real PostgreSQL rollback | Complete: 70 full-suite tests passed in 60.86 seconds |
| 3 | Membership requests, owner status and current member checks | Pending uniqueness, rejected re-request, revoked refusal, isolation/revocation/concurrency and rollback | Complete: 83 full-suite tests passed in 83.71 seconds |
| 4a | Administrator bootstrap and current role prerequisites | Verified target, blocked/role/membership denial, durable atomic singleton, concurrent refusal and rollback | Complete: 99 full-suite tests passed in 123.09 seconds |
| 3b | Membership administration | Scoped list/detail/review/revoke/reinstate, no self-actions, student-only restoration, concurrency and atomic audit | Complete: review plus revoke/reinstate; 124 full-suite tests passed in 223.93 seconds |
| 4b | Contributor/moderator administration | Scoped authorities, no self-grant, current-state denial, atomic audit | Not started |
| 5 | Remaining A1 handoff | Original bootstrap moved to 4a; finish only after remaining workflows | Not started |

Write meaningful focused tests before each slice; use real disposable PostgreSQL and retain S0 regression checks. Label synthetic provider responses and signed fixtures explicitly. No skipped assertions, SQLite substitution, shared bootstrap execution, push or deployment. Naman retains content moderation, reports, worker and roadmap ownership.

The user explicitly confirms revoke/reinstate remains in 3b and approves its target restrictions, reason validation, student-only restoration and atomic audit. Original checkpoint 5 bootstrap is brought forward to bounded 4a; contributor/moderator administration and final A1 handoff remain unstarted.
