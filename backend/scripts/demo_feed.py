# -*- coding: utf-8 -*-
"""Демо персональной ленты на реальном фиде — без Postgres (SQLite в памяти).

Запуск из папки backend:
  .venv\\Scripts\\python.exe scripts\\demo_feed.py

Создаёт двух тестовых пользователей с разными профилями,
импортирует реальный Admitad-фид и показывает их ленты
с объяснениями. Результат пишется в ../демо_лента.txt
"""
from __future__ import annotations

import csv
import io
import sys
from pathlib import Path
from uuid import uuid4

BACKEND = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(BACKEND))

from sqlalchemy import create_engine, select  # noqa: E402
from sqlalchemy.orm import sessionmaker  # noqa: E402

from app.db import Base  # noqa: E402
from app.models import FitProfile, Product, ProductSource, User  # noqa: E402
from app.catalog_normalize import normalize_category  # noqa: E402
from app.services.catalog.feed_import_service import sync_partner_feed_from_text  # noqa: E402
from app.services.recommendation_engine import ensure_taste_profile, generate_feed  # noqa: E402

FEED = BACKEND / "test_feeds" / "osnovnoi_products_20260515_001507.csv"
OUT = BACKEND.parent / "demo_lenta.txt"
N_ROWS = 4000


def feed_sample() -> str:
  csv.field_size_limit(50_000_000)
  out = io.StringIO()
  with FEED.open(encoding="utf-8", errors="replace", newline="") as f:
    reader = csv.reader(f, delimiter=";")
    writer = csv.writer(out, delimiter=";", quoting=csv.QUOTE_MINIMAL)
    for i, row in enumerate(reader):
      writer.writerow(row)
      if i >= N_ROWS:
        break
  return out.getvalue()


def make_user(db, *, email, gender, size, budget_max, interests, scenarios, styles):
  u = User(id=str(uuid4()), email=email, password_hash="demo")
  db.add(u)
  db.flush()
  db.add(
    FitProfile(
      id=str(uuid4()),
      user_id=u.id,
      height_cm=170,
      gender_target=gender,
      clothing_size=size,
      budget_min=0,
      budget_max=budget_max,
      interest_categories=interests,
      style_scenarios=scenarios,
    )
  )
  tp = ensure_taste_profile(db, u.id)
  tp.liked_styles = styles
  db.flush()
  return u


def show_feed(db, user, title, lines):
  lines.append("")
  lines.append("=" * 78)
  lines.append(title)
  lines.append("=" * 78)
  feed = generate_feed(db, user, limit=15)
  if not feed:
    lines.append("  (лента пуста)")
    return
  for i, s in enumerate(feed, 1):
    p = s.product
    cat = normalize_category(p.category or "")
    colors = ",".join(p.colors or []) or "-"
    tags = ",".join((p.style_tags or [])[:3]) or "-"
    lines.append(
      f"{i:2}. [{s.final_score:5.1f}] {p.title[:52]:<52} | {cat:<12} | {p.price:>6}₽ | {colors:<12}"
    )
    lines.append(f"      стиль: {tags}")
    lines.append(f"      почему: {'; '.join(s.reasons[:3])}")


def main() -> None:
  engine = create_engine("sqlite:///:memory:", future=True)
  Base.metadata.create_all(engine)
  Session = sessionmaker(bind=engine, autoflush=False, future=True)
  db = Session()

  src = ProductSource(
    id="src-demo",
    code="fable",
    name="FABLE",
    network="admitad",
    feed_url="https://example.com/feed.csv?format=csv",
  )
  db.add(src)
  db.flush()

  print("Импортирую реальный фид (это ~30 секунд)...")
  run = sync_partner_feed_from_text(
    db, source=src, body=feed_sample(), parser_url_hint="feed.csv?format=csv"
  )
  n = len(db.execute(select(Product)).scalars().all())

  lines: list[str] = []
  lines.append(f"Импортировано товаров: {n} (статус: {run.status})")

  anna = make_user(
    db,
    email="anna@demo.io",
    gender="womenswear",
    size="S",
    budget_max=8000,
    interests=["платья", "юбки", "джинсы"],
    scenarios=["daily", "minimal"],
    styles=["minimalism", "casual"],
  )
  show_feed(
    db, anna,
    "АННА — женское, размер S, до 8 000₽, минимализм, платья/юбки/джинсы",
    lines,
  )

  ivan = make_user(
    db,
    email="ivan@demo.io",
    gender="menswear",
    size="L",
    budget_max=15000,
    interests=["футболки", "джинсы", "верхний_слой"],
    scenarios=["daily"],
    styles=["streetwear", "casual"],
  )
  show_feed(
    db, ivan,
    "ИВАН — мужское, размер L, до 15 000₽, стритвир, футболки/джинсы/куртки",
    lines,
  )

  # Обучение: Анна «сохраняет» несколько платьев → лента должна перестроиться
  tp = ensure_taste_profile(db, anna.id)
  tp.category_weights = dict(tp.category_weights or {}, **{"платья": 8})
  cw = dict(tp.color_weights or {})
  cw["blue"] = 7
  tp.color_weights = cw
  db.flush()
  show_feed(
    db, anna,
    "АННА после лайков платьев и голубых вещей — лента перестроилась",
    lines,
  )

  text = "\n".join(lines)
  OUT.write_text(text, encoding="utf-8")
  print(text)
  print(f"\nСохранено в {OUT}")


if __name__ == "__main__":
  main()
