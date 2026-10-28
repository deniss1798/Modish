"""Read cheap size evidence before hydrating merchant blobs.

This is a negative-only retrieval optimization. Every returned ORM product
still needs the caller's full eligibility and source rules. Preserve SQL order,
query scope and live identity-map values; never cap the candidate catalogue.
"""
from functools import lru_cache

from ...models import Product
from ..fit_size_profile import SLOT_CATEGORIES, requested_size_for_product
from .size_resolver import _flat_sizes_may_match, may_match_flat_size, resolve_size
from ..recommendation_budget import check_ranking_budget


def _confirmed_size_choices(fit):
    stored = getattr(fit, 'size_profile_json', None) or {}
    if not isinstance(stored, dict):
        return None
    sizes = stored.get('sizes') or {}
    if not isinstance(sizes, dict):
        return None
    choices = []
    for slot in SLOT_CATEGORIES:
        selected = sizes.get(slot) or {}
        if not isinstance(selected, dict):
            return None
        if selected.get('state') != 'confirmed':
            continue
        value, system = selected.get('value'), selected.get('system')
        if not all(item is None or isinstance(item, str) for item in (value, system)):
            return None
        choices.append((value, system))
    return tuple(choices)


@lru_cache(maxsize=128)
def _requested_tokens(choices):
    # Omitting category is deliberately more permissive for waist sizes. This
    # probe can reject only a token absent from every confirmed personal slot.
    resolved = (resolve_size(value, size_system=system) for value, system in choices)
    return tuple(sorted({size.normalized_size for size in resolved if size.known}))


@lru_cache(maxsize=4096)
def _flat_list_matches_any(values: tuple[str, ...], tokens: tuple[str, ...]):
    return any(_flat_sizes_may_match(values, token) for token in tokens)


def _may_match_any_confirmed_size(product, fit):
    choices = _confirmed_size_choices(fit)
    if choices is None:
        return True  # Preserve the existing path for malformed legacy profiles.
    values = product.available_sizes
    if isinstance(values, str):
        values = (values,)
    if not isinstance(values, (list, tuple)):
        return True  # Do not consume or reinterpret other legacy containers.
    # Key current values rather than list identity: untracked in-place JSON
    # mutations must be as visible as they are to the full ORM admission path.
    return _flat_list_matches_any(tuple(str(value) for value in values), _requested_tokens(choices))


def iter_size_candidates(db, query, fit, *, batch_size=200):
    check_ranking_budget(db)
    if not 1 <= batch_size <= 500:
        raise ValueError('batch_size must be between 1 and 500')
    # Autoflush is disabled in production. Full ORM reads preserve pending
    # field changes; the old path also handles any ambiguous query shape.
    if any(isinstance(value, Product) for value in db.dirty):
        with db.scalars(query.execution_options(yield_per=batch_size)) as rows:
            yield from rows
        return

    projection = query.with_only_columns(Product.id, Product.title,
        Product.category, Product.category_name, Product.subcategory,
        Product.available_sizes, maintain_column_froms=True)
    # Plain JSON lists can change in place without SQLAlchemy marking dirty.
    # Use the live loaded object for the same predicate as a full ORM read.
    loaded = {value.id: value for value in db.identity_map.values()
              if isinstance(value, Product)}
    pending = []

    def hydrate():
        check_ranking_budget(db)
        # Repeat the original scope so a concurrent importer cannot move a
        # now-forbidden row into the shortlist between the two reads.
        # Pagination already chose the projected IDs. Applying its offset to
        # this smaller batch again would silently drop those same candidates.
        hydrate_query = query.limit(None).offset(None).where(Product.id.in_(pending))
        with db.scalars(hydrate_query) as rows:
            products = {product.id: product for product in rows}
        for pid in pending:
            product = products.get(pid)
            if product is not None:
                yield product

    with db.execute(projection.execution_options(yield_per=500)) as rows:
        for row in rows:
            check_ranking_budget(db)
            evidence = loaded.get(row.id, row)
            if not _may_match_any_confirmed_size(evidence, fit):
                continue
            requested, system = requested_size_for_product(fit, evidence)
            if not may_match_flat_size(evidence, requested, requested_system=system):
                continue
            pending.append(row.id)
            if len(pending) == batch_size:
                yield from hydrate()
                pending.clear()
        if pending:
            yield from hydrate()
