from __future__ import annotations

from datetime import datetime, timezone
from typing import Any
from uuid import uuid4

from sqlalchemy import select
from sqlalchemy.orm import Session

from ..models import FitProfile, Outfit, Product, RecommendationEventV2, StyleProfile, User
from ..schemas.photo_analysis import extract_analysis_section


def _norm_list(values: Any) -> list[str]:
  if not isinstance(values, list):
    return []
  out: list[str] = []
  for x in values:
    s = str(x).strip().lower()
    if s and s not in out:
      out.append(s)
  return out


def generate_outfits(db: Session, user: User, *, count: int = 3) -> list[Outfit]:
  """
  MVP outfits:
  - pick top/bottom/shoes from highest scored products by category
  - use profile analysis style_direction/palette as hints (best-effort)
  """
  profile = db.execute(select(StyleProfile).where(StyleProfile.user_id == user.id)).scalar_one()
  analysis = extract_analysis_section(profile.profile_json or {})
  palette = _norm_list(analysis.get("color_palette"))[:4]
  style_dir = (_norm_list(analysis.get("style_directions"))[:1] or ["minimal"])[0]

  fit = db.execute(select(FitProfile).where(FitProfile.user_id == user.id)).scalar_one_or_none()
  size = (fit.clothing_size or "").strip().upper() if fit else ""

  # Don't use products the user already skipped/disliked
  excluded = set(
    db.execute(
      select(RecommendationEventV2.product_id).where(
        RecommendationEventV2.user_id == user.id,
        RecommendationEventV2.product_id.is_not(None),
        RecommendationEventV2.event_type.in_(["skip", "dislike"]),
      )
    ).scalars()
  )
  excluded = {str(x) for x in excluded if x}

  def candidates(category: str) -> list[Product]:
    q = select(Product).where(Product.is_active == 1, Product.category == category)
    rows = db.execute(q.limit(200)).scalars().all()
    if excluded:
      rows = [p for p in rows if p.id not in excluded]
    # filter by size if possible
    if size:
      rows = [p for p in rows if not p.available_sizes or size in {str(x).upper() for x in p.available_sizes}]
    # prefer palette match
    if palette:
      rows.sort(key=lambda p: int(bool(set(_norm_list(p.colors)) & set(palette))), reverse=True)
    return rows[:20]

  tops = (
    candidates("футболки")
    or candidates("рубашки")
    or candidates("shirts")
    or candidates("tshirts")
    or candidates("tops")
  )
  bottoms = candidates("джинсы") or candidates("jeans") or candidates("брюки") or candidates("trousers")
  shoes_list = candidates("обувь") or candidates("shoes")

  created: list[Outfit] = []
  now = datetime.now(timezone.utc)
  need = max(1, min(10, int(count)))
  seen: set[tuple[str | None, str | None, str | None]] = set()
  ti = bi = si = 0
  # generate unique combinations by cycling candidates
  while len(created) < need and (tops or bottoms or shoes_list):
    top = tops[ti % len(tops)] if tops else None
    bottom = bottoms[bi % len(bottoms)] if bottoms else None
    shoes = shoes_list[si % len(shoes_list)] if shoes_list else None
    ti += 1
    bi += 1 if ti % 2 == 0 else 0
    si += 1 if ti % 3 == 0 else 0

    key = (top.id if top else None, bottom.id if bottom else None, shoes.id if shoes else None)
    if key in seen:
      continue
    seen.add(key)

    items: dict[str, Any] = {}
    total = 0
    if top:
      items["top"] = top.id
      total += int(top.price or 0)
    if bottom:
      items["bottom"] = bottom.id
      total += int(bottom.price or 0)
    if shoes:
      items["shoes"] = shoes.id
      total += int(shoes.price or 0)

    out = Outfit(
      id=str(uuid4()),
      user_id=user.id,
      items_json=items,
      total_price=total,
      style_direction=style_dir,
      reason=f"Собрано под {style_dir} с учётом палитры: {', '.join(palette) if palette else '—'}.",
      score=0.5,
      is_saved=0,
      created_at=now,
      updated_at=now,
    )
    db.add(out)
    created.append(out)
  db.flush()
  return created

