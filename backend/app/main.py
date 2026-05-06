from datetime import datetime, timedelta, timezone
from hashlib import sha256
from typing import Any
from uuid import uuid4

import os

import bcrypt
import jwt
from fastapi import Depends, FastAPI, File, Header, HTTPException, UploadFile, status
from fastapi.security import HTTPAuthorizationCredentials, HTTPBearer
from pydantic import BaseModel, EmailStr, Field
from sqlalchemy import delete, func, select
from sqlalchemy.orm import Session

from . import business
from .catalog_normalize import normalize_category, normalize_product_colors
from .config import get_jwt_expires_hours, get_jwt_secret
from .db import SessionLocal
from .schemas.photo_analysis import build_profile_json_after_analysis
from .schemas.api_product import product_to_api as _product_to_api
from .services.style_analysis_service import analyze_photo_bytes as analyze_photo_ai
from .models import (
  FitProfile,
  MetricEvent,
  Outfit,
  Product,
  RecommendationEventV2,
  Recommendation,
  RecommendationEvent,
  SavedRecommendation,
  StyleProfile,
  TasteProfile,
  UserProductState,
  User,
)
from .services.visual_analysis_service import generate_style_visual
from .services.recommendation_engine import generate_feed, score_product, ensure_taste_profile
from .services.outfit_service import generate_outfits


def _demo_picsum_image_url(*, external_id: str, source: str = "demo") -> str:
  """
  Демо-картинки: только /id/{n}/w/h (стабильный путь без кириллицы и без длинных seed).
  /seed/... с не-ASCII ломался в Flutter; часть /seed/ на Android тоже вела себя нестабильно.
  """
  digest = sha256(f"{source}:{external_id}".encode("utf-8")).hexdigest()
  pic_id = 1 + (int(digest[:8], 16) % 999)
  return f"https://picsum.photos/id/{pic_id}/600/800"


app = FastAPI(title="Modish API", version="0.9.0-pre")

from .api.affiliate import router as affiliate_router  # noqa: E402
from .api.catalog_admin import router as catalog_admin_router  # noqa: E402

app.include_router(affiliate_router)
app.include_router(catalog_admin_router)

auth_scheme = HTTPBearer(auto_error=False)
JWT_SECRET = get_jwt_secret()
JWT_ALG = "HS256"
JWT_EXPIRES_HOURS = get_jwt_expires_hours()

MAX_PHOTO_BYTES = business.MAX_PHOTO_BYTES


class AuthRequest(BaseModel):
  email: EmailStr
  password: str = Field(min_length=8)


class StyleTargetRequest(BaseModel):
  style_target: str = Field(pattern="^(menswear|womenswear|unisex|unknown)$")


class FeedbackRequest(BaseModel):
  event_type: str = Field(pattern="^(like|dislike|save|unsave|view_details)$")


class TokenResponse(BaseModel):
  access_token: str
  token_type: str = "bearer"
  user: dict[str, Any]


class GenerateRequest(BaseModel):
  type: str = Field(default="outfit", pattern="^(outfit)$")
  count: int = Field(default=10, ge=1, le=10)
  scenario: str = Field(default="daily", pattern="^(daily|office|evening)$")


class StyleProfilePatchRequest(BaseModel):
  profile_json: dict[str, Any] | None = None
  style_target: str | None = None


class UserPreferencesPatchRequest(BaseModel):
  height: int = Field(ge=50, le=250)
  weight: int | None = Field(default=None, ge=20, le=400)
  fit_preference: str = Field(pattern="^(slim|regular|oversized)$")


class ProductIn(BaseModel):
  external_id: str
  source: str
  title: str
  brand: str
  category: str
  subcategory: str | None = None
  price: int = Field(ge=0)
  currency: str = "RUB"
  image_url: str
  product_url: str
  available_sizes: list[str] = Field(default_factory=list)
  available_sizes_detailed: list[dict[str, Any]] = Field(default_factory=list)
  size_system: str | None = None
  colors: list[str] = Field(default_factory=list)
  color_family: str | None = None
  material: str | None = None
  season: str | None = None
  occasion: str | None = None
  gender_target: str | None = None
  fit: str | None = None
  silhouette: str | None = None
  style_tags: list[str] = Field(default_factory=list)
  image_quality_score: float | None = None
  is_available: bool = True
  is_active: bool = True


class EventRequest(BaseModel):
  event_type: str = Field(
    pattern="^(view|skip|dislike|like|save|unsave|open_product|buy_click)$"
  )
  product_id: str | None = None
  outfit_id: str | None = None
  meta: dict[str, Any] | None = None


class FitProfilePatchRequest(BaseModel):
  height: int = Field(ge=50, le=250)
  weight: int | None = Field(default=None, ge=20, le=400)
  gender_target: str = Field(pattern="^(menswear|womenswear|unisex)$")
  clothing_size: str = Field(min_length=1, max_length=16)
  budget_min: int = Field(default=0, ge=0)
  budget_max: int = Field(default=10000, ge=0)
  interest_categories: list[str] = Field(default_factory=list, max_length=24)
  style_scenarios: list[str] = Field(default_factory=list, max_length=24)


class TasteProfilePatchRequest(BaseModel):
  price_min: int = Field(default=0, ge=0)
  price_max: int = Field(default=10000, ge=0)
  preferred_fit: str = Field(pattern="^(slim|regular|oversized)$")


class MetricEventRequest(BaseModel):
  name: str = Field(pattern="^[a-z_]{3,64}$")
  meta: dict[str, Any] | None = None


def get_db():
  db = SessionLocal()
  try:
    yield db
  finally:
    db.close()


def _hash(password: str) -> str:
  raw = password.encode("utf-8")
  if len(raw) > 72:
    raise HTTPException(status_code=400, detail="Пароль слишком длинный (bcrypt: макс. 72 байта)")
  return bcrypt.hashpw(raw, bcrypt.gensalt()).decode("ascii")


def _verify_password(plain: str, hashed: str) -> bool:
  try:
    return bcrypt.checkpw(plain.encode("utf-8"), hashed.encode("ascii"))
  except (ValueError, TypeError):
    return False


def _token(user_id: str) -> str:
  now = datetime.now(timezone.utc)
  payload = {
    "sub": user_id,
    "iat": int(now.timestamp()),
    "exp": int((now + timedelta(hours=JWT_EXPIRES_HOURS)).timestamp()),
  }
  return jwt.encode(payload, JWT_SECRET, algorithm=JWT_ALG)


