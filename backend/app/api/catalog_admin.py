from __future__ import annotations

import os
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Literal
from uuid import uuid4

from fastapi import APIRouter, Depends, File, Header, HTTPException, UploadFile
from pydantic import BaseModel, Field
from sqlalchemy import func, or_, select
from sqlalchemy.orm import Session

from ..catalog_demo import demo_picsum_image_url
from ..catalog_normalize import normalize_category, normalize_product_colors
from ..db import SessionLocal
from ..models import CatalogSyncRun, Product, ProductSource, SourceRule
from ..services.catalog.admitad_bootstrap import bootstrap_admitad_csv_sources
from ..services.catalog.feed_import_service import sync_partner_feed, sync_partner_feed_from_text


def _allow_demo_catalog_mutations() -> bool:
  """POST demo-seed / demo-rewrite — только если явно включено в .env (прод: реальные фиды)."""
  return os.getenv("ALLOW_DEMO_CATALOG", "").strip().lower() in ("1", "true", "yes", "on")


RuleType = Literal[
  "blocked_brand",
  "min_price",
  "blocked_category",
  "blocked_category_exact",
  "blocked_keyword",
  "allowed_category",
  "requires_affiliate_url",
  "hide_without_image",
  "hide_without_size",
]
RULE_TYPES: frozenset[str] = frozenset(
  (
    "blocked_brand",
    "min_price",
    "blocked_category",
    "blocked_category_exact",
    "blocked_keyword",
    "allowed_category",
    "requires_affiliate_url",
    "hide_without_image",
    "hide_without_size",
  )
)
FLAG_RULE_TYPES: frozenset[str] = frozenset(
  ("requires_affiliate_url", "hide_without_image", "hide_without_size")
)
MOCK_LAMODA_REL = Path("test_feeds") / "mock_lamoda.yml"

router = APIRouter(prefix="/admin/catalog", tags=["admin-catalog"])


def get_db() -> Any:
  db = SessionLocal()
  try:
    yield db
  finally:
    db.close()


def _admin_auth(authorization: str | None) -> None:
  admin_token = (os.getenv("ADMIN_TOKEN") or "").strip()
  if not admin_token:
    raise HTTPException(status_code=503, detail="ADMIN_TOKEN is not configured")
  if authorization != f"Bearer {admin_token}":
    raise HTTPException(status_code=401, detail="Admin token required")


class ProductSourceCreate(BaseModel):
  code: str = Field(min_length=2, max_length=64)
  name: str = Field(min_length=1, max_length=128)
  network: str = Field(min_length=1, max_length=64)
  advertiser_id: str | None = None
  feed_url: str | None = None
  deeplink_template: str | None = None
  status: str = "pending"


class SourceRuleCreate(BaseModel):
  rule_type: RuleType
  rule_value: str = Field(default="1", max_length=4000)
  is_active: bool = True


class SourceRulePatch(BaseModel):
  rule_type: RuleType | None = None
  rule_value: str | None = Field(default=None, max_length=4000)
  is_active: bool | None = None


class ProductSourcePatch(BaseModel):
  name: str | None = Field(default=None, min_length=1, max_length=128)
  network: str | None = Field(default=None, min_length=1, max_length=64)
  advertiser_id: str | None = None
  feed_url: str | None = None
  deeplink_template: str | None = None
  status: str | None = Field(default=None, max_length=32)


def _validate_rule_pair(rule_type: str, rule_value: str) -> None:
  rt = (rule_type or "").strip()
  rv = (rule_value or "").strip()
  if rt not in RULE_TYPES:
    raise HTTPException(status_code=400, detail=f"Unknown rule_type: {rt}")
  if rt in FLAG_RULE_TYPES:
    return
  if not rv:
    raise HTTPException(status_code=400, detail="rule_value is required")
  if rt == "min_price":
    try:
      if int(rv) < 0:
        raise ValueError
    except ValueError as exc:
      raise HTTPException(status_code=400, detail="min_price rule_value must be a non-negative integer") from exc


