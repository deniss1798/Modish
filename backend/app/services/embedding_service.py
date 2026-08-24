"""Embedding retrieval layer for MIE.

The first implementation stores vectors in SQL JSON and uses a deterministic
local text vectorizer. The recommendation code depends on VectorStore, so this
can be swapped for pgvector or Qdrant without changing the ranking pipeline.
"""
from __future__ import annotations

import hashlib
import math
import re
from dataclasses import dataclass
from datetime import datetime, timedelta, timezone
from typing import Protocol
from uuid import uuid4

from sqlalchemy import and_, or_, select
from sqlalchemy.orm import Session

from ..models import Product, ProductEmbedding, RecommendationEventV2, User
from .recommendation_config import EMBEDDING_MODEL_VERSION


EMBEDDING_MODEL = EMBEDDING_MODEL_VERSION
EMBEDDING_DIM = 64
NEGATIVE_SIMILARITY_COEFFICIENT = 0.65

POSITIVE_EMBEDDING_EVENT_WEIGHTS: dict[str, float] = {
  "like": 1.0,
  "save": 2.0,
  "affiliate_click": 2.0,
  "purchase": 4.0,
  "post_purchase_positive": 6.0,
}

NEGATIVE_EMBEDDING_EVENT_WEIGHTS: dict[str, float] = {
  "dislike": 2.0,
  "post_purchase_negative": 4.0,
}

_TOKEN_RE = re.compile(r"[\wа-яА-ЯёЁ]+", re.UNICODE)


@dataclass(frozen=True)
class VectorSearchResult:
  product_id: str
  score: float


@dataclass(frozen=True)
class EmbeddingIndexSummary:
  scanned: int
  indexed: int
  skipped: int
  errors: int

  def to_dict(self) -> dict[str, int]:
    return {
      "scanned": self.scanned,
      "indexed": self.indexed,
      "skipped": self.skipped,
      "errors": self.errors,
    }


@dataclass(frozen=True)
class UserTasteEmbedding:
  positive_centroid: list[float] | None
  negative_centroid: list[float] | None
  positive_events: int
  negative_events: int

  @property
  def has_signal(self) -> bool:
    return bool(self.positive_centroid)


class VectorStore(Protocol):
  def upsert_product_embedding(self, db: Session, *, product: Product, embedding: list[float], text: str) -> ProductEmbedding:
    ...

  def search_similar_products(
    self,
    db: Session,
    *,
    query_embedding: list[float],
    limit: int,
    exclude_product_ids: set[str] | None = None,
    source: str | None = None,
    min_price: int = 500,
    min_score: float = 0.05,
  ) -> list[VectorSearchResult]:
    ...

  def delete_product_embedding(self, db: Session, *, product_id: str) -> None:
    ...


class SemanticVectorStoreAdapter:
  """Future adapter for pgvector/Qdrant and a real semantic embedding model."""

  def upsert_product_embedding(self, db: Session, *, product: Product, embedding: list[float], text: str) -> ProductEmbedding:
    del db, product, embedding, text
    raise NotImplementedError("TODO(MIE): connect pgvector/Qdrant semantic vector storage")

  def search_similar_products(
    self,
    db: Session,
    *,
    query_embedding: list[float],
    limit: int,
    exclude_product_ids: set[str] | None = None,
    source: str | None = None,
    min_price: int = 500,
    min_score: float = 0.05,
  ) -> list[VectorSearchResult]:
    del db, query_embedding, limit, exclude_product_ids, source, min_price, min_score
    raise NotImplementedError("TODO(MIE): use ANN search in pgvector/Qdrant")

  def delete_product_embedding(self, db: Session, *, product_id: str) -> None:
    del db, product_id
    raise NotImplementedError("TODO(MIE): delete vector from pgvector/Qdrant")


def _now() -> datetime:
  return datetime.now(timezone.utc)


def _clean_parts(values: list[object]) -> list[str]:
  out: list[str] = []
  for raw in values:
    if isinstance(raw, list):
      out.extend(_clean_parts(raw))
      continue
    value = str(raw or "").strip()
    if value:
      out.append(value)
  return out


def product_embedding_text(product: Product) -> str:
  parts = _clean_parts(
    [
      product.brand,
      product.title,
      product.category,
      product.subcategory,
      product.category_name,
      product.colors or [],
      product.color_family,
      product.style_tags or [],
      product.silhouette,
      product.fit,
      product.material,
      product.occasion,
      product.description,
    ]
  )
  return " ".join(parts).strip().lower()


def embedding_text_hash(text: str) -> str:
  return hashlib.sha256(text.encode("utf-8")).hexdigest()


def _normalize_vector(vector: list[float]) -> list[float]:
  norm = math.sqrt(sum(float(x) * float(x) for x in vector))
  if norm <= 0:
    return [0.0 for _ in vector]
  return [float(x) / norm for x in vector]


