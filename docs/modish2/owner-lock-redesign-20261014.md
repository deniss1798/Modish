# Short owner transactions for feed and Shopping Intent

Date: 14 October 2026. Status: implementation proposal; the P1 defect is **not
resolved** by this document. This review changes no backend, Flutter, release
package, database or deployment configuration. It follows the remaining
whole-catalogue owner-lock finding in
[backend-engineering-audit-20261013.md](backend-engineering-audit-20261013.md).

The objective is to let feedback and profile changes complete while expensive
catalogue ranking runs. Preserve exact-fit admission, stable identity handling,
current feedback semantics, Intent ownership/version checks and honest exposure
metrics. Do not remove the existing locks before their publication safeguards
have a replacement.

## Current transaction boundaries

| Path | Current behaviour and code anchor |
| --- | --- |
| Feed | `recommendation_engine.generate_feed` (`backend/app/services/recommendation_engine.py:299`) locks `User.id` at line 314 before candidate retrieval. Activation users enter `activation_service.personalized_feed`; legacy users also write recommendation caches. `api.products._feed_scored_items` computes results, logs delivery impressions, serializes products and finally commits (`backend/app/api/products.py:22`). The owner lock spans that work. |
| New Intent | `shopping_intent_service.create_intent` (`backend/app/services/shopping_intent_service.py:48`) locks the owner before request-id lookup, parsing, row creation and metrics. `api.shopping_intents._execute` calls `intent_response(..., remember=True)` before committing (`backend/app/api/shopping_intents.py:12`). Thus the owner lock also spans `item_results` or outfit composition. |
| Intent edits | `refine_intent`, `_selected_outfit`, `feedback` and `close_intent` obtain a `ShoppingIntent` row lock through `get_intent(..., lock=True)` or a direct query. Replacement ranks in `replace_outfit_item`, and `_execute` can rank again when remembering the new version. A global feedback rejection can call `record_product_event` while already holding the Intent row. These paths must be enrolled in a single future lock order. |
| Intent GET | `intent_response` without generation revalidates the issued `product_ids`/outfit item snapshot. It deliberately does not rescore the catalogue. Keep this behaviour. |
| Product feedback | `product_event_service.record_product_event` (`backend/app/services/product_event_service.py:28`) obtains the owner lock at line 31 and updates event history, cooldown/state, legacy Taste, User Twin and activation exposure within the caller's transaction. |
| Other protected work | `activation_service.swipe_deck`, `complete_wow`, and `outfit_engine_v2.generate_outfits_v2` also perform catalogue work under owner locks. They remain separate follow-up paths; converting one feed/Intent path does not fix them all. |

The request authentication Session can already have loaded rows before a lock
wait. Acquiring `SELECT User.id ... FOR UPDATE` does not refresh every previously
loaded ORM object. Publication must use a new Session or explicitly reload
relevant rows with `populate_existing=True`.

## Mutable input inventory

The proposal must identify both admission inputs and ranking inputs. A changed
size is a hard publication conflict; a changed Taste profile makes the proposal
obsolete even if the old finalists remain individually eligible.

| Input | Required detached values | Current readers |
| --- | --- | --- |
| Owner and Fit | Owner id; profile absence/presence; gender; height confirmation; `clothing_size`, `size_profile_json`, `body_measurements_json`, size-system constraints; budget; interests/scenarios; palette/avoid colours; silhouette/fit recommendations and exclusions. Include owner fallback measurements used when an absent profile is initialized. | `require_warm_feed`, `requested_size_for_product`, `product_ok`, `evaluate_eligibility`, `AffinityScorer.bind_admission_context`, legacy `build_feed_context`. |
| Style | `style_target`, selected styles, price segment, relevant analysis/profile JSON, onboarding completion/step and analysis recommendations used for scoring/reasons. | `profile_repository.load_preferences`, `activation_service.candidate_pool`/`reasons_for`, `build_feed_context`, `AffinityScorer`. |
| Taste | Legacy lists and weights; the relevant persisted `UserTasteFeature` fields; read-derived decay at a fixed `as_of`; `behavior_snapshot`; owner visible-action history and `seen_counts`. | `load_user_taste_feature_map`, `load_behavior_profile`, `load_ranking_seen_counts`, `AffinityScorer`, legacy popularity/saved-category readers. |
| Activation | Presence/completion, segment, shoe size, Style Swipe reactions, and actual first-20 exposure count. Treat `issued_feed_ids` separately as attribution/allocation state. | `require_warm_feed`, `candidate_pool`, `personalized_feed`, `record_exposure_event`. |
| Exclusions | Request `exclude_ids`; current `UserProductState.hidden_until`; recent owner like/dislike/skip/save IDs; rejected/unavailable swipe IDs; identity keys derived from those products. | `_feed_scored_items`, `candidate_pool`, `expand_product_exclusions`, `product_identity_keys`. |
| Request context | Normalized source/scenario, all explicit filters, requested limit, context and algorithm/config version. These immutable request values must be bound to the proposal fingerprint. | `CatalogFilters`, `context_for`, feed/Intent orchestration. |
| Intent | Owner/id/request id, version, active/expiry, text/constraints, result version, excluded IDs, selected results and replacement's locked items. | `get_intent`, `refine_intent`, `intent_response`, `_selected_outfit`, `replace_outfit_item`, `feedback`, `close_intent`. |
| Catalogue and policy | Current finalists' full admission/identity/score evidence: role/category/title, gender, price/currency, links/photo, source, stock flags/observation time, per-size evidence/raw attributes and relevant semantic facts. Feed also reads active `SourceRule` values. | `product_ok`, `intent_product_problem`, `outfit_problem`, `product_passes_source_rules`, common identity and size/stock helpers. |