def _rule_to_api(r: SourceRule) -> dict[str, Any]:
  return {
    "id": r.id,
    "source_id": r.source_id,
    "rule_type": r.rule_type,
    "rule_value": r.rule_value,
    "is_active": bool(r.is_active),
    "created_at": r.created_at.isoformat(),
    "updated_at": r.updated_at.isoformat(),
  }


def _sync_run_to_api(run: CatalogSyncRun) -> dict[str, Any]:
  return {
    "sync_run_id": run.id,
    "id": run.id,
    "source_id": run.source_id,
    "status": run.status,
    "total_received": run.total_received,
    "created_count": run.created_count,
    "updated_count": run.updated_count,
    "deactivated_count": run.deactivated_count,
    "skipped_count": run.skipped_count,
    "ingested_count": run.ingested_count,
    "skip_breakdown": run.skip_breakdown or {},
    "error_message": run.error_message,
    "started_at": run.started_at.isoformat(),
    "finished_at": run.finished_at.isoformat() if run.finished_at else None,
  }


@router.get("/sources")
def admin_catalog_sources(
  db: Session = Depends(get_db),
  authorization: str | None = Header(default=None),
) -> list[dict[str, Any]]:
  _admin_auth(authorization)
  rows = db.execute(select(ProductSource).order_by(ProductSource.code)).scalars().all()
  return [
    {
      "id": s.id,
      "code": s.code,
      "name": s.name,
      "network": s.network,
      "advertiser_id": s.advertiser_id,
      "feed_url": s.feed_url,
      "deeplink_template": s.deeplink_template,
      "status": s.status,
      "last_sync_at": s.last_sync_at.isoformat() if s.last_sync_at else None,
      "created_at": s.created_at.isoformat(),
      "updated_at": s.updated_at.isoformat(),
    }
    for s in rows
  ]


@router.post("/sources")
def admin_catalog_sources_create(
  payload: ProductSourceCreate,
  db: Session = Depends(get_db),
  authorization: str | None = Header(default=None),
) -> dict[str, Any]:
  _admin_auth(authorization)
  exists = db.execute(select(ProductSource).where(ProductSource.code == payload.code)).scalar_one_or_none()
  if exists:
    raise HTTPException(status_code=409, detail="Source code already exists")
  now = datetime.now(timezone.utc)
  s = ProductSource(
    id=str(uuid4()),
    code=payload.code.strip(),
    name=payload.name.strip(),
    network=payload.network.strip(),
    advertiser_id=(payload.advertiser_id or "").strip() or None,
    feed_url=(payload.feed_url or "").strip() or None,
    deeplink_template=(payload.deeplink_template or "").strip() or None,
    status=payload.status.strip() or "pending",
    created_at=now,
    updated_at=now,
  )
  db.add(s)
  db.commit()
  return {"id": s.id, "code": s.code}


@router.patch("/sources/{source_id}")
def admin_catalog_sources_patch(
  source_id: str,
  payload: ProductSourcePatch,
  db: Session = Depends(get_db),
  authorization: str | None = Header(default=None),
) -> dict[str, Any]:
  _admin_auth(authorization)
  s = db.execute(select(ProductSource).where(ProductSource.id == source_id)).scalar_one_or_none()
  if s is None:
    raise HTTPException(status_code=404, detail="Source not found")
  now = datetime.now(timezone.utc)
  if payload.name is not None:
    s.name = payload.name.strip()
  if payload.network is not None:
    s.network = payload.network.strip()
  if payload.advertiser_id is not None:
    s.advertiser_id = payload.advertiser_id.strip() or None
  if payload.feed_url is not None:
    s.feed_url = payload.feed_url.strip() or None
  if payload.deeplink_template is not None:
    s.deeplink_template = payload.deeplink_template.strip() or None
  if payload.status is not None:
    s.status = payload.status.strip() or s.status
  s.updated_at = now
  db.commit()
  return {
    "id": s.id,
    "code": s.code,
    "feed_url": s.feed_url,
    "status": s.status,
    "updated_at": s.updated_at.isoformat(),
  }