def _user_from_token(
  credentials: HTTPAuthorizationCredentials | None,
  db: Session,
) -> User:
  if credentials is None or credentials.scheme.lower() != "bearer":
    raise HTTPException(
      status_code=status.HTTP_401_UNAUTHORIZED,
      detail="Missing bearer token",
    )
  try:
    payload = jwt.decode(credentials.credentials, JWT_SECRET, algorithms=[JWT_ALG])
  except jwt.PyJWTError as exc:
    raise HTTPException(
      status_code=status.HTTP_401_UNAUTHORIZED,
      detail="Invalid token",
    ) from exc
  user_id = payload.get("sub")
  if not user_id:
    raise HTTPException(
      status_code=status.HTTP_401_UNAUTHORIZED,
      detail="Invalid token payload",
    )
  user = db.execute(select(User).where(User.id == user_id)).scalar_one_or_none()
  if user is None:
    raise HTTPException(
      status_code=status.HTTP_401_UNAUTHORIZED,
      detail="User not found",
    )
  return user


def _as_user_payload(user: User) -> dict[str, Any]:
  return {
    "id": user.id,
    "email": user.email,
    "plan": user.plan,
    "subscription_status": user.subscription_status,
    "height_cm": user.height_cm,
    "weight_kg": user.weight_kg,
    "fit_preference": user.fit_preference,
    "trial_started_at": user.trial_started_at.isoformat(),
    "trial_ends_at": user.trial_ends_at.isoformat(),
  }


def _norm_fit_tag_list(raw: list[str], *, max_items: int = 24) -> list[str]:
  out: list[str] = []
  for x in raw[:max_items]:
    s = str(x).strip()[:64]
    if s and s not in out:
      out.append(s)
  return out


def _fit_to_api(fp: FitProfile) -> dict[str, Any]:
  return {
    "height": fp.height_cm,
    "weight": fp.weight_kg,
    "gender_target": fp.gender_target,
    "clothing_size": fp.clothing_size,
    "body_proportions": fp.body_proportions,
    "contrast_level": fp.contrast_level,
    "color_palette": fp.color_palette or [],
    "avoid_colors": fp.avoid_colors or [],
    "recommended_silhouettes": fp.recommended_silhouettes or [],
    "avoid_silhouettes": fp.avoid_silhouettes or [],
    "recommended_fit": fp.recommended_fit,
    "avoid_fit": fp.avoid_fit or [],
    "style_constraints": fp.style_constraints or {},
    "interest_categories": fp.interest_categories or [],
    "style_scenarios": fp.style_scenarios or [],
    "budget_min": fp.budget_min,
    "budget_max": fp.budget_max,
    "updated_at": fp.updated_at.isoformat(),
  }


def _taste_to_api(tp: TasteProfile) -> dict[str, Any]:
  def top_keys(d: dict[str, Any], *, sign: int, limit: int = 12) -> list[str]:
    items: list[tuple[str, int]] = []
    for k, v in (d or {}).items():
      try:
        iv = int(v)
      except Exception:
        continue
      if sign > 0 and iv > 0:
        items.append((str(k), iv))
      if sign < 0 and iv < 0:
        items.append((str(k), iv))
    items.sort(key=lambda x: abs(x[1]), reverse=True)
    return [k for k, _ in items[:limit]]

  return {
    "liked_categories": top_keys(tp.category_weights or {}, sign=+1) or (tp.liked_categories or []),
    "disliked_categories": top_keys(tp.category_weights or {}, sign=-1) or (tp.disliked_categories or []),
    "liked_colors": top_keys(tp.color_weights or {}, sign=+1) or (tp.liked_colors or []),
    "disliked_colors": top_keys(tp.color_weights or {}, sign=-1) or (tp.disliked_colors or []),
    "liked_brands": top_keys(tp.brand_weights or {}, sign=+1) or (tp.liked_brands or []),
    "disliked_brands": top_keys(tp.brand_weights or {}, sign=-1) or (tp.disliked_brands or []),
    "liked_styles": top_keys(tp.style_weights or {}, sign=+1) or (tp.liked_styles or []),
    "disliked_styles": top_keys(tp.style_weights or {}, sign=-1) or (tp.disliked_styles or []),
    "weights": {
      "categories": tp.category_weights or {},
      "brands": tp.brand_weights or {},
      "colors": tp.color_weights or {},
      "styles": tp.style_weights or {},
    },
    "price_range": {"min": tp.price_min, "max": tp.price_max},
    "preferred_fit": tp.preferred_fit,
    "updated_at": tp.updated_at.isoformat(),
  }


def _outfit_to_api(o: Outfit, products: dict[str, Any] | None = None) -> dict[str, Any]:
  items = dict(o.items_json or {})
  return {
    "id": o.id,
    "items": items,
    "products": products or {},
    "total_price": o.total_price,
    "style_direction": o.style_direction,
    "reason": o.reason,
    "score": o.score,
    "is_saved": bool(o.is_saved),
    "created_at": o.created_at.isoformat(),
  }


def _rec_to_dict(item: Recommendation) -> dict[str, Any]:
  return {
    "id": item.id,
    "type": item.type,
    "title": item.title,
    "description": item.description,
    "content_json": item.content_json,
    "tags_json": item.tags_json,
    "status": item.status,
    "created_at": item.created_at.isoformat(),
  }


def _seed_recommendations(db: Session, user_id: str) -> None:
  existing = db.execute(
    select(Recommendation).where(Recommendation.user_id == user_id)
  ).scalars().first()
  if existing:
    return
  base = Recommendation(
    id=str(uuid4()),
    user_id=user_id,
    type="outfit",
    title="Образ на каждый день",
    description="Спокойный минималистичный образ для повседневного использования.",
    content_json={
      "items": {
        "top": "белый лонгслив",
        "bottom": "прямые тёмные джинсы",
        "layer": "серый жакет",
        "shoes": "белые кроссовки",
      },
      "why_it_fits": ["чистый силуэт", "легко повторить", "спокойный контраст"],
      "alternatives": [
        "лонгслив можно заменить на белую футболку",
        "кроссовки можно заменить на лоферы",
      ],
    },
    tags_json={
      "styles": ["minimal", "smart_casual"],
      "colors": ["white", "navy", "gray"],
      "silhouettes": ["straight", "structured"],
      "occasion": ["daily"],
      "item_types": ["longsleeve", "jeans", "jacket", "sneakers"],
    },
    status="active",
  )
  db.add(base)
  db.commit()