def embed_text(text: str, *, dim: int = EMBEDDING_DIM) -> list[float]:
  vector = [0.0 for _ in range(dim)]
  for token in _TOKEN_RE.findall((text or "").lower()):
    digest = hashlib.blake2b(token.encode("utf-8"), digest_size=8).digest()
    index = int.from_bytes(digest[:4], "big") % dim
    sign = 1.0 if (digest[4] % 2 == 0) else -1.0
    vector[index] += sign
  return _normalize_vector(vector)


def cosine_similarity(left: list[float] | None, right: list[float] | None) -> float:
  if not left or not right or len(left) != len(right):
    return 0.0
  return float(sum(float(a) * float(b) for a, b in zip(left, right)))


def weighted_average(vectors: list[tuple[list[float], float]]) -> list[float] | None:
  if not vectors:
    return None
  dim = len(vectors[0][0])
  total_weight = sum(max(0.0, float(weight)) for _, weight in vectors)
  if total_weight <= 0:
    return None
  out = [0.0 for _ in range(dim)]
  for vector, weight in vectors:
    if len(vector) != dim:
      continue
    w = max(0.0, float(weight))
    for idx, value in enumerate(vector):
      out[idx] += float(value) * w
  return _normalize_vector([value / total_weight for value in out])


def build_query_embedding(user_embedding: UserTasteEmbedding) -> list[float] | None:
  if not user_embedding.positive_centroid:
    return None
  query = list(user_embedding.positive_centroid)
  if user_embedding.negative_centroid:
    query = [
      pos - NEGATIVE_SIMILARITY_COEFFICIENT * neg
      for pos, neg in zip(user_embedding.positive_centroid, user_embedding.negative_centroid)
    ]
  return _normalize_vector(query)


class SqlAlchemyVectorStore:
  def upsert_product_embedding(self, db: Session, *, product: Product, embedding: list[float], text: str) -> ProductEmbedding:
    now = _now()
    row = db.execute(
      select(ProductEmbedding).where(ProductEmbedding.product_id == product.id)
    ).scalar_one_or_none()
    if row is None:
      row = ProductEmbedding(
        id=str(uuid4()),
        product_id=product.id,
        embedding_model=EMBEDDING_MODEL,
        embedding_dim=len(embedding),
        embedding_json=list(embedding),
        text_hash=embedding_text_hash(text),
        text_snapshot=text,
        created_at=now,
        updated_at=now,
      )
      db.add(row)
    else:
      row.embedding_model = EMBEDDING_MODEL
      row.embedding_dim = len(embedding)
      row.embedding_json = list(embedding)
      row.text_hash = embedding_text_hash(text)
      row.text_snapshot = text
      row.updated_at = now
    db.flush()
    return row

  def search_similar_products(
    self,
    db: Session,
    *,
    query_embedding: list[float],
    limit: int,
    exclude_product_ids: set[str] | None = None,
    source: str | None = None,
    min_price: int = 500,
    min_score: float = 0.05,
  ) -> list[VectorSearchResult]:
    exclude = {str(pid).strip() for pid in (exclude_product_ids or set()) if str(pid).strip()}
    conds = [
      ProductEmbedding.embedding_model == EMBEDDING_MODEL,
      Product.is_active == 1,
      Product.is_available == 1,
      Product.is_deleted_from_feed == 0,
      Product.source != "demo",
      Product.price > min_price,
      Product.image_url.isnot(None),
      Product.image_url != "",
      or_(
        and_(Product.affiliate_url.isnot(None), Product.affiliate_url != ""),
        Product.product_url != "",
      ),
    ]
    if source and source.strip():
      conds.append(Product.source == source.strip())
    if exclude:
      conds.append(Product.id.notin_(list(exclude)))
    rows = db.execute(
      select(ProductEmbedding.product_id, ProductEmbedding.embedding_json)
      .join(Product, Product.id == ProductEmbedding.product_id)
      .where(and_(*conds))
      .limit(5000)
    ).all()
    scored = [
      VectorSearchResult(product_id=str(product_id), score=cosine_similarity(query_embedding, list(vector or [])))
      for product_id, vector in rows
    ]
    scored = [row for row in scored if row.score >= min_score]
    scored.sort(key=lambda row: row.score, reverse=True)
    return scored[: max(1, int(limit))]

  def delete_product_embedding(self, db: Session, *, product_id: str) -> None:
    row = db.execute(
      select(ProductEmbedding).where(ProductEmbedding.product_id == product_id)
    ).scalar_one_or_none()
    if row is not None:
      db.delete(row)
      db.flush()


DEFAULT_VECTOR_STORE = SqlAlchemyVectorStore()


def upsert_product_embedding(
  db: Session,
  product: Product,
  *,
  vector_store: VectorStore = DEFAULT_VECTOR_STORE,
) -> ProductEmbedding:
  text = product_embedding_text(product)
  return vector_store.upsert_product_embedding(db, product=product, embedding=embed_text(text), text=text)