@router.post("/sources/{source_id}/sync")
def admin_catalog_source_sync(
  source_id: str,
  db: Session = Depends(get_db),
  authorization: str | None = Header(default=None),
) -> dict[str, Any]:
  _admin_auth(authorization)
  src = db.execute(select(ProductSource).where(ProductSource.id == source_id)).scalar_one_or_none()
  if src is None:
    raise HTTPException(status_code=404, detail="Source not found")
  run = sync_partner_feed(db, source=src)
  return _sync_run_to_api(run)


_MAX_FEED_UPLOAD_BYTES = 50 * 1024 * 1024


@router.post("/sources/{source_id}/sync-upload")
async def admin_catalog_source_sync_upload(
  source_id: str,
  file: UploadFile = File(..., description="XML/YML фид (например Befree yml_catalog)"),
  db: Session = Depends(get_db),
  authorization: str | None = Header(default=None),
) -> dict[str, Any]:
  """Импорт из загруженного файла (без сети). Формат определяется по содержимому и имени файла."""
  _admin_auth(authorization)
  src = db.execute(select(ProductSource).where(ProductSource.id == source_id)).scalar_one_or_none()
  if src is None:
    raise HTTPException(status_code=404, detail="Source not found")
  raw = await file.read()
  if len(raw) > _MAX_FEED_UPLOAD_BYTES:
    raise HTTPException(
      status_code=413,
      detail=f"File too large (max {_MAX_FEED_UPLOAD_BYTES // (1024 * 1024)} MB)",
    )
  try:
    body = raw.decode("utf-8")
  except UnicodeDecodeError:
    body = raw.decode("utf-8", errors="replace")
  hint = (file.filename or "feed.xml").strip() or "feed.xml"
  run = sync_partner_feed_from_text(db, source=src, body=body, parser_url_hint=hint)
  return {**_sync_run_to_api(run), "upload_filename": hint}


@router.get("/sources/{source_id}/rules")
def admin_catalog_source_rules(
  source_id: str,
  db: Session = Depends(get_db),
  authorization: str | None = Header(default=None),
  include_inactive: bool = False,
) -> list[dict[str, Any]]:
  _admin_auth(authorization)
  src = db.execute(select(ProductSource).where(ProductSource.id == source_id)).scalar_one_or_none()
  if src is None:
    raise HTTPException(status_code=404, detail="Source not found")
  q = select(SourceRule).where(SourceRule.source_id == source_id)
  if not include_inactive:
    q = q.where(SourceRule.is_active == 1)
  q = q.order_by(SourceRule.created_at)
  rows = db.execute(q).scalars().all()
  return [_rule_to_api(r) for r in rows]


@router.post("/sources/{source_id}/rules")
def admin_catalog_source_rule_create(
  source_id: str,
  payload: SourceRuleCreate,
  db: Session = Depends(get_db),
  authorization: str | None = Header(default=None),
) -> dict[str, Any]:
  _admin_auth(authorization)
  src = db.execute(select(ProductSource).where(ProductSource.id == source_id)).scalar_one_or_none()
  if src is None:
    raise HTTPException(status_code=404, detail="Source not found")
  rv_store = "1" if payload.rule_type in FLAG_RULE_TYPES else payload.rule_value.strip()
  _validate_rule_pair(payload.rule_type, rv_store)
  now = datetime.now(timezone.utc)
  r = SourceRule(
    id=str(uuid4()),
    source_id=source_id,
    rule_type=payload.rule_type.strip(),
    rule_value=rv_store if payload.rule_type in FLAG_RULE_TYPES else payload.rule_value.strip(),
    is_active=1 if payload.is_active else 0,
    created_at=now,
    updated_at=now,
  )
  db.add(r)
  db.commit()
  return _rule_to_api(r)