Current Intent admission does not call `load_rules_by_source_id`; feed admission
does. This transaction redesign must preserve each path's current gates. Adding
new Intent source-rule policy is a separate explicit change with its own tests,
not an undocumented consequence of moving computation.

Global popularity is also mutable. Read it once outside the owner lock and record
its cutoff/basis in the proposal. Do not invalidate every owner's proposal for
every other user's like. Exact global-catalogue/ranking snapshot consistency is
not an existing API promise; current hard admission at publication is required.
If a stronger catalogue generation guarantee is chosen later, all catalogue and
source-policy writers must participate in that generation protocol.

## First slice: writer enrollment and revision coverage

Add a dedicated monotonic owner `recommendation_revision` in an additive
migration. Increment it in SQL in the same transaction as any accepted mutation
that changes personal admission, ranking or reasons. Do not reuse
`User.updated_at`: writes can bypass it and a timestamp is not a complete
monotonic state token. Keep a separate `allocation_revision` when publication
state itself needs conflict detection; a delivery is not a Taste signal.

`TasteProfile.profile_version` is useful but incomplete.
`user_twin_service.apply_event_to_user_twin` increments it only when feature
rows are touched (`backend/app/services/user_twin_service.py:261`, increment at
line 305). Some events change cooldowns/history without touching those features.
Legacy onboarding can modify liked styles directly. `ActivationSession.version`
is initialized in the model and is not a complete mutation revision either.

Enroll the following writers before removing an owner lock:

| Writer | Existing owner-lock coverage | Required enrollment |
| --- | --- | --- |
| `api.fit_profile.fit_profile_patch` | **Already acquires `lock_user`** (`backend/app/api/fit_profile.py:82`, lock at line 89). | Bump the shared revision with Fit/size/body/budget/category changes; reload after waiting. |
| `api.fit_profile.fit_profile_me` when Fit is absent | GET can create and commit Fit without an owner lock. | Make initialization explicit, serialized and revisioned; keep creation out of read-only ranking. |
| `api.profile.users_me_preferences_patch`, `style_profile_patch`, `patch_style_target` | No common owner lock at these entry points. | Lock/reload and revision accepted changes, including summary-derived recommendation inputs. |
| `api.onboarding.onboarding_step1`, `onboarding_step3`, `onboarding_photo_confirm` | Legacy entry points do not acquire `lock_user`; their `onboarding_service.apply_step1`, `apply_step3`, `apply_photo_confirm` helpers mutate Style/Fit and sometimes Taste. | Enroll both entry points and direct service use. Avoid counting the same logical transaction twice through nested helpers. |
| `activation_service.configure`, `swipe_feedback`, `complete_wow` | Owner lock already present. | Revision changes to preferences, reactions, completion or resulting personal inputs. Deck/issued-id publication belongs to allocation state, not learned Taste. |
| `product_event_service.record_product_event` and `user_twin_service.apply_event_to_user_twin` | Owner lock already present; the latter has an SQL Taste version increment. | One shared personal revision per accepted logical transaction covering history/state/Taste/exposure changes; idempotent no-op does not create fake evidence. Audit direct helper callers too. |
| `photo_analysis_jobs.complete_photo` | Short owner lock already present (`backend/app/services/photo_analysis_jobs.py:130`). | Bump revision only when a completed, non-superseded analysis actually changes profile inputs. Admission/refund alone is not a recommendation-profile change. Preserve its existing quota reservation protocol. |
| `profile_repository.ensure_style_profile`/`ensure_taste_profile`, `onboarding_service._ensure_fit_profile` | Helpers can create rows and flush; not every caller owns the same lock. | Explicit initialization transaction, or require an enrolled mutation context. Ranking must not invoke hidden writes through `load_preferences`. |
| `user_twin_service.get_feature_preference(..., apply_time_decay=True)` | Optional persisted decay is a separate write path. Ranking's `load_user_taste_feature_map` currently decays detached snapshots instead. | Enroll any persisted-decay caller that remains; continue using read-only decay for ranking. |

