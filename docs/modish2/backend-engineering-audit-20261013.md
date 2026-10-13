# Backend engineering audit — 13 October 2026

The review found concrete lifecycle and admission defects, rather than a reason
to replace the existing architecture. Four bounded fixes are now in the local
source: expiring rate-limit state, resilient legacy image identities, a shared
public image URL gate, and deterministic intent offer ordering. They are not a
claim that a new backend package has been deployed. The release owner controls
immutable packages and isolated server validation.

## Scope and evidence limits

- Reviewed `app/db.py`, `config.py`, `main.py`, request dependencies, API commit
  boundaries, catalogue retrieval, Fit/Taste/context separation, scoring caches,
  outfit composition, event history queries, ORM metadata and Alembic indexes.
- No credentials, `.env` contents, production rows, network exports or private
  merchant APIs were read for this audit. No SSH or production mutations.
- Index findings below describe repository metadata/migrations. The deployed
  database may have additional indexes; its `pg_indexes`, cardinalities and
  query plans have not been collected by this reviewer.
- Local fixtures use SQLite. They prove the relevant URL/order behaviour but
  cannot establish PostgreSQL row-lock wait times or concurrency correctness.
- Existing v24 performance artifacts cover one controlled profile on a real
  isolated catalogue, three completed runs, and actual-time stock gates. They
  are not production request latency or a representative load test.

## Fixes completed in local source

| Defect | Fix and preserved contract | Validation |
| --- | --- | --- |
| Per-worker rate limiter retained every first-seen IP forever | `app/middleware.py:43`: per-instance `OrderedDict` with monotonic 60-second idle expiration, accepted timestamp deque, maximum 4096 active client keys. At capacity a new untracked address receives the existing 429 response; an active bucket is never evicted to reset its quota. | `tests/test_rate_limit_lifecycle.py`: 11 tests, including 1000 expired one-time addresses, 1000 rotating denied addresses, rolling-window boundary, exemptions, original client-address trust policy and independent app instances. |
| An invalid image URL on a previously viewed/hidden product could throw `ValueError` before admission and abort feed retrieval | `app/services/product_identity.py:7`: only `urlsplit`'s `ValueError` is handled, returning the trimmed invalid value as opaque identity. Valid URL canonicalization remains unchanged. | Hidden legacy invalid row tested on full ORM and thin mandatory-role paths; valid CDN resize aliases remain excluded. |
| Intent hard admission accepted any nonempty image string, including malformed URLs and private addresses | `app/services/catalog/catalog_quality.py:29`: reuse existing `_public_web_url`, which is already used by activation and catalogue quarantine. No fallback for unknown size/stock. | `tests/test_product_identity_resilience.py`: public image remains eligible; malformed IPv6, localhost/private IP, protocol-relative, data and JavaScript values cannot reach intent results. |
| SQL row/insertion order picked an arbitrary alias before scoring | `shopping_intent_service.py:135` orders the admitted scan by `Product.id`; `intent_outfits.py:101` orders thin role IDs and `:123` orders each hydration batch. | `tests/test_intent_identity_order.py`: reversed insertion gives identical canonical item/outfit selection and outfit UUID; rejected lower ID never reserves identity; exclusion still suppresses aliases. |

The rate limiter remains per worker, as it was before. Its finite-capacity policy
adds deliberate backpressure once 4096 distinct clients have been active within
the last minute. Existing clients can use their remaining quota. Health/docs and
the original login/register exemptions are preserved. This does not introduce
a distributed limiter or alter trusted forwarding-header policy.

Intent alias policy is explicitly **stable first eligible ID**, not highest
MIE-score offer within a model group. Hard admission runs first. For outfit
composition the existing role priority still applies, then IDs within that role
are visited in ascending order. The existing overlapping-key exclusion algorithm
is preserved; this change does not define or materialize a transitive identity
graph. A future best-score representative policy needs a separate memory and
identity contract.

The defect was reproduced before the ordering fix with two admissible offers
sharing an image: score(low)=0.1 and score(high)=0.9. Inserting low then high
ranked/returned only low; inserting high then low ranked/returned only high.
The higher score is deliberately not claimed as the policy after this bounded
stability fix.

Relevant successful local suites after the changes:

- 11 rate-limit lifecycle tests, 0.246 seconds.
- 54 identity/admission, exact-fit, repeat, shopping-intent and WOW tests,
  2.074 seconds after adding the test fixture directory to `PYTHONPATH`.
- 40 intent ordering, existing intent and semantic performance tests,
  1.582 seconds after query ordering changes.