async def _read_upload_bytes_limited(photo: UploadFile, max_bytes: int) -> bytes:
  total = 0
  buf = bytearray()
  while True:
    chunk = await photo.read(65536)
    if not chunk:
      break
    total += len(chunk)
    if total > max_bytes:
      raise HTTPException(status_code=400, detail="Файл больше 5 MB")
    buf.extend(chunk)
  return bytes(buf)


@app.get("/health")
def health() -> dict[str, str]:
  return {"status": "ok"}


@app.post("/auth/register", response_model=TokenResponse)
def register(payload: AuthRequest, db: Session = Depends(get_db)) -> TokenResponse:
  existing = db.execute(select(User).where(User.email == payload.email)).scalar_one_or_none()
  if existing:
    raise HTTPException(status_code=409, detail="Email уже зарегистрирован")
  now = datetime.now(timezone.utc)
  user = User(
    id=str(uuid4()),
    email=payload.email,
    password_hash=_hash(payload.password),
    trial_started_at=now,
    trial_ends_at=now + timedelta(days=14),
    plan="plus",
    subscription_status="trial",
    height_cm=170,
    weight_kg=None,
    fit_preference="regular",
  )
  db.add(user)
  db.flush()
  db.add(
    StyleProfile(
      id=str(uuid4()),
      user_id=user.id,
      style_target="unknown",
      confidence_score=0.5,
      profile_json={},
    )
  )
  db.commit()
  business.get_or_refresh_limits(db, user.id)
  db.commit()
  _seed_recommendations(db, user.id)
  business.rebuild_user_summary(db, user.id)
  db.commit()
  return TokenResponse(access_token=_token(user.id), user=_as_user_payload(user))


@app.post("/auth/login", response_model=TokenResponse)
def login(payload: AuthRequest, db: Session = Depends(get_db)) -> TokenResponse:
  user = db.execute(select(User).where(User.email == payload.email)).scalar_one_or_none()
  if user is None or not _verify_password(payload.password, user.password_hash):
    raise HTTPException(status_code=401, detail="Неверный email или пароль")
  business.get_or_refresh_limits(db, user.id)
  db.commit()
  _seed_recommendations(db, user.id)
  return TokenResponse(access_token=_token(user.id), user=_as_user_payload(user))


@app.get("/users/me")
def users_me(
  db: Session = Depends(get_db),
  credentials: HTTPAuthorizationCredentials | None = Depends(auth_scheme),
) -> dict[str, Any]:
  user = _user_from_token(credentials, db)
  return _as_user_payload(user)


@app.get("/products")
def products_list(
  limit: int = 50,
  offset: int = 0,
  category: str | None = None,
  db: Session = Depends(get_db),
) -> list[dict[str, Any]]:
  q = select(Product).where(Product.is_active == 1)
  if category:
    q = q.where(Product.category == category)
  q = q.order_by(Product.created_at.desc()).offset(max(0, offset)).limit(min(200, max(1, limit)))
  rows = db.execute(q).scalars().all()
  return [_product_to_api(p) for p in rows]


@app.get("/products/{product_id}")
def products_get(product_id: str, db: Session = Depends(get_db)) -> dict[str, Any]:
  p = db.execute(select(Product).where(Product.id == product_id)).scalar_one_or_none()
  if p is None:
    raise HTTPException(status_code=404, detail="Product not found")
  return _product_to_api(p)


@app.post("/admin/products/import")
def admin_products_import(
  items: list[ProductIn],
  db: Session = Depends(get_db),
  authorization: str | None = Header(default=None),
) -> dict[str, Any]:
  admin_token = (os.getenv("ADMIN_TOKEN") or "").strip()
  if not admin_token:
    raise HTTPException(status_code=503, detail="ADMIN_TOKEN is not configured")
  if authorization != f"Bearer {admin_token}":
    raise HTTPException(status_code=401, detail="Admin token required")
  now = datetime.now(timezone.utc)
  created = 0
  updated = 0
  for it in items:
    existing = db.execute(
      select(Product).where(Product.external_id == it.external_id, Product.source == it.source)
    ).scalar_one_or_none()
    payload = it.model_dump()
    if existing is None:
      p = Product(
        id=str(uuid4()),
        external_id=payload["external_id"],
        source=payload["source"],
        title=payload["title"],
        brand=payload["brand"],
        category=normalize_category(payload["category"]),
        subcategory=payload.get("subcategory"),
        price=int(payload["price"]),
        currency=payload.get("currency") or "RUB",
        image_url=payload["image_url"],
        product_url=payload["product_url"],
        available_sizes=payload.get("available_sizes") or [],
        available_sizes_detailed=payload.get("available_sizes_detailed") or [],
        size_system=payload.get("size_system"),
        colors=normalize_product_colors(payload.get("colors") or []),
        color_family=payload.get("color_family"),
        material=payload.get("material"),
        season=payload.get("season"),
        occasion=payload.get("occasion"),
        gender_target=payload.get("gender_target"),
        fit=payload.get("fit"),
        silhouette=payload.get("silhouette"),
        style_tags=payload.get("style_tags") or [],
        image_quality_score=payload.get("image_quality_score"),
        is_available=1 if payload.get("is_available", True) else 0,
        last_checked_at=now,
        is_active=1 if payload.get("is_active", True) else 0,
        created_at=now,
        updated_at=now,
      )
      db.add(p)
      created += 1
    else:
      existing.title = payload["title"]
      existing.brand = payload["brand"]
      existing.category = normalize_category(payload["category"])
      existing.subcategory = payload.get("subcategory")
      existing.price = int(payload["price"])
      existing.currency = payload.get("currency") or "RUB"
      existing.image_url = payload["image_url"]
      existing.product_url = payload["product_url"]
      existing.available_sizes = payload.get("available_sizes") or []
      existing.available_sizes_detailed = payload.get("available_sizes_detailed") or []
      existing.size_system = payload.get("size_system")
      existing.colors = normalize_product_colors(payload.get("colors") or [])
      existing.color_family = payload.get("color_family")
      existing.material = payload.get("material")
      existing.season = payload.get("season")
      existing.occasion = payload.get("occasion")
      existing.gender_target = payload.get("gender_target")
      existing.fit = payload.get("fit")
      existing.silhouette = payload.get("silhouette")
      existing.style_tags = payload.get("style_tags") or []
      existing.image_quality_score = payload.get("image_quality_score")
      existing.is_available = 1 if payload.get("is_available", True) else 0
      existing.last_checked_at = now
      existing.is_active = 1 if payload.get("is_active", True) else 0
      existing.updated_at = now
      updated += 1
  db.commit()
  return {"created": created, "updated": updated}