Revision increments must be atomic SQL expressions, rollback with their mutation,
and preserve successful idempotent replay. Define a service mutation context so
nested event/User Twin/exposure helpers do not produce inconsistent accounting.
Document controlled maintenance/backfill writers as participants too. ORM
`updated_at` hooks alone cannot provide coverage.

Acceptance for this first slice is a writer matrix test: each listed mutation
changes the canonical recommendation fingerprint and revision atomically;
rollback changes neither; replay/no-op creates no new evidence. Keep the current
protected ranking path until this prerequisite is covered.

## Snapshot, computation and publication

1. **Snapshot in a short owned transaction.** Authenticate/verify owner, perform
   required profile initialization under the owner lock, reload small mutable
   personal inputs and record their revision/fingerprint plus `as_of`. Return
   immutable DTOs; close Session/transaction. Do not hold the owner lock while
   aggregating unbounded 90-day history: derive history outside the lock and use
   the final revision check to reject concurrent mutations.
2. **Prepare outside owner/Intent write locks.** Use a distinct read-only Session
   for catalogue scanning/history aggregation. Preserve full candidate coverage,
   stable first-eligible identity order, hard admission before identity, diversity
   rules and bounded scoring/composition heaps. Close cursors/Sessions. Return a
   bounded proposal of finalist IDs, immutable evidence, scores/reasons and input
   fingerprint. No cache/exposure/metric writes or live lazy ORM objects cross
   phases. Pass one ranking `as_of` through decay/history reads; it does not renew
   source stock observations.
3. **Publish in another short transaction.** Reload the bounded selected products
   and policy evidence, acquire owner and then relevant Intent row in a single
   declared lock order, and verify current revisions, warm gate, Intent version,
   active/expiry, ownership, exclusions and hard admission. Recheck stock against
   publication time, not the earlier ranking clock. Rebuild size matches and
   explanations from current evidence. Persist the result, issued IDs/caches and
   delivery analytics atomically, then return detached response data.

No whole-catalogue re-ranking may occur while holding publication's owner lock.
Keep enough bounded ordered finalists to fill a valid page, or discard the
proposal if changed evidence requires actual recalculation. Do not reuse scores
or reasons after a price, role, identity, size or relevant semantic change.

Personal-input/Intent conflict: rollback and recompute at most **once** outside
the lock. A second conflict returns a controlled 409/retry state with no partial
publication. This needs explicit exception mapping rather than feed's broad
generic 500 wrapper. Expected client-version conflict on a user edit should
remain 409; a retry must never silently override a newer Intent.

If selected catalogue rows need to remain stable until commit, use bounded
shared row locks in stable ID order **before** the owner lock, with a short
`lock_timeout` and statement budget. Catalogue import must not queue personal
feedback behind a publication waiting on product locks. Test the chosen lock
order with import and policy writers; do not introduce a global catalogue lock
held across ranking. A product can change after commit/response, so claims remain
source-observed availability, not guaranteed checkout stock.

## Intent is the first computation path to convert

After revision enrollment, convert only **single-item Intent creation** first:

- A short transaction enforces `(user_id, request_id)` uniqueness and same-text
  reuse, persists parsed constraints and an internal generation/pending state.
- Prepare `item_results` outside the owner/Intent write lock using detached
  preferences and bound constraints.
- Short publication checks the generation, personal revision and Intent version
  before remembering `product_ids`/result metadata and emitting generation
  metrics exactly once.
- Define pending/replay explicitly. Concurrent retry must not interpret an
  uncomputed `context_json` as an empty `ready` result. A saved successful result
  replays without a new catalogue scan. Interrupted pending generations need
  expiry/recovery; a lost HTTP response cannot create another published result.
- Preserve GET's issued-snapshot revalidation. It may remove invalid items but
  must not replace valid issued items with newer higher-scoring catalogue rows.

Then convert activation feed; then Intent refine/replacement/outfit composition;
then activation deck/WOW and legacy outfit generation. Establish one lock order
for all participants before mixing old Intent-row-first feedback with new
owner-first publication. These are staged changes, not one broad refactor.