The earlier identity-only run passed 17 tests before adding the shared image
gate. An intermediate broader invocation failed solely because
`test_exact_eligibility` could not import `fit_fixtures`; the corrected invocation
used `PYTHONPATH=.;tests` and passed all 54. These counts overlap; they must not be
summed as independent full-package coverage. The release owner must run the
final packaged suite.

## Remaining defects and bounded follow-up contracts

### P1: photo quota is a read-then-increment race

`app/business.py:65` loads `UserLimits` without a row lock/reservation, and
`:91` checks `photo_analysis_used` before external work. Both
`api/onboarding.py:182–196` and `api/profile.py:253–272` await analysis and later
increment the previously loaded value. The controlled interleaving is:

1. Requests A and B both read `used=0` and pass the monthly limit of one.
2. Both invoke the external analysis provider.
3. Both assign their ORM copy from 0 to 1 and commit; the final counter can be 1
   while two provider operations ran.

This is a concrete lost-update/admission defect from the code, not measured
production billing. It also applies to other read-then-increment quotas if their
operations can overlap. Holding a database row lock across the external await
would replace the race with a long-lived resource lock and is not an acceptable
fix.

Bounded fix: introduce a durable analysis reservation keyed by owner and request
ID. In one short transaction initialize/refresh the quota under a user/limit
lock, atomically reserve one slot, and commit. Perform external work with no DB
transaction alive. In a second short transaction store the result and complete
the reservation. Failed/cancelled reservations must be released idempotently
for their original quota period; a replay cannot consume another slot or refund
a different request. Keep retry/result ownership checks explicit.

Acceptance: two concurrent PostgreSQL requests with one remaining slot invoke
the fake provider once; duplicated request ID replays one result; provider
failure/cancellation releases exactly its reservation; a month transition cannot
refund a new period. Verify both photo API entry points, not only the helper.

### P1: external awaits retain a checked-out DB transaction

`main.py:85–91` reads the authenticated user/profile and then awaits
`generate_style_visual`. `api/onboarding.py:180–188` and
`api/profile.py:251–259` similarly perform ORM reads before the provider await.
`get_db` closes the session only after the handler returns. Thus a pending
external request retains its transaction/connection for the duration of that
await. Synchronous ORM calls also run inside these async handlers.

This connection lifetime follows directly from the request flow. Actual pool
capacity and traffic were not measured. `db.py:9` enables pre-ping but leaves
pool/time-out settings implicit; increasing pool capacity would mask the
lifetime problem rather than remove it.

Bounded fix: use synchronous helpers with their own short sessions to return
immutable analysis DTOs or reservation IDs, then close those sessions before
awaiting the provider. Use another short helper/session to persist completion.
Do not pass a live `Session` or lazy ORM object across worker threads or the
external await. `/visual-analysis` only needs a detached copy of its existing
analysis dictionary and can release its read transaction without a quota
schema change. Photo endpoints should share the reservation contract above.

Acceptance: a controlled fake provider blocks on an async event; while blocked,
connection check-in has already occurred and an independent DB request succeeds.
Provider failure closes sessions and preserves the expected quota/result state.

### P1: whole-catalogue work runs inside the owner's write lock

`recommendation_engine.py:314` takes `SELECT users ... FOR UPDATE` before
retrieval/ranking. The lock remains until `api/products.py:112` commits after
impression logging and serialization. `product_event_service.py:31` requires
the same user lock for likes/skips. `create_intent` takes it at
`shopping_intent_service.py:50`, and `api/shopping_intents.py:14–16` computes the
result before committing. Slow catalogue work therefore queues another device
or an overlapping feedback request for that owner behind the scan.

This is an intentional duplicate/exposure safeguard, not a redundant lock to
delete. The v24 controlled catalogue artifact
`intent-v24-current-activation-performance.json` recorded first-feed median
10.552908 seconds and WOW completion median 19.309169 seconds. It demonstrates
that the protected work can be substantial under that fixture; it does not
prove current optimized latency or production lock waits.

Bounded fix: separate expensive candidate ranking from the short owner mutation
transaction. Read a detached snapshot containing Fit/Taste/configuration version
and current exclusions, compute candidates without the owner write lock, then
lock the owner to verify snapshot versions, recheck current hard admission and
allocate exposures/cache state. On conflict, discard the proposal and recompute
at most once outside the lock; otherwise return a controlled retry state. Keep
database uniqueness as the final allocation guard. Do not put a shared mutable
ORM session or cached eligibility decision into the precompute phase.

Acceptance on isolated PostgreSQL: overlapping feed requests issue no repeated
identities/exposure positions; a like/skip is not held behind catalogue scoring;
Fit/Taste changes between snapshot and allocation cause a bounded retry and
never leak an old-size product; cancelled ranking writes no exposure. Measure
lock wait and lock hold separately from total request latency.

### P2: event indexes do not cover the current history/popularity predicates