def ensure_product_embedding(
  db: Session,
  product: Product,
  *,
  vector_store: VectorStore = DEFAULT_VECTOR_STORE,
) -> ProductEmbedding:
  text = product_embedding_text(product)
  text_hash = embedding_text_hash(text)
  row = db.execute(
    select(ProductEmbedding).where(ProductEmbedding.product_id == product.id)
  ).scalar_one_or_none()
  if row and row.embedding_model == EMBEDDING_MODEL and row.text_hash == text_hash:
    return row
  return vector_store.upsert_product_embedding(db, product=product, embedding=embed_text(text), text=text)


def _event_weight(event_type: str) -> tuple[str, float] | None:
  if event_type in POSITIVE_EMBEDDING_EVENT_WEIGHTS:
    return "positive", POSITIVE_EMBEDDING_EVENT_WEIGHTS[event_type]
  if event_type in NEGATIVE_EMBEDDING_EVENT_WEIGHTS:
    return "negative", NEGATIVE_EMBEDDING_EVENT_WEIGHTS[event_type]
  return None


def build_user_taste_embedding(
  db: Session,
  *,
  user_id: str,
  since_days: int = 365,
  max_events: int = 200,
) -> UserTasteEmbedding:
  since = _now() - timedelta(days=max(1, int(since_days)))
  rows = db.execute(
    select(RecommendationEventV2.product_id, RecommendationEventV2.event_type)
    .where(
      RecommendationEventV2.user_id == user_id,
      RecommendationEventV2.product_id.is_not(None),
      RecommendationEventV2.created_at >= since,
      RecommendationEventV2.event_type.in_(
        list(POSITIVE_EMBEDDING_EVENT_WEIGHTS) + list(NEGATIVE_EMBEDDING_EVENT_WEIGHTS)
      ),
    )
    .order_by(RecommendationEventV2.created_at.desc())
    .limit(max(1, int(max_events)))
  ).all()
  if not rows:
    return UserTasteEmbedding(None, None, 0, 0)

  product_ids = {str(pid) for pid, _ in rows if str(pid or "").strip()}
  products = {
    product.id: product
    for product in db.execute(select(Product).where(Product.id.in_(product_ids))).scalars().all()
  }
  embeddings: dict[str, list[float]] = {}
  for product_id, product in products.items():
    row = ensure_product_embedding(db, product)
    embeddings[product_id] = list(row.embedding_json or [])

  positive_vectors: list[tuple[list[float], float]] = []
  negative_vectors: list[tuple[list[float], float]] = []
  for product_id, event_type in rows:
    product_id = str(product_id or "")
    event = _event_weight(str(event_type or ""))
    vector = embeddings.get(product_id)
    if event is None or not vector:
      continue
    direction, weight = event
    if direction == "positive":
      positive_vectors.append((vector, weight))
    else:
      negative_vectors.append((vector, weight))

  return UserTasteEmbedding(
    positive_centroid=weighted_average(positive_vectors),
    negative_centroid=weighted_average(negative_vectors),
    positive_events=len(positive_vectors),
    negative_events=len(negative_vectors),
  )


def retrieve_embedding_candidates(
  db: Session,
  *,
  user: User,
  limit: int = 150,
  exclude_product_ids: set[str] | None = None,
  source: str | None = None,
  min_price: int = 500,
  vector_store: VectorStore = DEFAULT_VECTOR_STORE,
) -> list[Product]:
  user_embedding = build_user_taste_embedding(db, user_id=user.id)
  query = build_query_embedding(user_embedding)
  if not query:
    return []
  hits = vector_store.search_similar_products(
    db,
    query_embedding=query,
    limit=limit,
    exclude_product_ids=exclude_product_ids,
    source=source,
    min_price=min_price,
  )
  if not hits:
    return []
  ids = [hit.product_id for hit in hits]
  by_id = {
    product.id: product
    for product in db.execute(select(Product).where(Product.id.in_(ids))).scalars().all()
  }
  return [by_id[product_id] for product_id in ids if product_id in by_id]


def index_active_product_embeddings(
  db: Session,
  *,
  source: str | None = None,
  limit: int | None = None,
  vector_store: VectorStore = DEFAULT_VECTOR_STORE,
) -> EmbeddingIndexSummary:
  conds = [
    Product.is_active == 1,
    Product.is_available == 1,
    Product.is_deleted_from_feed == 0,
    Product.source != "demo",
  ]
  if source and source.strip():
    conds.append(Product.source == source.strip())
  query = select(Product).where(and_(*conds)).order_by(Product.updated_at.desc(), Product.id.asc())
  if limit is not None:
    query = query.limit(max(1, int(limit)))
  products = db.execute(query).scalars().all()

  scanned = indexed = skipped = errors = 0
  for product in products:
    scanned += 1
    if not product_embedding_text(product):
      skipped += 1
      continue
    try:
      ensure_product_embedding(db, product, vector_store=vector_store)
      indexed += 1
    except Exception:
      errors += 1
  db.flush()
  return EmbeddingIndexSummary(scanned=scanned, indexed=indexed, skipped=skipped, errors=errors)