## Delivery reservation is not an actual exposure

Current repeat suppression uses request exclusions, active hidden cooldowns and
the owner's last-30-day like/dislike/skip/save product IDs in
`api.products._feed_scored_items`. `candidate_pool` adds rejected/unavailable
Style Swipe IDs. Common identity keys expand aliases by merchant URL,
source/group/vendor and normalized image; rejected candidates do not reserve
identity ahead of admission.

`activation_service.personalized_feed` stores only the last 200
`issued_feed_ids`. These establish that a subsequent feed event can be attributed
to a delivered card; the candidate pool does **not** exclude this list.
`ProductImpression` logs API delivery/prefetch and likewise is not an exclusion
or proof of sight. Consequently two concurrent feed loads with no intervening
visible event can currently return overlapping cards even with the owner lock.
Do not claim removing/splitting that lock alone fixes concurrent prefetch overlap.

`record_exposure_event` creates first-20 `ActivationExposure` only for a real
supported event on `surface='feed'` and an issued product. Database uniqueness is
on `(user_id, product_id)` and `(user_id, position)`. Count/position and reactions
must remain serialized; prefetch must not increment them. First-feed cap and
80/20 exploration offset use these actual exposures.

If a later product requirement demands disjoint simultaneous prefetch, design a
**separate expiring delivery reservation** with owner/request id, saved response,
generation, identity keys, TTL and release/replay behaviour. Existing overlapping
identity sets are not a persisted transitive model id; a naive unique product-id
constraint cannot suppress all aliases. Pending deliveries must expire so an
abandoned client does not create false catalogue exhaustion. That design needs
its own client/reload contract and must never train Taste or spend exposure
positions merely because the server returned a page.

## Real PostgreSQL acceptance

Use independent connections and deterministic barriers around snapshot,
computation and publication on the isolated catalogue. SQLite ignores the
relevant row-lock semantics and cannot establish these properties.

| Race/check | Required result |
| --- | --- |
| Fake ranking blocks while another connection posts like/skip or Fit PATCH | Feedback/profile mutation completes before releasing the ranking barrier. Measure owner lock wait and hold separately from total ranking/request latency. |
| Fit changes M to L, or size-system/confirmation/budget changes after snapshot | Old admission is never published. At most one recomputation uses the new inputs; persistent conflict is controlled, with no partial writes. |
| Taste/history/hidden state changes while scoring | Revision conflict is detected, including events that do not touch User Twin features. Newly excluded identity aliases do not appear. |
| Same Intent request id concurrently, and same id with different text | One owned Intent/generation and one published successful result; same-text retry gets pending/replay contract; different text is 409. |
| Refine, close or task feedback commits between phases | Old version cannot overwrite the new constraints, exclusions, closure or result. Preserve task-local versus permanent-feedback semantics. |
| Import changes finalist price, stock, role, identity, variant size evidence or source policy | Current path's admission is checked again; stale reasons/size claims are not serialized. Product/policy lock contention times out without holding the owner queue. |
| Concurrent feed deliveries followed by concurrent real feed events | No lost issued attribution/caches; exposure product and position uniqueness and reactions preserved. Do not assert disjoint delivery pages unless the separate reservation contract is implemented. |
| Cancellation before publication; failure during commit; response lost after commit | No pre-publication result/impression/cache writes. An uncertain commit is recovered through persisted request/generation replay, not blind re-run or duplicate analytics. |
| Sustained profile changes | Maximum two compute attempts total; no infinite retry loop. No old-size fallback to manufacture a nonempty result. |
| Catalogue/owner isolation | Stable identity/order semantics, full candidate coverage and late eligible winners remain; no preference/context or exclusion state leaks between owners. |

Keep the existing exact-size, identity, feedback, Intent replay and diversity
regression suites. Add API-level PostgreSQL races for actual entry points, not
only helper mocks. Record attempted computations, discarded proposals, commits,
pool checkouts, lock wait/hold and total duration, with a reproducible catalogue
fixture and comparison to the still-protected path. Select indexes only from
measured isolated query plans; this proposal does not prescribe speculative
indexes, new embeddings or a framework replacement.

## Completion boundary

This document is a next-slice contract, not evidence of deployment or concurrency
correctness. The remaining P1 can be marked resolved for a path only after writer
coverage, bounded publication/retry, replay behaviour and real PostgreSQL races
pass for that path. Keep the frozen build14 source and release evidence unchanged
while implementing the next immutable candidate separately.