@router.patch("/rules/{rule_id}")
def admin_catalog_rule_patch(
  rule_id: str,
  payload: SourceRulePatch,
  db: Session = Depends(get_db),
  authorization: str | None = Header(default=None),
) -> dict[str, Any]:
  _admin_auth(authorization)
  r = db.execute(select(SourceRule).where(SourceRule.id == rule_id)).scalar_one_or_none()
  if r is None:
    raise HTTPException(status_code=404, detail="Rule not found")
  rt = payload.rule_type if payload.rule_type is not None else r.rule_type
  rv = payload.rule_value if payload.rule_value is not None else r.rule_value
  if rt in FLAG_RULE_TYPES:
    rv = "1"
  _validate_rule_pair(rt, rv)
  if payload.rule_type is not None:
    r.rule_type = payload.rule_type.strip()
  if rt in FLAG_RULE_TYPES:
    r.rule_value = "1"
  elif payload.rule_value is not None:
    r.rule_value = payload.rule_value.strip()
  if payload.is_active is not None:
    r.is_active = 1 if payload.is_active else 0
  r.updated_at = datetime.now(timezone.utc)
  db.commit()
  return _rule_to_api(r)


@router.delete("/rules/{rule_id}")
def admin_catalog_rule_delete(
  rule_id: str,
  db: Session = Depends(get_db),
  authorization: str | None = Header(default=None),
) -> dict[str, Any]:
  _admin_auth(authorization)
  r = db.execute(select(SourceRule).where(SourceRule.id == rule_id)).scalar_one_or_none()
  if r is None:
    raise HTTPException(status_code=404, detail="Rule not found")
  db.delete(r)
  db.commit()
  return {"deleted": True, "id": rule_id}


@router.get("/sync-runs")
def admin_catalog_sync_runs(
  limit: int = 50,
  source_id: str | None = None,
  db: Session = Depends(get_db),
  authorization: str | None = Header(default=None),
) -> list[dict[str, Any]]:
  _admin_auth(authorization)
  q = select(CatalogSyncRun).order_by(CatalogSyncRun.started_at.desc())
  if source_id:
    q = q.where(CatalogSyncRun.source_id == source_id)
  rows = db.execute(q.limit(min(200, max(1, limit)))).scalars().all()
  return [_sync_run_to_api(r) for r in rows]


@router.get("/sync-runs/{run_id}")
def admin_catalog_sync_run_get(
  run_id: str,
  db: Session = Depends(get_db),
  authorization: str | None = Header(default=None),
) -> dict[str, Any]:
  _admin_auth(authorization)
  r = db.execute(select(CatalogSyncRun).where(CatalogSyncRun.id == run_id)).scalar_one_or_none()
  if r is None:
    raise HTTPException(status_code=404, detail="Sync run not found")
  return _sync_run_to_api(r)


@router.get("/products/stats")
def admin_catalog_product_stats(
  db: Session = Depends(get_db),
  authorization: str | None = Header(default=None),
) -> dict[str, Any]:
  _admin_auth(authorization)
  total = db.execute(select(func.count()).select_from(Product)).scalar_one()
  active = db.execute(select(func.count()).select_from(Product).where(Product.is_active == 1)).scalar_one()
  with_affiliate = db.execute(
    select(func.count()).select_from(Product).where(Product.affiliate_url.isnot(None), Product.affiliate_url != "")
  ).scalar_one()
  no_image = db.execute(
    select(func.count())
    .select_from(Product)
    .where(or_(Product.image_url.is_(None), Product.image_url == ""))
  ).scalar_one()
  no_link = db.execute(
    select(func.count())
    .select_from(Product)
    .where(
      or_(Product.product_url.is_(None), Product.product_url == ""),
      or_(Product.affiliate_url.is_(None), Product.affiliate_url == ""),
    )
  ).scalar_one()
  no_price = db.execute(select(func.count()).select_from(Product).where(Product.price <= 0)).scalar_one()
  active_no_image = db.execute(
    select(func.count())
    .select_from(Product)
    .where(
      Product.is_active == 1,
      or_(Product.image_url.is_(None), Product.image_url == ""),
    )
  ).scalar_one()
  active_no_link = db.execute(
    select(func.count())
    .select_from(Product)
    .where(
      Product.is_active == 1,
      or_(Product.product_url.is_(None), Product.product_url == ""),
      or_(Product.affiliate_url.is_(None), Product.affiliate_url == ""),
    )
  ).scalar_one()
  by_source_rows = db.execute(
    select(
      Product.source,
      func.count().label("cnt"),
      func.sum(Product.is_active).label("active_cnt"),
    ).group_by(Product.source)
  ).all()
  by_source = [
    {
      "source": str(row[0]),
      "total": int(row[1] or 0),
      "active": int(row[2] or 0),
    }
    for row in by_source_rows
  ]
  return {
    "total_products": int(total or 0),
    "active_products": int(active or 0),
    "with_affiliate_url": int(with_affiliate or 0),
    "without_image": int(no_image or 0),
    "without_shop_link": int(no_link or 0),
    "without_price_or_zero": int(no_price or 0),
    "active_without_image": int(active_no_image or 0),
    "active_without_shop_link": int(active_no_link or 0),
    "by_source": by_source,
  }


