# Separate catalogue contract — 14 October 2026

The dedicated catalogue supports browsing and opening the merchant when the
merchant size system or the person's size is not confirmed. It never claims
that a product fits the person. Exact-size admission remains mandatory for
personal feed, Style Swipe, shopping intent and complete outfits.

## HTTP and capability

All discovery reads require the usual authenticated bearer token. They do not
require completed Style Swipe, and do not create or complete an activation
session. `/onboarding/status` advertises `catalogue_discovery_v1` in its existing
`recommendation_features` array. Clients hide the entry when that capability is
absent, including when connected to an older backend.

`GET /catalog/discovery` accepts:

| Parameter | Contract |
| --- | --- |
| `category` | Empty/all, `shoes`, `outerwear`, `tops`, `bottoms`, `dresses`. Aliases `обувь`, `верхняя одежда`, `верхняя_одежда`, `верхний_слой`, `платья` map to the matching group. Unknown groups return 422. The sold garment role is authoritative, not an outfit visible in the photograph. |
| `q` | Optional literal catalogue search, maximum 160 characters, using the existing escaped search conditions. Description cross-selling does not establish a match. |
| `source` | Optional source code, maximum 64 characters; case and surrounding whitespace are normalized. It never bypasses excluded-source or source-rule gates. |
| `min_price`, `max_price` | Nonnegative integer rubles. Inverted range returns 422. Effective range is intersected with the server/profile bounds; a range with no intersection returns an honest empty result. |
| `limit` | 1–40, default 20. |
| `cursor` | Opaque query-scoped continuation returned by the prior page; maximum 1024 characters. Client clears it after changing filters. Invalid, foreign-owner, changed-scope or no-longer-valid cursor returns 422 and asks the client to reload. |

Response:

```json
{
  "products": [],
  "next_cursor": null,
  "has_more": false,
  "size_verification": "not_verified",
  "mode": "catalogue_discovery",
  "personalized": false,
  "scope": {
    "gender_target": "womenswear",
    "gender_basis": "catalogue_default",
    "budget_max": 10000,
    "budget_basis": "catalogue_default"
  }
}
```

Each product is the existing plain Product API DTO plus:

```json
{
  "catalogue_discovery": true,
  "size_verification": "not_verified",
  "size_notice": "Размер нужно уточнить у магазина",
  "merchant_sizes": [
    {
      "raw_size": "39",
      "normalized_size": null,
      "size_system": "UNKNOWN",
      "availability": "available"
    }
  ]
}
```

`merchant_sizes` carries existing merchant tokens and resolver evidence. A
numeric token without an explicit system stays `UNKNOWN`; conflicting units and
ambiguous compound sizes also cannot establish a known unit. Supported proved
systems are RU/EU/US/UK/LETTER/WAIST. Size availability preserves the source
resolver's `available`, `unavailable` or `unknown` state; this is feed evidence,
not a checkout guarantee. Empty merchant size evidence yields an empty list.
Every row still has `size_verification=not_verified`, even when its merchant
system is known, because discovery has not compared it with the person's Fit.

The top-level Product `size_system` is derived from current consistent resolver
evidence in this mode. A denormalized old system column cannot turn unknown
merchant units into a known system. Products have no recommendation reasons,
fit scores or size-match claims.

`GET /catalog/discovery/{product_id}` returns the same flagged Product DTO after
rechecking all non-size rules for the authenticated owner. Missing, forbidden,
hidden, over-budget or stale products return 404. Detail refresh checks the
current server/profile scope; list-specific search/price text is not a promise
that a later changed price still matches that earlier query.

## Admission and defaults

The only omitted rule is exact-size matching. Every returned product still
passes adult/fashion classification, sold-garment role, compatible known gender,
the budget intersection, RUB/RUR currency, current global available stock,
the existing 72-hour observation/future-skew limits, active/nondeleted flags,
public photo URL, valid public merchant link, source exclusions/rules and the
casual segment's existing sports exclusion. No time is rewritten or inferred
from a view. No source is imported or updated by these endpoints.

- If Fit has women's or men's gender, use it and mark `gender_basis=profile`.
  Missing/unknown/unisex Fit uses the catalogue's women's default and explicitly
  marks `catalogue_default`; it does not write that value into Fit.