`models.py:373–382` and migration `20260505_0004_catalog_taste_events.py:77–79`
define separate event indexes on owner/product/outfit, but no composite/time
index for the new history queries. `personalization_scoring.py:45–56` groups
last-90-day owner actions by product/day;
`behavior_profile.py:83–90` scans owner/date/type actions ordered newest first;
`activation_service.py:179–182` aggregates global recent like/save popularity.
Product retrieval additionally uses gender, active/fresh stock and price, for
which the repository currently has no corresponding compound/partial index.

Collect deployed `pg_indexes` and `EXPLAIN (ANALYZE, BUFFERS)` only on the isolated
real catalogue before choosing a migration. Candidate index shapes are
owner/date/product for owner history and a partial event date/product index for
positive popularity, plus a product index matching the observed selective
retrieval predicates. Compare actual cardinality, read cost, write cost and
ordered output hashes. Do not create speculative indexes or limit candidates
to the most recently updated 1000 products.

### P2: registration's unique-email conflict is not mapped to 409

`api/auth.py:74–94` first checks email absence and then flushes a new user.
Concurrent registrations can both pass the check; the unique database index
then rejects one flush with an uncaught `IntegrityError`. The existing 409 path
only covers a row already present at the initial read. This concurrency case
was identified by code inspection, not exercised on production.

Bounded fix: rollback and map only the unique-email conflict to the existing
409 response; rethrow unrelated integrity failures. Preserve atomic ownership
of the newly created account and refresh session. PostgreSQL concurrency test:
two same-email registrations produce one account and one successful response,
the other 409, without a partially initialized second account.

### P2: bounded scoring does not imply bounded retrieval/history memory

`AffinityScorer` has a request-local 512-entry cache and correct eviction;
`intent_outfits` holds at most 18 top + 6 cheap finalists per role, a 2048-entry
local pair cache and 200 final combinations. These are useful explicit bounds.
However `intent_outfits.py:98–103` stores every eligible role ID before hydration,
and retrieval identity sets grow with the number of admitted catalogue rows.
`behavior_profile.aggregate_behavior` materializes every distinct product/day
of the 90-day owner history before taking up to 200 category price samples.

Measure peak memory with synthetic wide catalogues and long action histories.
A next optimization can stream the role scan directly into fixed-size hydration
batches or use an indexed persisted role projection; it must preserve stable ID
ordering, late eligible winners, exclusion identities and exact-fit semantics.
For behavior history, aggregate product/day actions in SQL or a maintained
snapshot with tested replay semantics, rather than truncating recent raw events
and changing the denominator. These are growth risks, not evidence of a current
out-of-memory incident.

## Architecture decisions to retain

- `profile_repository` owns shared profile persistence, independent of API and
  recommendation orchestration. Intent/session constraints do not write User
  Twin merely because the current request says cheaper/different occasion.
- Fit and commercial admission precede Taste/MIE ranking. Unknown national size
  systems or size-level stock cannot be compensated by a high affinity score.
  No default M, hidden numeric conversion or stale-stock fallback is justified.
- Catalogue identity/exclusion is shared across activation, feed and intent.
  Invalid hidden legacy rows must be handled without admitting current invalid
  merchandise; the URL changes above implement both halves of that contract.
- Thin candidate scanning is a negative prefilter only. Full current admission
  still checks stock, size unit/variant evidence, role, source, budget, gender
  and context. Hydration pagination must be applied once, and live session
  mutations must retain the full ORM fallback. See the separate candidate-scan
  review for the tested pagination correction.
- Request-local score caches avoid cross-user leakage and invalidation concerns.
  Store the finalist's common score with heap entries to avoid semantic parsing
  again after cache eviction.
- Outfit composition requires a complete supported slot set and validates both
  exact available size evidence and pair compatibility. Returning fewer honest
  outfits is acceptable; partial/unknown-size outfits are not a substitute.

## Recommended implementation sequence

1. Package and verify the local lifecycle/image/order fixes in a new immutable
   candidate; preserve existing prior archives. Run the final package suite and
   isolated real-catalogue ordered-output/performance checks.
2. Shorten the external-analysis transaction lifetime and implement one shared
   durable quota reservation flow for both photo endpoints.
3. Split owner allocation from expensive feed/intent precompute, with isolated
   PostgreSQL concurrency/lock measurements as release acceptance.
4. Select indexes from isolated real query plans, then address registration's
   duplicate-email race and measured retrieval/history memory growth.

No framework replacement, distributed cache, new store integrations or broad
module rewrite is required to implement these bounded changes. The remaining
P1 contracts require concurrency tests and deliberate transaction ownership;
they must not be marked resolved because the current local unit suite passes.