@router.post("/demo-seed")
def admin_catalog_demo_seed(
  db: Session = Depends(get_db),
  authorization: str | None = Header(default=None),
) -> dict[str, Any]:
  _admin_auth(authorization)
  if not _allow_demo_catalog_mutations():
    raise HTTPException(
      status_code=403,
      detail="Demo catalog disabled. Set ALLOW_DEMO_CATALOG=1 in backend/.env to enable.",
    )
  now = datetime.now(timezone.utc)
  source = "demo"
  categories = [
    ("футболки", ["white", "black", "navy", "gray"]),
    ("джинсы", ["indigo", "black", "navy"]),
    ("брюки", ["graphite", "black", "navy"]),
    ("куртки", ["black", "navy", "olive"]),
    ("обувь", ["white", "black", "brown"]),
  ]
  sizes = ["XS", "S", "M", "L", "XL"]
  brands = ["BasicLab", "CityWear", "Nord", "Mono", "Everyday"]
  created = 0
  for ci, (cat, cols) in enumerate(categories):
    for i in range(60):
      external_id = f"{cat}-{i}"
      exists = db.execute(
        select(Product).where(Product.external_id == external_id, Product.source == source)
      ).scalar_one_or_none()
      if exists:
        continue
      brand = brands[(i + ci) % len(brands)]
      color = cols[i % len(cols)]
      price = 990 + (i % 15) * 150
      p = Product(
        id=str(uuid4()),
        external_id=external_id,
        source=source,
        title=f"{cat.capitalize()} {brand} #{i+1}",
        brand=brand,
        category=normalize_category(cat),
        subcategory=None,
        price=price,
        currency="RUB",
        image_url=demo_picsum_image_url(external_id=external_id, source=source),
        product_url="https://example.com/product",
        available_sizes=sizes,
        colors=normalize_product_colors([color]),
        fit=None,
        silhouette=None,
        style_tags=["minimal"],
        is_active=1,
        created_at=now,
        updated_at=now,
      )
      db.add(p)
      created += 1
  db.commit()
  return {"created": created}


@router.post("/demo-rewrite-image-urls")
def admin_demo_rewrite_image_urls(
  db: Session = Depends(get_db),
  authorization: str | None = Header(default=None),
) -> dict[str, Any]:
  _admin_auth(authorization)
  if not _allow_demo_catalog_mutations():
    raise HTTPException(
      status_code=403,
      detail="Demo catalog disabled. Set ALLOW_DEMO_CATALOG=1 in backend/.env to enable.",
    )
  rows = db.execute(select(Product).where(Product.source == "demo")).scalars().all()
  updated = 0
  for p in rows:
    p.image_url = demo_picsum_image_url(external_id=p.external_id, source=p.source or "demo")
    p.updated_at = datetime.now(timezone.utc)
    updated += 1
  db.commit()
  return {"updated": updated}


def _mock_lamoda_feed_path() -> Path:
  env = (os.getenv("MOCK_LAMODA_FEED_PATH") or "").strip()
  if env:
    return Path(env)
  backend_root = Path(__file__).resolve().parent.parent.parent
  return backend_root / MOCK_LAMODA_REL


