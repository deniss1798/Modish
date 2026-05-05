from __future__ import annotations

from datetime import datetime, timezone
from typing import Any
from uuid import uuid4

from sqlalchemy import select
from sqlalchemy.orm import Session

from ..models import FitProfile, Outfit, Product, StyleProfile, User
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

  def pick(category: str) -> Product | None:
    q = select(Product).where(Product.is_active == 1, Product.category == category)
    rows = db.execute(q.limit(200)).scalars().all()
    # filter by size if possible
    if size:
      rows = [p for p in rows if not p.available_sizes or size in {str(x).upper() for x in p.available_sizes}]
    # prefer palette match
    if palette:
      rows.sort(key=lambda p: int(bool(set(_norm_list(p.colors)) & set(palette))), reverse=True)
    return rows[0] if rows else None

  top = pick("футболки") or pick("tshirts") or pick("tops")
  bottom = pick("джинсы") or pick("jeans") or pick("брюки") or pick("trousers")
  shoes = pick("обувь") or pick("shoes")

  created: list[Outfit] = []
  now = datetime.now(timezone.utc)
  for _ in range(max(1, min(10, int(count)))):
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