@app.post("/admin/catalog/demo-seed")
def admin_catalog_demo_seed(
  db: Session = Depends(get_db),
  authorization: str | None = Header(default=None),
) -> dict[str, Any]:
  """
  Быстрый seed каталога для демо (100–300 товаров).
  В проде: удалить или защитить админ-ролью.
  """
  now = datetime.now(timezone.utc)
  admin_token = (os.getenv("ADMIN_TOKEN") or "").strip()
  if not admin_token:
    raise HTTPException(status_code=503, detail="ADMIN_TOKEN is not configured")
  if authorization != f"Bearer {admin_token}":
    raise HTTPException(status_code=401, detail="Admin token required")
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
    for i in range(60):  # 5*60=300
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
        image_url=_demo_picsum_image_url(external_id=external_id, source=source),
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


@app.post("/admin/catalog/demo-rewrite-image-urls")
def admin_demo_rewrite_image_urls(
  db: Session = Depends(get_db),
  authorization: str | None = Header(default=None),
) -> dict[str, Any]:
  """
  Переписывает image_url у уже созданных demo-товаров на ASCII-only Picsum seed
  (после старых сидов с кириллицей в пути URL картинки в приложении не открывались).
  """
  admin_token = (os.getenv("ADMIN_TOKEN") or "").strip()
  if not admin_token:
    raise HTTPException(status_code=503, detail="ADMIN_TOKEN is not configured")
  if authorization != f"Bearer {admin_token}":
    raise HTTPException(status_code=401, detail="Admin token required")
  rows = db.execute(select(Product).where(Product.source == "demo")).scalars().all()
  updated = 0
  for p in rows:
    p.image_url = _demo_picsum_image_url(external_id=p.external_id, source=p.source or "demo")
    p.updated_at = datetime.now(timezone.utc)
    updated += 1
  db.commit()
  return {"updated": updated}


def _feed_scored_items(
  db: Session,
  user: User,
  *,
  limit: int,
) -> list[dict[str, Any]]:
  now = datetime.now(timezone.utc)
  hidden_ids = set(
    db.execute(
      select(UserProductState.product_id).where(
        UserProductState.user_id == user.id,
        UserProductState.hidden_until.is_not(None),
        UserProductState.hidden_until > now,
      )
    ).scalars()
  )
  scored = generate_feed(db, user, limit=limit, exclude_product_ids=set(map(str, hidden_ids)))
  out: list[dict[str, Any]] = []
  for s in scored:
    out.append(
      {
        "product": _product_to_api(s.product),
        "final_score": s.final_score,
        "breakdown": s.breakdown,
        "reason": s.reason,
        "reasons": s.reasons,
      }
    )
  return out


@app.get("/feed")
def feed(
  limit: int = 30,
  db: Session = Depends(get_db),
  credentials: HTTPAuthorizationCredentials | None = Depends(auth_scheme),
) -> list[dict[str, Any]]:
  user = _user_from_token(credentials, db)
  return _feed_scored_items(db, user, limit=limit)


@app.get("/products/recommended")
def products_recommended(
  limit: int = 30,
  db: Session = Depends(get_db),
  credentials: HTTPAuthorizationCredentials | None = Depends(auth_scheme),
) -> list[dict[str, Any]]:
  user = _user_from_token(credentials, db)
  return _feed_scored_items(db, user, limit=limit)


