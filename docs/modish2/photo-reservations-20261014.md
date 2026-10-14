# Photo analysis reservations — 14 October 2026

This is a source and acceptance contract, not a deployment record. The release
owner must record the packaged revision, migration result and isolated PostgreSQL
concurrency report before claiming the change is live.

## Request and quota contract

Both `POST /onboarding/photo` and `POST /style-profile/analyze` accept the existing
multipart upload. An optional `Idempotency-Key` header identifies one operation
for that owner. Keys are 1–128 ASCII letters, digits, `.`, `_`, `:` or `-`. With
no header the server generates a UUID, preserving existing clients. Clients that
need safe retries after a lost response should retain one key for that photo
until the operation finishes; a newly selected photo receives a new key.

Migration `20261013_0025` adds `photo_analysis_reservations`, with unique
owner/request-key and owner/generation constraints. It stores a payload digest,
operation kind, original quota month, status and completed response, never image
bytes. Existing quota consumption is preserved. No quota is reset by migration.

A short worker-thread session authenticates the owner, locks the owner row,
refreshes the calendar-month quota and commits one debit plus its reservation.
All other monthly quota refreshes use that same owner lock, so login/billing
cannot reset a concurrent reservation at the month boundary. The provider runs
after this session closes. Another short session stores the result and response
atomically. No ORM instance or session crosses the provider await.

- A completed matching request replays its stored response without another
  provider call or quota debit, even if another analysis later changed the profile.
- A matching pending request receives HTTP 409 and `Retry-After: 2`. The client
  should retain its selected photo/key, show that analysis is running and allow
  a bounded retry of that same request. Polling must not generate new keys.
- Reusing a key with another payload or operation kind returns 409.
- Failed/cancelled keys are terminal and do not invoke the provider again. A
  deliberate new attempt uses a new key after the prior slot has been released.
- Provider failure returns a generic 502, without upstream payloads or URLs.
  Timeout returns 504. The provider coroutine is cancelled and awaited before
  a known stopped operation is released. Cancellation cleanup and database
  commits are shielded from the enclosing AnyIO cancellation scope. Repeated
  native `Task.cancel()` calls are deferred until the short worker finishes;
  cancelling admission then releases its committed debit without starting a
  provider, while cancelling completion preserves the committed replay.
- Refund is idempotent and applies only when the active quota row still belongs
  to that reservation's original month. It never credits a new month. A successful
  but superseded result stays charged and cannot overwrite a newer generation.

`POST /visual-analysis` uses a deep-copied analysis dictionary read in a short
session. Its provider receives no live ORM object or checked-out connection.
This endpoint does not introduce a new quota.

## Timeout, unknown outcome and safe recovery

The photo provider coroutine has a 180-second timeout. A reservation's deadline
is 300 seconds, allowing normal completion/persistence overhead. These limits
do not promise that a remote provider has stopped billing or processing; the
provider's existing transport retry policy is unchanged.

A worker crash, lost completion or failed completion commit can leave an outcome
unknown. On observing an overdue pending request, mark it `expired`, retain its
debit and return 409. Expiry alone **never grants another slot**. A late provider
result cannot change an expired/failed/cancelled/superseded record, and an older
generation cannot replace a newer analysis. A completion committed before a lost
HTTP response remains replayable with the original key.

Do not recover by deleting reservations, decrementing an arbitrary current
counter, resetting all quotas, replaying the provider or stamping an Alembic
revision. First stop/identify the worker that owns the uncertain operation and
establish that its provider work has stopped. Check whether the response already
committed; completed records should be replayed. Only for a verified stopped,
uncompleted operation may an operator invoke the idempotent `release_photo`
helper with that exact reservation ID and owner. It locks the owner, transitions
only pending/expired records, and refunds only the matching original period.
If the outcome cannot be established, leave the charge retained; the regular
calendar-month reset permits the next month's quota without rewriting history.

Application rollback must preserve reservation history and consumption. Do not
run the migration downgrade against a live reservation ledger: it drops the
ledger and loses replay/ownership evidence. An old API image does not enforce
this reservation contract, even if it can read the additive schema. Prefer a
forward fix; a rollback needs an explicit plan to disable photo entry points or
otherwise preserve admission while returning the application.

## Acceptance and evidence limits

`backend/tests/test_photo_reservations.py` covers both endpoints, connection
check-in before a controlled provider, independent DB access while it waits,
replay/in-flight conflicts, failure/cancellation, timeout, cross-operation keys,
backwards compatibility, month-boundary refunds, expired late results, newer
generation protection and detached visual inputs. SQLite tests establish those
lifecycle/HTTP contracts, not PostgreSQL lock correctness.

The final targeted local run passed 17 tests (15 photo lifecycle/HTTP tests and
2 registration integrity tests) in 12.971 seconds. It includes repeated native
cancellation during admission, refund and completion. The Windows sandbox
could not create asyncio's internal socket pair; the successful local run used
the approved unsandboxed test process with temporary SQLite and fake providers.

`backend/tests/test_registration_conflict.py` exercises a real failed unique
email insert after a simulated absent precheck and an unrelated primary-key
failure. Only the email uniqueness conflict maps to 409; unrelated integrity
errors remain errors after rollback.

The isolated PostgreSQL gate is `scripts/check_photo_postgres.py`. After restoring
the approved test database and applying migration 0025, run it in the candidate
image with a test-only `DATABASE_URL` already in the environment:

```text
python scripts/check_photo_postgres.py --isolated-write-test --expected-host TEST_DB_HOST --expected-database TEST_DB_NAME --report /tmp/photo-postgres-report.json
```

The name must explicitly identify a test/restore/clone DB, and `modish`/`postgres`
are rejected. The script verifies the exact supplied host/database and current
database, uses only fake providers, and reports counts/status/timing without
credentials or personal data. It creates UUID test owners and removes only their
rows, leaving the restored catalogue and existing owners untouched.

For each photo endpoint, two requests meet at a real thread barrier immediately
before admission. With one remaining slot and different keys, require one 200,
one 403 and exactly one provider invocation. With identical keys, require one
200, one 409, followed by an identical 200 replay and still one provider call.
The loser must finish while the provider is blocked; the connection pool must
show zero checked-out connections, and an independent `SELECT 1` must succeed.
Finally two registrations meet at `before_insert`: require one complete account
with a refresh token, one HTTP 409 and one account row. A successful JSON report
is produced only after cleanup commits.

The PostgreSQL gate has been prepared here; its execution and production
deployment remain the release owner's separate checks. The long feed/intent
owner-lock defect is not changed by this package.