@router.post("/sources/{source_id}/sync-mock-lamoda")
def admin_catalog_sync_mock_lamoda(
  source_id: str,
  db: Session = Depends(get_db),
  authorization: str | None = Header(default=None),
) -> dict[str, Any]:
  """Импорт из test_feeds/mock_lamoda.yml (без сети). Путь: MOCK_LAMODA_FEED_PATH или backend/test_feeds/."""
  _admin_auth(authorization)
  src = db.execute(select(ProductSource).where(ProductSource.id == source_id)).scalar_one_or_none()
  if src is None:
    raise HTTPException(status_code=404, detail="Source not found")
  path = _mock_lamoda_feed_path()
  if not path.is_file():
    raise HTTPException(
      status_code=404,
      detail=f"Mock feed not found: {path}. Создайте файл или задайте MOCK_LAMODA_FEED_PATH.",
    )
  body = path.read_text(encoding="utf-8")
  run = sync_partner_feed_from_text(db, source=src, body=body, parser_url_hint=str(path.name))
  return {**_sync_run_to_api(run), "mock_file": str(path)}


@router.post("/renormalize")
def admin_catalog_renormalize(
  source: str | None = None,
  limit: int | None = None,
  db: Session = Depends(get_db),
  authorization: str | None = Header(default=None),
) -> dict[str, Any]:
  """P2: пересчитать категории, размеры, пол; деактивировать неполные карточки."""
  _admin_auth(authorization)
  from ..services.catalog.renormalize_service import renormalize_catalog

  return renormalize_catalog(db, source=source, limit=limit)


class AdmitadBootstrapBody(BaseModel):
  """Опционально: только указанные code; sync=true — сразу скачать фиды."""

  codes: list[str] | None = Field(
    default=None,
    description="Коды источников, напр. fable, aimclo. Пусто — оба пресета.",
  )
  sync: bool = Field(default=False, description="Сразу POST sync по каждому источнику")


@router.post("/bootstrap-admitad-csv")
def admin_catalog_bootstrap_admitad_csv(
  body: AdmitadBootstrapBody | None = None,
  db: Session = Depends(get_db),
  authorization: str | None = Header(default=None),
) -> dict[str, Any]:
  """
  Admitad export_adv_products (CSV): FABLE (feed_id=25560), Aim Clo (21738).
  Создаёт ProductSource + правила «только одежда» (без аксессуаров/сумок).
  """
  _admin_auth(authorization)
  payload = body or AdmitadBootstrapBody()
  try:
    return bootstrap_admitad_csv_sources(
      db,
      codes=payload.codes,
      sync=payload.sync,
    )
  except ValueError as exc:
    raise HTTPException(status_code=400, detail=str(exc)) from exc


@router.post("/alpha-bootstrap")
def admin_catalog_alpha_bootstrap(
  db: Session = Depends(get_db),
  authorization: str | None = Header(default=None),
) -> dict[str, Any]:
  """
  Подготовка alpha: источник каталога `befree` (создаётся один раз).
  URL фида: `BEFREE_FEED_URL` в окружении или пусто — задайте позже PATCH /sources/{id}.
  """
  _admin_auth(authorization)
  code = "befree"
  feed_url = (os.getenv("BEFREE_FEED_URL") or "").strip() or None
  existing = db.execute(select(ProductSource).where(ProductSource.code == code)).scalar_one_or_none()
  if existing is not None:
    return {
      "ok": True,
      "created": False,
      "source_id": existing.id,
      "code": existing.code,
      "feed_url": existing.feed_url,
      "hint": "Источник уже есть. При необходимости обновите feed_url через PATCH /admin/catalog/sources/{id}.",
    }
  now = datetime.now(timezone.utc)
  s = ProductSource(
    id=str(uuid4()),
    code=code,
    name="Befree",
    network="befree",
    advertiser_id=None,
    feed_url=feed_url,
    deeplink_template=None,
    status="ok" if feed_url else "pending",
    created_at=now,
    updated_at=now,
  )
  db.add(s)
  db.commit()
  return {
    "ok": True,
    "created": True,
    "source_id": s.id,
    "code": code,
    "feed_url": feed_url,
    "next": "POST .../sources/{source_id}/sync или .../sync-upload с XML фида",
  }
