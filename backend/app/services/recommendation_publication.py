"""Current catalogue evidence for short recommendation publication phases.

All catalogue waits and identity work precede the caller's owner lock. This
module never scores, loads personal history, acquires User, or commits.
"""
from dataclasses import dataclass
from hashlib import sha256
import json

from sqlalchemy import select, text

from ..models import Product, ProductSource, SourceRule
from .product_identity import product_identity_keys
from .recommendation_revision import RecommendationSnapshotChanged


@dataclass(frozen=True)
class ProductEvidence:
    product_id: str
    source_id: str | None
    evidence: str


@dataclass
class LockedCatalogue:
    products: dict
    rules: dict
    evidence: dict


def product_evidence(product):
    values = {column.name: getattr(product, column.name) for column in Product.__table__.columns}
    return sha256(json.dumps(values, sort_keys=True, ensure_ascii=True, default=str,
        separators=(',', ':')).encode('utf-8')).hexdigest()


def identity_projection(ids):
    # Excluded products can have large merchant blobs: their identity is enough.
    return select(Product.id, Product.original_url, Product.source, Product.group_id,
        Product.vendor_code, Product.image_url).where(Product.id.in_(ids)).order_by(Product.id)


def identity_signature(keys):
    return sha256(json.dumps(sorted(keys), ensure_ascii=True,
        separators=(',', ':')).encode('utf-8')).hexdigest()


def identity_rows_signature(rows):
    """Keep identity ownership when consumers compare separate outfit groups."""
    values = sorted((row.id, sorted(product_identity_keys(row))) for row in rows)
    return sha256(json.dumps(values, ensure_ascii=True,
        separators=(',', ':')).encode('utf-8')).hexdigest()


def lock_catalogue(db, finalists, *, excluded_ids=(), excluded_identity_signature=None,
        excluded_identity_rows_signature=None, include_source_rules=True, max_finalists=100):
    """Lock finalist and exclusion evidence in one declared stable order.

    Finalists are bounded by the path. Exclusions must stay complete; a timeout
    rejects the proposal rather than silently truncating the personal history.
    Returned ORM objects remain inside the caller's publication Session.
    """
    maximum = max(1, min(201, int(max_finalists)))
    if len(finalists) > maximum:
        raise ValueError(f'Publication has more than {maximum} finalists')
    if db.get_bind().dialect.name == 'postgresql':
        db.execute(text("SET LOCAL lock_timeout = '750ms'"))
        db.execute(text("SET LOCAL statement_timeout = '2000ms'"))
    source_ids = sorted({row.source_id for row in finalists if row.source_id}) if include_source_rules else []
    ids = sorted({row.product_id for row in finalists})
    if source_ids:
        list(db.scalars(select(ProductSource.id).where(ProductSource.id.in_(source_ids))
            .order_by(ProductSource.id).with_for_update(read=True)))
    rules = {}
    if source_ids:
        for rule in db.scalars(select(SourceRule).where(SourceRule.source_id.in_(source_ids),
                SourceRule.is_active == 1).order_by(SourceRule.id).with_for_update(read=True)):
            rules.setdefault(rule.source_id, []).append(rule)
    excluded_ids = set(excluded_ids)
    keys = set()
    identity_rows = []
    locked_ids = sorted(set(ids) | excluded_ids)
    if locked_ids:
        with db.execute(identity_projection(locked_ids).with_for_update(read=True)
                .execution_options(yield_per=200)) as identities:
            for row in identities:
                if row.id in excluded_ids:
                    keys.update(product_identity_keys(row))
                    if excluded_identity_rows_signature is not None:
                        identity_rows.append(row)
    if excluded_identity_signature is not None and identity_signature(keys) != excluded_identity_signature:
        raise RecommendationSnapshotChanged()
    if excluded_identity_rows_signature is not None and identity_rows_signature(identity_rows) != excluded_identity_rows_signature:
        raise RecommendationSnapshotChanged()
    products = {row.id: row for row in db.scalars(select(Product).where(Product.id.in_(ids)))} if ids else {}
    return LockedCatalogue(products, rules, {pid: product_evidence(row) for pid, row in products.items()})
