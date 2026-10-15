"""Read cheap size evidence before hydrating merchant blobs.

This is a negative-only retrieval optimization. Every returned ORM product
still needs the caller's full eligibility and source rules. Preserve SQL order,
query scope and live identity-map values; never cap the candidate catalogue.
"""
from ...models import Product
from ..fit_size_profile import requested_size_for_product
from .size_resolver import may_match_flat_size
from ..recommendation_budget import check_ranking_budget


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
            requested, system = requested_size_for_product(fit, evidence)
            if not may_match_flat_size(evidence, requested, requested_system=system):
                continue
            pending.append(row.id)
            if len(pending) == batch_size:
                yield from hydrate()
                pending.clear()
        if pending:
            yield from hydrate()