@app.post("/recommendations/events")
def recommendations_events(
  payload: EventRequest,
  db: Session = Depends(get_db),
  credentials: HTTPAuthorizationCredentials | None = Depends(auth_scheme),
) -> dict[str, Any]:
  user = _user_from_token(credentials, db)
  weights = {
    "view": 0,
    "skip": -1,
    "dislike": -3,
    "like": 2,
    "save": 4,
    "unsave": -4,
    "open_product": 5,
    "buy_click": 8,
  }
  weight = weights[payload.event_type]
  if payload.product_id is None and payload.outfit_id is None:
    raise HTTPException(status_code=400, detail="product_id or outfit_id required")

  # Update user->product state (soft hiding rules)
  if payload.product_id:
    now = datetime.now(timezone.utc)
    st = db.execute(
      select(UserProductState).where(
        UserProductState.user_id == user.id,
        UserProductState.product_id == payload.product_id,
      )
    ).scalar_one_or_none()
    if st is None:
      st = UserProductState(
        id=str(uuid4()),
        user_id=user.id,
        product_id=payload.product_id,
        hidden_until=None,
        last_seen_at=None,
        event_strength=0,
        created_at=now,
        updated_at=now,
      )
      db.add(st)
      db.flush()
    st.last_seen_at = now if payload.event_type in ("view", "open_product") else st.last_seen_at
    st.event_strength = int(st.event_strength or 0) + int(weight or 0)

    # Hide rules per spec:
    # view/open_product -> do not hide
    # skip -> hide temporarily
    # dislike -> hide long
    # like -> do not hide (but avoid immediate repeat)
    # save/buy_click -> remove from main feed
    if payload.event_type == "skip":
      st.hidden_until = now + timedelta(hours=24)
    elif payload.event_type == "dislike":
      st.hidden_until = now + timedelta(days=30)
    elif payload.event_type == "like":
      st.hidden_until = now + timedelta(hours=6)
    elif payload.event_type == "save":
      st.hidden_until = now + timedelta(days=3650)
    elif payload.event_type == "buy_click":
      st.hidden_until = now + timedelta(days=3650)
    elif payload.event_type == "unsave":
      # allow back to feed after short cooldown
      st.hidden_until = now + timedelta(hours=6)
    st.updated_at = now

  # Idempotency for save/unsave on products
  if payload.product_id and payload.event_type in ("save", "unsave"):
    last = db.execute(
      select(RecommendationEventV2.event_type)
      .where(
        RecommendationEventV2.user_id == user.id,
        RecommendationEventV2.product_id == payload.product_id,
        RecommendationEventV2.event_type.in_(["save", "unsave"]),
      )
      .order_by(RecommendationEventV2.created_at.desc())
      .limit(1)
    ).scalar_one_or_none()
    if last == payload.event_type:
      # no-op
      return {
        "status": "ok",
        "event_type": payload.event_type,
        "weight": 0,
        "total_events": int(
          db.execute(
            select(func.count())
            .select_from(RecommendationEventV2)
            .where(RecommendationEventV2.user_id == user.id)
          ).scalar_one()
          or 0
        ),
        "milestone_reached": False,
        "outfits_generated": 0,
        "idempotent": True,
      }

  # Save event
  ev = RecommendationEventV2(
    id=str(uuid4()),
    user_id=user.id,
    product_id=payload.product_id,
    outfit_id=payload.outfit_id,
    event_type=payload.event_type,
    event_weight=weight,
    meta_json=payload.meta or {},
    created_at=datetime.now(timezone.utc),
  )
  db.add(ev)

  # Update TasteProfile (very first iteration)
  tp = ensure_taste_profile(db, user.id)
  if payload.product_id:
    p = db.execute(select(Product).where(Product.id == payload.product_id)).scalar_one_or_none()
    if p:
      cat = (p.category or "").strip().lower()
      brand = (p.brand or "").strip().lower()
      cols = [str(c).strip().lower() for c in (p.colors or []) if str(c).strip()]
      styles = [str(t).strip().lower() for t in (p.style_tags or []) if str(t).strip()]

      def add_unique(lst: list[str], v: str) -> None:
        if v and v not in lst:
          lst.append(v)

      def bump(d: dict, key: str, delta: int) -> None:
        if not key:
          return
        cur = d.get(key, 0)
        try:
          cur = int(cur)
        except Exception:
          cur = 0
        nxt = cur + int(delta)
        # clamp to keep weights stable
        if nxt > 50:
          nxt = 50
        if nxt < -50:
          nxt = -50
        d[key] = nxt

      def prune(d: dict, limit: int = 200) -> None:
        if not isinstance(d, dict) or len(d) <= limit:
          return
        # keep keys with highest absolute weights
        items: list[tuple[str, int]] = []
        for k, v in d.items():
          try:
            items.append((str(k), int(v)))
          except Exception:
            continue
        items.sort(key=lambda x: abs(x[1]), reverse=True)
        keep = {k for k, _ in items[:limit]}
        drop = [k for k in list(d.keys()) if str(k) not in keep]
        for k in drop:
          d.pop(k, None)

      if payload.event_type in ("like", "save", "open_product", "buy_click"):
        add_unique(tp.liked_categories, cat)
        add_unique(tp.liked_brands, brand)
        for c in cols[:3]:
          add_unique(tp.liked_colors, c)
        for t in styles[:3]:
          add_unique(tp.liked_styles, t)
      elif payload.event_type in ("dislike", "skip"):
        add_unique(tp.disliked_categories, cat)
        add_unique(tp.disliked_brands, brand)
        for c in cols[:3]:
          add_unique(tp.disliked_colors, c)
        for t in styles[:3]:
          add_unique(tp.disliked_styles, t)

      # Weighted learning (new ядро)
      delta = weight
      bump(tp.category_weights, cat, delta)
      bump(tp.brand_weights, brand, delta)
      for c in cols[:3]:
        bump(tp.color_weights, c, delta)
      for t in styles[:3]:
        bump(tp.style_weights, t, delta)
      prune(tp.category_weights)
      prune(tp.brand_weights)
      prune(tp.color_weights)
      prune(tp.style_weights)
      tp.updated_at = datetime.now(timezone.utc)

  db.commit()
  # Milestone: after 20–30 actions, generate 3 outfits once
  total = db.execute(
    select(func.count()).select_from(RecommendationEventV2).where(RecommendationEventV2.user_id == user.id)
  ).scalar_one()
  total = int(total or 0)
  milestone = total in (20, 30)
  generated = 0
  if milestone:
    existing = db.execute(select(Outfit).where(Outfit.user_id == user.id)).scalars().first()
    if existing is None:
      items = generate_outfits(db, user, count=3)
      db.commit()
      generated = len(items)
  return {
    "status": "ok",
    "event_type": payload.event_type,
    "weight": weight,
    "total_events": total,
    "milestone_reached": milestone,
    "outfits_generated": generated,
  }


@app.get("/profile/brief")
def profile_brief(
  db: Session = Depends(get_db),
  credentials: HTTPAuthorizationCredentials | None = Depends(auth_scheme),
) -> dict[str, Any]:
  user = _user_from_token(credentials, db)
  fp = db.execute(select(FitProfile).where(FitProfile.user_id == user.id)).scalar_one_or_none()
  tp = ensure_taste_profile(db, user.id)
  events = db.execute(
    select(func.count()).select_from(RecommendationEventV2).where(RecommendationEventV2.user_id == user.id)
  ).scalar_one()
  events = int(events or 0)
  saved = db.execute(
    select(func.count()).select_from(RecommendationEventV2).where(
      RecommendationEventV2.user_id == user.id,
      RecommendationEventV2.event_type == "save",
    )
  ).scalar_one()
  saved = int(saved or 0)
  return {
    "fit_profile": _fit_to_api(fp) if fp else None,
    "taste_profile": _taste_to_api(tp),
    "stats": {"events": events, "saved": saved},
    "next": "keep_swiping" if events < 20 else "check_outfits",
  }


@app.post("/metrics/events")
def metrics_events(
  payload: MetricEventRequest,
  db: Session = Depends(get_db),
  credentials: HTTPAuthorizationCredentials | None = Depends(auth_scheme),
) -> dict[str, Any]:
  user = _user_from_token(credentials, db)
  ev = MetricEvent(
    id=str(uuid4()),
    user_id=user.id,
    name=payload.name,
    meta_json=payload.meta or {},
    created_at=datetime.now(timezone.utc),
  )
  db.add(ev)
  db.commit()
  return {"status": "ok"}


