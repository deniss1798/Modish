"""Интеграционные тесты Ranking Engine v3 на реальном Admitad-фиде.

Запускаются на SQLite in-memory (Postgres не нужен):
  импорт реального фида → style_tags/цвета заполнены →
  персональная лента → жёсткие фильтры → разнообразие → обучение на действиях.
"""
from __future__ import annotations

import csv
import io
import unittest
from datetime import datetime, timezone
from pathlib import Path
from uuid import uuid4

from sqlalchemy import create_engine, select
from sqlalchemy.orm import sessionmaker

from app.db import Base
from app.models import (
  FitProfile,
  Product,
  ProductSource,
  RecommendationEventV2,
  User,
)
from app.catalog_normalize import CANON_COLORS, normalize_category
from app.services.catalog.feed_import_service import sync_partner_feed_from_text
from app.services.recommendation_engine import (
  build_feed_context,
  ensure_taste_profile,
  generate_feed,
  score_product,
)

FEED = Path(__file__).resolve().parents[1] / "test_feeds" / "osnovnoi_products_20260515_001507.csv"
N_ROWS = 1500


def _feed_sample_csv(n_rows: int = N_ROWS) -> str:
  """Первые n_rows корректных CSV-строк реального фида (с заголовком)."""
  csv.field_size_limit(50_000_000)
  out = io.StringIO()
  with FEED.open(encoding="utf-8", errors="replace", newline="") as f:
    reader = csv.reader(f, delimiter=";")
    writer = csv.writer(out, delimiter=";", quoting=csv.QUOTE_MINIMAL)
    for i, row in enumerate(reader):
      writer.writerow(row)
      if i >= n_rows:
        break
  return out.getvalue()