- A positive saved Fit budget is used with a hard 25,000-ruble ceiling. Otherwise
  a saved economy/mass/mid segment supplies 4,000/10,000/25,000 rubles. Without
  such a segment the documented mass default is 10,000 rubles. The response
  reports `profile`, `profile_segment` or `catalogue_default` as its basis.
- The catalogue's price floor is 300 rubles, as in activation admission. Explicit
  price filters further narrow this range; a request cannot widen the owner/server
  maximum. Premium settings do not increase the hard ceiling.
- Existing active hidden states and activation dislikes/unavailable reactions
  suppress their visible aliases. Another owner's hidden state is ignored.

Missing or unconfirmed sizes are not filled in, translated across national
systems, or treated as a weak personalization score. They are simply irrelevant
to this explicitly unverified browse mode.

## Pagination and resource contract

Products are visited by stable ascending Product ID. The first admissible offer
represents a visible identity, using the existing shared merchant/model/image
identity keys. Hard quality/filter admission precedes identity reservation.
Aliases are suppressed across page boundaries as well as within a page.

The cursor contains an opaque versioned ID and scope fingerprint. Its fingerprint
binds the owner, effective gender/budget, normalized query/category/source/price
filters and active hidden IDs. It is not an authorization credential; all gates
are re-evaluated for every request. Tampering cannot widen the server budget or
expose another owner's rows. The cursor anchor must still be a valid canonical
row in that scope, otherwise a controlled 422 reload is required.

`has_more` requires one actual admitted, nonduplicate lookahead row. A rejected
or duplicate row cannot create a phantom next page. `next_cursor` exists only
when that lookahead is present.

ORM hydration streams in batches of 200. To reconstruct cross-page identity
exclusion without a growing client cursor or process-local mutable session, a
later page rescans the preceding admitted prefix. Identity strings therefore
use O(admitted prefix) memory and late-page work grows with the prefix. This
first version deliberately favors correct deduplication; it is not a claim of
constant total memory or constant-time paging. No most-recent-1000 cutoff hides
late valid products. Future optimization should benchmark real sparse/wide
catalogues before adding a persisted canonical identity index.

## Client actions and persistence

Discovery supports browse, factual detail refresh and opening the merchant.
It exposes no discovery Like/Save mutation endpoint. The client hides those
actions, skips strict complements/why-this-fits, and does not send generic
recommendation events for discovery view/open/click. The existing affiliate URL
ledger can obtain the merchant URL; the client must not follow it with the
separate recommendation/taste event.

The existing generic event API is a separate API and has not been rewritten by
this feature. Consequently, clients must keep the discovery action contract
explicit rather than tagging a generic training event with a new surface and
assuming that makes it read-only. A later bookmark-only feature needs its own
server contract before adding Save to this screen.

These two GET routes do not initialize profiles, log exposures/impressions,
train User Twin/Taste, or write first-session/intent metrics. Opening discovery
does not mark cold onboarding completed or allow a cold personal feed.

## Verification and release handoff

`tests/test_catalog_discovery.py` passed 16 tests on 14 October. Its fixtures
cover authentication, cold/unconfirmed numeric profiles, non-size rejection
cases, category/source/literal search/price filtering, server ceiling, owner
hidden aliases, current detail gates, unknown/conflicting merchant units,
query/owner/stale/unknown cursors, cross-page alias lookahead and zero profile /
Taste / exposure / metric writes. A valid product after 1501 rejected child rows
is returned; the sparse scan retains fewer than 500 simultaneous Product ORM
objects rather than every merchant blob.

The broader discovery + activation + exact eligibility + repeat + shopping
intent + intent HTTP + WOW regression run passed **88 tests in 6.387 seconds**.
This is local SQLite evidence, not a production coverage or latency claim.

New package entries required:

- `app/services/catalog_discovery.py`
- `app/api/catalog_discovery.py`
- `tests/test_catalog_discovery.py`

Existing package entries `app/main.py` and `app/api/onboarding.py` contain only
the discovery import/router registration and feature addition from this task.
Other owners may independently change their photo-analysis bodies.

No package/archive, Git push, feed renewal or server deployment was performed by
the discovery implementer. The release owner must include the new files, run
the final package suite, migrate/rehearse on the isolated production clone,
verify fresh real catalogue coverage and then perform the authorized deployment.