@app.get("/saved-products")
def saved_products(
  limit: int = 50,
  db: Session = Depends(get_db),
  credentials: HTTPAuthorizationCredentials | None = Depends(auth_scheme),
) -> list[dict[str, Any]]:
  user = _user_from_token(credentials, db)
  rows = db.execute(
    select(RecommendationEventV2, Product)
    .join(Product, Product.id == RecommendationEventV2.product_id)
    .where(
      RecommendationEventV2.user_id == user.id,
      RecommendationEventV2.event_type.in_(["save", "unsave"]),
      RecommendationEventV2.product_id.is_not(None),
    )
    .order_by(RecommendationEventV2.created_at.desc())
    .limit(min(200, max(1, limit)))
  ).all()
  seen: set[str] = set()
  out: list[dict[str, Any]] = []
  for ev, p in rows:
    if p.id in seen:
      continue
    seen.add(p.id)
    # newest event wins; include only currently saved
    if ev.event_type == "save":
      out.append(
        {
          "saved_at": ev.created_at.isoformat(),
          "product": _product_to_api(p),
        }
      )
  return out


@app.patch("/users/me/preferences")
def users_me_preferences_patch(
  payload: UserPreferencesPatchRequest,
  db: Session = Depends(get_db),
  credentials: HTTPAuthorizationCredentials | None = Depends(auth_scheme),
) -> dict[str, Any]:
  user = _user_from_token(credentials, db)
  user.height_cm = int(payload.height)
  user.weight_kg = int(payload.weight) if payload.weight is not None else None
  user.fit_preference = payload.fit_preference
  user.updated_at = datetime.now(timezone.utc)
  db.commit()
  return _as_user_payload(user)


@app.get("/fit-profile/me")
def fit_profile_me(
  db: Session = Depends(get_db),
  credentials: HTTPAuthorizationCredentials | None = Depends(auth_scheme),
) -> dict[str, Any]:
  user = _user_from_token(credentials, db)
  fp = db.execute(select(FitProfile).where(FitProfile.user_id == user.id)).scalar_one_or_none()
  if fp is None:
    now = datetime.now(timezone.utc)
    fp = FitProfile(
      id=str(uuid4()),
      user_id=user.id,
      height_cm=user.height_cm,
      weight_kg=user.weight_kg,
      gender_target="unisex",
      clothing_size="M",
      body_proportions="",
      contrast_level="",
      color_palette=[],
      avoid_colors=[],
      recommended_silhouettes=[],
      avoid_silhouettes=[],
      recommended_fit=user.fit_preference,
      avoid_fit=[],
      style_constraints={},
      interest_categories=[],
      style_scenarios=[],
      budget_min=0,
      budget_max=10000,
      created_at=now,
      updated_at=now,
    )
    db.add(fp)
    db.commit()
  return _fit_to_api(fp)


@app.patch("/fit-profile/me")
def fit_profile_patch(
  payload: FitProfilePatchRequest,
  db: Session = Depends(get_db),
  credentials: HTTPAuthorizationCredentials | None = Depends(auth_scheme),
) -> dict[str, Any]:
  user = _user_from_token(credentials, db)
  fp = db.execute(select(FitProfile).where(FitProfile.user_id == user.id)).scalar_one_or_none()
  now = datetime.now(timezone.utc)
  if fp is None:
    fp = FitProfile(
      id=str(uuid4()),
      user_id=user.id,
      height_cm=payload.height,
      weight_kg=payload.weight,
      gender_target=payload.gender_target,
      clothing_size=payload.clothing_size,
      body_proportions="",
      contrast_level="",
      color_palette=[],
      avoid_colors=[],
      recommended_silhouettes=[],
      avoid_silhouettes=[],
      recommended_fit=user.fit_preference,
      avoid_fit=[],
      style_constraints={},
      interest_categories=_norm_fit_tag_list(payload.interest_categories),
      style_scenarios=_norm_fit_tag_list(payload.style_scenarios),
      budget_min=payload.budget_min,
      budget_max=payload.budget_max,
      created_at=now,
      updated_at=now,
    )
    db.add(fp)
  else:
    fp.height_cm = payload.height
    fp.weight_kg = payload.weight
    fp.gender_target = payload.gender_target
    fp.clothing_size = payload.clothing_size
    fp.budget_min = payload.budget_min
    fp.budget_max = payload.budget_max
    fp.interest_categories = _norm_fit_tag_list(payload.interest_categories)
    fp.style_scenarios = _norm_fit_tag_list(payload.style_scenarios)
    fp.updated_at = now
  db.commit()
  return _fit_to_api(fp)


@app.get("/taste-profile/me")
def taste_profile_me(
  db: Session = Depends(get_db),
  credentials: HTTPAuthorizationCredentials | None = Depends(auth_scheme),
) -> dict[str, Any]:
  user = _user_from_token(credentials, db)
  tp = ensure_taste_profile(db, user.id)
  db.commit()
  return _taste_to_api(tp)


@app.patch("/taste-profile/me")
def taste_profile_patch(
  payload: TasteProfilePatchRequest,
  db: Session = Depends(get_db),
  credentials: HTTPAuthorizationCredentials | None = Depends(auth_scheme),
) -> dict[str, Any]:
  user = _user_from_token(credentials, db)
  tp = ensure_taste_profile(db, user.id)
  tp.price_min = payload.price_min
  tp.price_max = payload.price_max
  tp.preferred_fit = payload.preferred_fit
  tp.updated_at = datetime.now(timezone.utc)
  db.commit()
  return _taste_to_api(tp)


@app.post("/outfits/generate")
def outfits_generate(
  count: int = 3,
  db: Session = Depends(get_db),
  credentials: HTTPAuthorizationCredentials | None = Depends(auth_scheme),
) -> list[dict[str, Any]]:
  user = _user_from_token(credentials, db)
  items = generate_outfits(db, user, count=count)
  db.commit()
  out: list[dict[str, Any]] = []
  for o in items:
    products: dict[str, Any] = {}
    for slot, pid in (o.items_json or {}).items():
      p = db.execute(select(Product).where(Product.id == str(pid))).scalar_one_or_none()
      if p is not None:
        products[str(slot)] = _product_to_api(p)
    out.append(_outfit_to_api(o, products))
  return out