class RecommendationV3Tests(unittest.TestCase):
  @classmethod
  def setUpClass(cls) -> None:
    cls.engine = create_engine("sqlite:///:memory:", future=True)
    Base.metadata.create_all(cls.engine)
    cls.Session = sessionmaker(bind=cls.engine, autoflush=False, future=True)
    db = cls.Session()
    src = ProductSource(
      id="src-test",
      code="fable",
      name="FABLE",
      network="admitad",
      feed_url="https://example.com/feed.csv?format=csv",
    )
    db.add(src)
    db.flush()
    run = sync_partner_feed_from_text(
      db,
      source=src,
      body=_feed_sample_csv(),
      parser_url_hint="feed.csv?format=csv",
    )
    assert run.status == "ok", run.error_message
    cls.db = db

  @classmethod
  def tearDownClass(cls) -> None:
    cls.db.close()

  # --- каталог ---

  def test_products_imported(self) -> None:
    n = len(self.db.execute(select(Product)).scalars().all())
    self.assertGreater(n, 300, "должно импортироваться много товаров")

  def test_style_tags_not_empty(self) -> None:
    products = self.db.execute(select(Product)).scalars().all()
    without = [p for p in products if not p.style_tags]
    self.assertEqual(
      len(without), 0,
      f"у всех товаров одежды должны быть style-теги, пусто у {len(without)}",
    )

  def test_colors_canonical(self) -> None:
    products = self.db.execute(select(Product)).scalars().all()
    with_colors = [p for p in products if p.colors]
    self.assertGreater(len(with_colors), len(products) * 0.8)
    bad = [c for p in with_colors for c in p.colors if c not in CANON_COLORS]
    self.assertEqual(bad, [], f"неканонические цвета: {bad[:10]}")

  def test_categories_canonical(self) -> None:
    products = self.db.execute(select(Product)).scalars().all()
    known = {
      "футболки", "рубашки", "джинсы", "брюки", "верхний_слой", "обувь",
      "сумки", "аксессуары", "худи", "платья", "юбки", "спорт", "шорты",
      "джемперы", "бельё", "купальники", "костюмы", "комбинезоны",
    }
    canon = [p for p in products if normalize_category(p.category) in known]
    self.assertGreater(len(canon), len(products) * 0.9)

  # --- персональная лента ---

  def _make_user(self, *, email: str, gender: str, interests: list[str], styles: list[str]) -> User:
    db = self.db
    u = User(id=str(uuid4()), email=email, password_hash="x")
    db.add(u)
    db.flush()
    db.add(
      FitProfile(
        id=str(uuid4()),
        user_id=u.id,
        height_cm=170,
        gender_target=gender,
        clothing_size="M",
        budget_min=0,
        budget_max=10_000,
        interest_categories=interests,
        style_scenarios=["daily", "minimal"],
      )
    )
    tp = ensure_taste_profile(db, u.id)
    tp.liked_styles = styles
    db.flush()
    return u

  def test_feed_personalized_for_woman(self) -> None:
    u = self._make_user(
      email="anna@test.io",
      gender="womenswear",
      interests=["платья", "юбки", "джинсы"],
      styles=["minimalism", "casual"],
    )
    feed = generate_feed(self.db, u, limit=30)
    self.assertGreater(len(feed), 0, "лента не должна быть пустой")
    from app.catalog_normalize import product_gender_from_model

    for s in feed:
      pg = product_gender_from_model(s.product)
      self.assertNotEqual(pg, "menswear", f"мужской товар в женской ленте: {s.product.title}")
      self.assertLessEqual(int(s.product.price), 10_500, "жёсткий фильтр бюджета нарушен")
      self.assertTrue(s.reason, "у каждой рекомендации должно быть объяснение")

  def test_diversity_no_long_runs(self) -> None:
    u = self._make_user(
      email="diva@test.io",
      gender="womenswear",
      interests=[],
      styles=["casual"],
    )
    feed = generate_feed(self.db, u, limit=40)
    cats = [normalize_category(s.product.category or "") for s in feed]
    run = 1
    for a, b in zip(cats, cats[1:]):
      run = run + 1 if a == b else 1
      self.assertLessEqual(run, 3, f"больше 3 подряд категории {b}")

  def test_learning_like_shifts_scores(self) -> None:
    u = self._make_user(
      email="learn@test.io",
      gender="womenswear",
      interests=[],
      styles=[],
    )
    db = self.db
    dress = None
    for p in db.execute(select(Product)).scalars().all():
      if normalize_category(p.category or "") == "платья" and (p.gender_target or "") != "menswear":
        dress = p
        break
    self.assertIsNotNone(dress, "в выборке фида должно быть платье")

    ctx0 = build_feed_context(db, u, product_ids=[dress.id])
    before = score_product(db, u, dress, ctx0).final_score

    # имитируем обучение: save платья → веса категории/стиля растут,
    # как в POST /recommendations/events
    tp = ensure_taste_profile(db, u.id)
    tp.category_weights = dict(tp.category_weights or {}, **{"платья": 5})
    sw = dict(tp.style_weights or {})
    for t in dress.style_tags or []:
      sw[str(t)] = 5
    tp.style_weights = sw
    db.flush()

    ctx1 = build_feed_context(db, u, product_ids=[dress.id])
    after = score_product(db, u, dress, ctx1).final_score
    self.assertGreater(after, before, "после сохранения похожие товары должны ранжироваться выше")

  def test_popularity_component(self) -> None:
    u = self._make_user(
      email="pop@test.io",
      gender="womenswear",
      interests=[],
      styles=[],
    )
    other = self._make_user(
      email="other@test.io",
      gender="womenswear",
      interests=[],
      styles=[],
    )
    db = self.db
    p = db.execute(select(Product)).scalars().first()
    for _ in range(3):
      db.add(
        RecommendationEventV2(
          id=str(uuid4()),
          user_id=other.id,
          product_id=p.id,
          event_type="save",
          event_weight=5,
          meta_json={},
          created_at=datetime.now(timezone.utc),
        )
      )
    db.flush()
    ctx = build_feed_context(db, u, product_ids=[p.id])
    scored = score_product(db, u, p, ctx)
    self.assertGreater(scored.breakdown.get("popularity", 0.0), 0.0)

  def test_underwear_not_in_feed(self) -> None:
    u = self._make_user(
      email="clean@test.io",
      gender="womenswear",
      interests=[],
      styles=[],
    )
    feed = generate_feed(self.db, u, limit=40)
    for s in feed:
      self.assertNotEqual(
        normalize_category(s.product.category or ""),
        "бельё",
        f"бельё в общей ленте: {s.product.title}",
      )


if __name__ == "__main__":
  unittest.main()