@app.get("/outfits")
def outfits_list(
  db: Session = Depends(get_db),
  credentials: HTTPAuthorizationCredentials | None = Depends(auth_scheme),
) -> list[dict[str, Any]]:
  user = _user_from_token(credentials, db)
  rows = db.execute(
    select(Outfit).where(Outfit.user_id == user.id).order_by(Outfit.created_at.desc()).limit(50)
  ).scalars().all()
  out: list[dict[str, Any]] = []
  for o in rows:
    products: dict[str, Any] = {}
    for slot, pid in (o.items_json or {}).items():
      p = db.execute(select(Product).where(Product.id == str(pid))).scalar_one_or_none()
      if p is not None:
        products[str(slot)] = _product_to_api(p)
    out.append(_outfit_to_api(o, products))
  return out


@app.post("/outfits/save")
def outfits_save(
  outfit_id: str,
  db: Session = Depends(get_db),
  credentials: HTTPAuthorizationCredentials | None = Depends(auth_scheme),
) -> dict[str, Any]:
  user = _user_from_token(credentials, db)
  o = db.execute(
    select(Outfit).where(Outfit.id == outfit_id, Outfit.user_id == user.id)
  ).scalar_one_or_none()
  if o is None:
    raise HTTPException(status_code=404, detail="Outfit not found")
  o.is_saved = 1
  o.updated_at = datetime.now(timezone.utc)
  db.commit()
  return {"status": "ok", "outfit_id": outfit_id}


@app.get("/billing/status")
def billing_status(
  db: Session = Depends(get_db),
  credentials: HTTPAuthorizationCredentials | None = Depends(auth_scheme),
) -> dict[str, Any]:
  user = _user_from_token(credentials, db)
  return business.billing_status_payload(db, user)


@app.get("/style-profile/me")
def style_profile_me(
  db: Session = Depends(get_db),
  credentials: HTTPAuthorizationCredentials | None = Depends(auth_scheme),
) -> dict[str, Any]:
  user = _user_from_token(credentials, db)
  profile = db.execute(select(StyleProfile).where(StyleProfile.user_id == user.id)).scalar_one()
  return {
    "id": profile.id,
    "user_id": profile.user_id,
    "style_target": profile.style_target,
    "confidence_score": profile.confidence_score,
    "profile_json": profile.profile_json,
    "updated_at": profile.updated_at.isoformat(),
  }


@app.patch("/style-profile/me")
def style_profile_patch(
  payload: StyleProfilePatchRequest,
  db: Session = Depends(get_db),
  credentials: HTTPAuthorizationCredentials | None = Depends(auth_scheme),
) -> dict[str, Any]:
  user = _user_from_token(credentials, db)
  profile = db.execute(select(StyleProfile).where(StyleProfile.user_id == user.id)).scalar_one()
  if payload.style_target is not None:
    if payload.style_target not in {"menswear", "womenswear", "unisex", "unknown"}:
      raise HTTPException(status_code=400, detail="Некорректный style_target")
    profile.style_target = payload.style_target
  if payload.profile_json is not None:
    merged = dict(profile.profile_json or {})
    merged.update(payload.profile_json)
    profile.profile_json = merged
  profile.updated_at = datetime.now(timezone.utc)
  db.commit()
  business.rebuild_user_summary(db, user.id)
  db.commit()
  return {
    "id": profile.id,
    "user_id": profile.user_id,
    "style_target": profile.style_target,
    "confidence_score": profile.confidence_score,
    "profile_json": profile.profile_json,
  }


@app.patch("/style-profile/target")
def patch_style_target(
  payload: StyleTargetRequest,
  db: Session = Depends(get_db),
  credentials: HTTPAuthorizationCredentials | None = Depends(auth_scheme),
) -> dict[str, Any]:
  user = _user_from_token(credentials, db)
  profile = db.execute(select(StyleProfile).where(StyleProfile.user_id == user.id)).scalar_one()
  profile.style_target = payload.style_target
  profile.updated_at = datetime.now(timezone.utc)
  db.commit()
  business.rebuild_user_summary(db, user.id)
  db.commit()
  return {
    "id": profile.id,
    "user_id": profile.user_id,
    "style_target": profile.style_target,
    "confidence_score": profile.confidence_score,
    "profile_json": profile.profile_json,
  }


@app.post("/style-profile/analyze")
async def analyze(
  photo: UploadFile = File(...),
  db: Session = Depends(get_db),
  credentials: HTTPAuthorizationCredentials | None = Depends(auth_scheme),
) -> dict[str, Any]:
  ct = (photo.content_type or "").lower()
  if ct not in {"image/jpeg", "image/jpg", "image/png", "image/webp"}:
    raise HTTPException(status_code=400, detail="Допустимы jpg, jpeg, png, webp")
  user = _user_from_token(credentials, db)
  try:
    limits = business.assert_can_analyze(db, user)
  except PermissionError as e:
    raise HTTPException(status_code=403, detail=str(e)) from e
  raw = await _read_upload_bytes_limited(photo, MAX_PHOTO_BYTES)
  profile = db.execute(select(StyleProfile).where(StyleProfile.user_id == user.id)).scalar_one()
  try:
    analysis = await analyze_photo_ai(content_type=ct, raw=raw)
  except RuntimeError as e:
    raise HTTPException(status_code=502, detail=str(e)) from e
  profile.profile_json = build_profile_json_after_analysis(
    analysis,
    source="photo_analysis_v2_openai",
  )
  profile.confidence_score = 0.75
  profile.updated_at = datetime.now(timezone.utc)
  limits.photo_analysis_used += 1
  limits.updated_at = datetime.now(timezone.utc)
  db.commit()
  business.rebuild_user_summary(db, user.id)
  db.commit()
  return {
    "id": profile.id,
    "user_id": profile.user_id,
    "style_target": profile.style_target,
    "confidence_score": profile.confidence_score,
    "profile_json": profile.profile_json,
  }


@app.post("/recommendations/generate")
def recommendations_generate(
  payload: GenerateRequest,
  db: Session = Depends(get_db),
  credentials: HTTPAuthorizationCredentials | None = Depends(auth_scheme),
) -> list[dict[str, Any]]:
  user = _user_from_token(credentials, db)
  try:
    limits = business.assert_can_generate(db, user)
  except PermissionError as e:
    raise HTTPException(status_code=403, detail=str(e)) from e
  items = business.generate_outfit_recommendations(db, user.id, payload.count, payload.scenario)
  limits.recommendation_batches_used += 1
  limits.updated_at = datetime.now(timezone.utc)
  db.commit()
  return [_rec_to_dict(r) for r in items]


@app.get("/recommendations/feed")
def recommendations_feed(
  db: Session = Depends(get_db),
  credentials: HTTPAuthorizationCredentials | None = Depends(auth_scheme),
) -> list[dict[str, Any]]:
  user = _user_from_token(credentials, db)
  reviewed = set(
    db.execute(
      select(RecommendationEvent.recommendation_id).where(
        RecommendationEvent.user_id == user.id,
        RecommendationEvent.event_type.in_(["like", "dislike"]),
      )
    ).scalars()
  )
  items = db.execute(select(Recommendation).where(Recommendation.user_id == user.id)).scalars().all()
  return [_rec_to_dict(item) for item in items if item.id not in reviewed]


@app.get("/recommendations/saved")
def recommendations_saved(
  db: Session = Depends(get_db),
  credentials: HTTPAuthorizationCredentials | None = Depends(auth_scheme),
) -> list[dict[str, Any]]:
  user = _user_from_token(credentials, db)
  rows = db.execute(
    select(SavedRecommendation, Recommendation)
    .join(Recommendation, Recommendation.id == SavedRecommendation.recommendation_id)
    .where(SavedRecommendation.user_id == user.id)
    .order_by(SavedRecommendation.created_at.desc())
  ).all()
  out: list[dict[str, Any]] = []
  for saved, rec in rows:
    out.append(
      {
        "id": saved.id,
        "recommendation_id": rec.id,
        "saved_at": saved.created_at.isoformat(),
        "recommendation": _rec_to_dict(rec),
      }
    )
  return out


@app.get("/recommendations/summary")
def recommendations_summary(
  db: Session = Depends(get_db),
  credentials: HTTPAuthorizationCredentials | None = Depends(auth_scheme),
) -> dict[str, Any]:
  user = _user_from_token(credentials, db)
  profile = db.execute(select(StyleProfile).where(StyleProfile.user_id == user.id)).scalar_one()
  summary = business.ensure_summary_row(db, user.id)
  db.commit()
  return business.summary_to_api(summary, profile)


@app.post("/recommendations/summary/rebuild")
def recommendations_summary_rebuild(
  db: Session = Depends(get_db),
  credentials: HTTPAuthorizationCredentials | None = Depends(auth_scheme),
) -> dict[str, Any]:
  user = _user_from_token(credentials, db)
  profile = db.execute(select(StyleProfile).where(StyleProfile.user_id == user.id)).scalar_one()
  summary = business.rebuild_user_summary(db, user.id)
  db.commit()
  return business.summary_to_api(summary, profile)


@app.get("/recommendations/{recommendation_id}")
def recommendation_detail(
  recommendation_id: str,
  db: Session = Depends(get_db),
  credentials: HTTPAuthorizationCredentials | None = Depends(auth_scheme),
) -> dict[str, Any]:
  user = _user_from_token(credentials, db)
  rec = db.execute(
    select(Recommendation).where(
      Recommendation.id == recommendation_id,
      Recommendation.user_id == user.id,
    )
  ).scalar_one_or_none()
  if rec is None:
    raise HTTPException(status_code=404, detail="Карточка не найдена")
  return _rec_to_dict(rec)


@app.post("/recommendations/{recommendation_id}/feedback")
def recommendations_feedback(
  recommendation_id: str,
  payload: FeedbackRequest,
  db: Session = Depends(get_db),
  credentials: HTTPAuthorizationCredentials | None = Depends(auth_scheme),
) -> dict[str, Any]:
  user = _user_from_token(credentials, db)
  recommendation = db.execute(
    select(Recommendation).where(Recommendation.id == recommendation_id)
  ).scalar_one_or_none()
  if recommendation is None or recommendation.user_id != user.id:
    raise HTTPException(status_code=404, detail="Карточка не найдена")
  weight = {"like": 2, "dislike": -2, "save": 3, "unsave": -1, "view_details": 0}[payload.event_type]
  tags = recommendation.tags_json or {}
  db.add(
    RecommendationEvent(
      id=str(uuid4()),
      user_id=user.id,
      recommendation_id=recommendation_id,
      event_type=payload.event_type,
      event_weight=weight,
    )
  )
  business.apply_preferences_from_tags(db, user.id, tags, weight)
  if payload.event_type == "save":
    exists = db.execute(
      select(SavedRecommendation).where(
        SavedRecommendation.user_id == user.id,
        SavedRecommendation.recommendation_id == recommendation_id,
      )
    ).scalar_one_or_none()
    if exists is None:
      db.add(
        SavedRecommendation(
          id=str(uuid4()),
          user_id=user.id,
          recommendation_id=recommendation_id,
        )
      )
  elif payload.event_type == "unsave":
    db.execute(
      delete(SavedRecommendation).where(
        SavedRecommendation.user_id == user.id,
        SavedRecommendation.recommendation_id == recommendation_id,
      )
    )
  db.commit()
  total_events = db.execute(
    select(func.count()).select_from(RecommendationEvent).where(RecommendationEvent.user_id == user.id)
  ).scalar_one()
  total_events = int(total_events or 0)
  if total_events > 0 and total_events % 10 == 0:
    business.rebuild_user_summary(db, user.id)
    db.commit()
  return {"status": "ok", "event_type": payload.event_type}


@app.post("/visual-analysis")
async def visual_analysis(
  db: Session = Depends(get_db),
  credentials: HTTPAuthorizationCredentials | None = Depends(auth_scheme),
) -> dict[str, Any]:
  user = _user_from_token(credentials, db)
  profile = db.execute(select(StyleProfile).where(StyleProfile.user_id == user.id)).scalar_one()
  analysis = profile.profile_json.get("analysis") if isinstance(profile.profile_json, dict) else None
  if not isinstance(analysis, dict) or not analysis:
    raise HTTPException(status_code=400, detail="Сначала выполните анализ стиля по фото.")
  try:
    image_url = await generate_style_visual(analysis)
  except RuntimeError as e:
    raise HTTPException(status_code=502, detail=str(e)) from e
  return {"image_url": image_url, "type": "style_visual"}
