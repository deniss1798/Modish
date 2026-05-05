from __future__ import annotations

from calendar import monthrange
from datetime import datetime, timezone
from typing import Any
from uuid import uuid4

from sqlalchemy import func, select
from sqlalchemy.orm import Session

from .models import (
  Recommendation,
  RecommendationEvent,
  SavedRecommendation,
  StyleProfile,
  User,
  UserLimits,
  UserPreference,
  UserRecommendationSummary,
)
from .config import allow_dev_analyze_bypass
from .schemas.photo_analysis import extract_analysis_section
from .services.recommendation_service import generate_recommendations

MAX_PHOTO_BYTES = 5 * 1024 * 1024
PLUS_MONTHLY_PHOTO_ANALYSES = 1
PLUS_MONTHLY_GENERATION_BATCHES = 5
OUTFITS_PER_BATCH = 10

TAG_FIELD_TO_CATEGORY: dict[str, str] = {
  "styles": "style",
  "colors": "color",
  "silhouettes": "silhouette",
  "occasion": "occasion",
  "item_types": "item_type",
}


def month_bounds_utc(now: datetime) -> tuple[datetime, datetime]:
  start = now.replace(day=1, hour=0, minute=0, second=0, microsecond=0, tzinfo=timezone.utc)
  last = monthrange(now.year, now.month)[1]
  end = start.replace(day=last, hour=23, minute=59, second=59, microsecond=999999)
  return start, end


def _trial_ends_utc(user: User) -> datetime:
  end = user.trial_ends_at
  if end.tzinfo is None:
    return end.replace(tzinfo=timezone.utc)
  return end.astimezone(timezone.utc)


def is_plus_available(user: User, now: datetime | None = None) -> bool:
  if allow_dev_analyze_bypass():
    return True
  now = now or datetime.now(timezone.utc)
  status = (user.subscription_status or "").strip().lower()
  if status == "active":
    return True
  if status == "trial":
    return _trial_ends_utc(user) >= now
  return False


def get_or_refresh_limits(db: Session, user_id: str) -> UserLimits:
  now = datetime.now(timezone.utc)
  row = db.execute(select(UserLimits).where(UserLimits.user_id == user_id)).scalar_one_or_none()
  start, end = month_bounds_utc(now)
  if row is None:
    row = UserLimits(
      id=str(uuid4()),
      user_id=user_id,
      period_start=start,
      period_end=end,
      photo_analysis_used=0,
      recommendation_batches_used=0,
    )
    db.add(row)
    db.flush()
    return row
  if now > row.period_end or now < row.period_start:
    row.period_start = start
    row.period_end = end
    row.photo_analysis_used = 0
    row.recommendation_batches_used = 0
    row.updated_at = now
    db.flush()
  return row


def assert_can_analyze(db: Session, user: User) -> UserLimits:
  if not is_plus_available(user):
    raise PermissionError("Plus недоступен: оформите подписку или дождитесь продления.")
  limits = get_or_refresh_limits(db, user.id)
  if limits.photo_analysis_used >= PLUS_MONTHLY_PHOTO_ANALYSES:
    raise PermissionError("Лимит фото-анализа на этот месяц исчерпан (Plus: 1 раз в месяц).")
  return limits


def assert_can_generate(db: Session, user: User) -> UserLimits:
  if not is_plus_available(user):
    raise PermissionError("Plus недоступен: генерация карточек только для активного trial или подписки.")
  limits = get_or_refresh_limits(db, user.id)
  if limits.recommendation_batches_used >= PLUS_MONTHLY_GENERATION_BATCHES:
    raise PermissionError("Лимит генераций на месяц исчерпан (Plus: 5 пачек).")
  return limits


def apply_preferences_from_tags(
  db: Session,
  user_id: str,
  tags_json: dict[str, Any],
  weight: int,
) -> None:
  if weight == 0:
    return
  now = datetime.now(timezone.utc)
  for field, category in TAG_FIELD_TO_CATEGORY.items():
    values = tags_json.get(field) or []
    if not isinstance(values, list):
      continue
    for raw in values:
      key = str(raw).strip().lower()
      if not key:
        continue
      pref = db.execute(
        select(UserPreference).where(
          UserPreference.user_id == user_id,
          UserPreference.category == category,
          UserPreference.key == key,
        )
      ).scalar_one_or_none()
      if pref is None:
        db.add(
          UserPreference(
            id=str(uuid4()),
            user_id=user_id,
            category=category,
            key=key,
            score=weight,
            source="feedback_tags",
            created_at=now,
            updated_at=now,
          )
        )
      else:
        pref.score += weight
        pref.updated_at = now
  db.flush()


def _stable_prefs(db: Session, user_id: str) -> tuple[dict[str, list[str]], dict[str, list[str]]]:
  rows = db.execute(select(UserPreference).where(UserPreference.user_id == user_id)).scalars().all()
  pos: dict[str, list[str]] = {}
  neg: dict[str, list[str]] = {}
  for r in rows:
    if r.score >= 3:
      pos.setdefault(r.category, []).append(r.key)
    elif r.score <= -3:
      neg.setdefault(r.category, []).append(r.key)
  return pos, neg


def rebuild_user_summary(db: Session, user_id: str) -> UserRecommendationSummary:
  profile = db.execute(select(StyleProfile).where(StyleProfile.user_id == user_id)).scalar_one()
  pj = profile.profile_json or {}
  a = extract_analysis_section(pj)
  pos, neg = _stable_prefs(db, user_id)
  event_count = db.execute(
    select(func.count()).select_from(RecommendationEvent).where(RecommendationEvent.user_id == user_id)
  ).scalar_one()
  event_count = int(event_count or 0)

  suitable_colors = list(dict.fromkeys((a.get("color_palette") or []) + (pos.get("color") or [])))
  avoid_colors = list(dict.fromkeys((a.get("avoid_colors") or []) + (neg.get("color") or [])))
  if not suitable_colors:
    suitable_colors = ["navy", "graphite", "white", "burgundy"]
  if not avoid_colors:
    avoid_colors = ["neon_yellow", "warm_orange"]
  suitable_silhouettes = list(
    dict.fromkeys((a.get("recommended_silhouettes") or []) + (pos.get("silhouette") or []))
  )
  avoid_silhouettes = list(dict.fromkeys((a.get("avoid_silhouettes") or []) + (neg.get("silhouette") or [])))

  recommended_items: list[str] = []
  for it in a.get("recommended_items") or []:
    recommended_items.append(str(it).replace("_", " "))
  for it in pos.get("item_type") or []:
    recommended_items.append(it.replace("_", " "))
  if not recommended_items:
    recommended_items = [
      "белый лонгслив",
      "тёмные прямые джинсы",
      "серый жакет",
      "минималистичные кроссовки",
    ]

  style_direction_human = [
    "Судя по анализу и вашим реакциям, вам вероятно ближе спокойные базовые образы.",
    "Вероятно, вам лучше подходят чистые сочетания без лишних деталей.",
  ]
  if a.get("style_directions"):
    try:
      dirs = [str(x) for x in (a.get("style_directions") or []) if str(x).strip()]
      if dirs:
        style_direction_human.append(f"Направления стиля по анализу: {', '.join(dirs[:4])}.")
    except Exception:
      pass
  if pos.get("style"):
    style_direction_human.append(
      f"Устойчиво положительные отклики по стилям: {', '.join(pos['style'][:4])}."
    )

  avoid_patterns_human = [
    "Лучше использовать осторожно слишком яркие цвета у лица.",
    "Перегруженные образы вы чаще отклоняете.",
  ]
  if a.get("avoid_items"):
    try:
      bad = [str(x) for x in (a.get("avoid_items") or []) if str(x).strip()]
      if bad:
        avoid_patterns_human.append(f"Избегать: {', '.join(bad[:4])}.")
    except Exception:
      pass
  if neg.get("style"):
    avoid_patterns_human.append(
      f"Реже заходят направления: {', '.join(neg['style'][:4])}."
    )

  reactions_insights: list[str] = []
  if event_count >= 3:
    reactions_insights.append("Судя по вашим реакциям, вам чаще нравятся спокойные сочетания.")
  if pos.get("item_type") and any("jacket" in x or "жакет" in x for x in map(str, pos["item_type"])):
    reactions_insights.append("Вы чаще сохраняете образы со структурированным верхом.")
  if neg.get("color"):
    reactions_insights.append("Яркие оттенки вы отклоняете чаще среднего.")

  conf = float(profile.confidence_score or 0.65)
  conf = min(0.95, max(0.35, conf + min(0.1, event_count * 0.005)))

  summary_json = {
    "suitable_colors": suitable_colors[:12],
    "avoid_colors": avoid_colors[:12],
    "suitable_silhouettes": suitable_silhouettes[:12],
    "avoid_silhouettes": avoid_silhouettes[:8],
    "recommended_items": recommended_items[:12],
    "style_direction_human": style_direction_human[:6],
    "avoid_patterns_human": avoid_patterns_human[:6],
    "reactions_insights": reactions_insights[:6],
  }

  row = db.execute(select(UserRecommendationSummary).where(UserRecommendationSummary.user_id == user_id)).scalar_one_or_none()
  now = datetime.now(timezone.utc)
  if row is None:
    row = UserRecommendationSummary(
      id=str(uuid4()),
      user_id=user_id,
      summary_json=summary_json,
      confidence_score=conf,
      based_on_events_count=event_count,
      created_at=now,
      updated_at=now,
    )
    db.add(row)
  else:
    row.summary_json = summary_json
    row.confidence_score = conf
    row.based_on_events_count = event_count
    row.updated_at = now
  db.flush()
  return row


def ensure_summary_row(db: Session, user_id: str) -> UserRecommendationSummary:
  row = db.execute(select(UserRecommendationSummary).where(UserRecommendationSummary.user_id == user_id)).scalar_one_or_none()
  if row:
    return row
  return rebuild_user_summary(db, user_id)


def summary_to_api(summary: UserRecommendationSummary, profile: StyleProfile) -> dict[str, Any]:
  sj = dict(summary.summary_json or {})
  base: dict[str, Any] = {
    "suitable_colors": sj.get("suitable_colors") or [],
    "avoid_colors": sj.get("avoid_colors") or [],
    "suitable_silhouettes": sj.get("suitable_silhouettes") or [],
    "avoid_silhouettes": sj.get("avoid_silhouettes") or [],
    "recommended_items": sj.get("recommended_items") or [],
    "style_direction_human": sj.get("style_direction_human") or [],
    "avoid_patterns_human": sj.get("avoid_patterns_human") or [],
    "reactions_insights": sj.get("reactions_insights") or [],
    "confidence_score": summary.confidence_score,
    "based_on_events_count": summary.based_on_events_count,
  }
  if not base["suitable_colors"] and profile.profile_json:
    a = extract_analysis_section(profile.profile_json)
    base["suitable_colors"] = list(a.get("color_palette") or [])
    base["avoid_colors"] = list(a.get("avoid_colors") or [])
  return base


def outfit_templates_ru() -> list[dict[str, Any]]:
  return [
    {
      "title": "Образ на каждый день",
      "description": "Спокойный минималистичный образ для города.",
      "items": {
        "top": "белый лонгслив",
        "bottom": "прямые тёмные джинсы",
        "layer": "серый жакет",
        "shoes": "белые кроссовки",
      },
      "styles": ["minimal", "smart_casual"],
      "colors": ["white", "navy", "gray"],
      "silhouettes": ["straight", "structured"],
      "item_types": ["longsleeve", "jeans", "jacket", "sneakers"],
    },
    {
      "title": "Офис без строгости",
      "description": "Аккуратный smart casual для рабочего дня.",
      "items": {
        "top": "голубая рубашка",
        "bottom": "тёмные чиносы",
        "layer": "тёмный пиджак",
        "shoes": "лоферы",
      },
      "styles": ["smart_casual", "minimal"],
      "colors": ["navy", "white", "graphite"],
      "silhouettes": ["structured", "straight"],
      "item_types": ["shirt", "chinos", "blazer", "loafers"],
    },
    {
      "title": "Вечер вне дома",
      "description": "Сдержанный вечерний образ с чистыми линиями.",
      "items": {
        "top": "чёрная водолазка",
        "bottom": "тёмные брюки",
        "layer": "шерстяное пальто",
        "shoes": "чёрные ботинки",
      },
      "styles": ["minimal", "evening_relaxed"],
      "colors": ["black", "graphite", "burgundy"],
      "silhouettes": ["structured", "clean_lines"],
      "item_types": ["turtleneck", "trousers", "coat", "boots"],
    },
    {
      "title": "Уикенд",
      "description": "Удобный базовый сет для выходных.",
      "items": {
        "top": "серая футболка",
        "bottom": "светлые джинсы",
        "layer": "худи оверширт",
        "shoes": "кеды",
      },
      "styles": ["casual", "minimal"],
      "colors": ["gray", "white", "sand"],
      "silhouettes": ["relaxed", "straight"],
      "item_types": ["tshirt", "jeans", "overshirt", "sneakers"],
    },
    {
      "title": "Монохром",
      "description": "Один оттенок + текстура, без визуального шума.",
      "items": {
        "top": "молочный свитер",
        "bottom": "графитовые брюки",
        "layer": "пальто camel",
        "shoes": "коричневые челси",
      },
      "styles": ["minimal"],
      "colors": ["cream", "graphite", "camel"],
      "silhouettes": ["structured", "clean_lines"],
      "item_types": ["knit", "trousers", "coat", "chelsea"],
    },
    {
      "title": "Слои и глубина",
      "description": "Тёплые слои с балансом пропорций.",
      "items": {
        "top": "поло тёмно-синее",
        "bottom": "серые брюки",
        "layer": "шерстяной кардиган",
        "shoes": "белые кроссовки",
      },
      "styles": ["smart_casual"],
      "colors": ["navy", "gray", "white"],
      "silhouettes": ["layered", "straight"],
      "item_types": ["polo", "trousers", "cardigan", "sneakers"],
    },
    {
      "title": "Городской минимализм",
      "description": "Лаконичный силуэт и спокойная палитра.",
      "items": {
        "top": "чёрная футболка",
        "bottom": "чёрные джинсы",
        "layer": "куртка-рубашка",
        "shoes": "чёрные кеды",
      },
      "styles": ["minimal", "street_minimal"],
      "colors": ["black", "graphite"],
      "silhouettes": ["straight", "clean_lines"],
      "item_types": ["tshirt", "jeans", "overshirt", "sneakers"],
    },
    {
      "title": "Светлый верх",
      "description": "Свежий контраст без кричащих акцентов.",
      "items": {
        "top": "белая рубашка",
        "bottom": "индиго джинсы",
        "layer": "бежевый тренч",
        "shoes": "лоферы",
      },
      "styles": ["smart_casual"],
      "colors": ["white", "indigo", "sand"],
      "silhouettes": ["structured", "straight"],
      "item_types": ["shirt", "jeans", "trench", "loafers"],
    },
    {
      "title": "Тёплые нейтрали",
      "description": "Мягкие нейтральные тона для повседневности.",
      "items": {
        "top": "бежевый свитер",
        "bottom": "коричневые чиносы",
        "layer": "оливковая куртка",
        "shoes": "белые кроссовки",
      },
      "styles": ["casual", "minimal"],
      "colors": ["sand", "brown", "olive"],
      "silhouettes": ["relaxed", "straight"],
      "item_types": ["knit", "chinos", "jacket", "sneakers"],
    },
    {
      "title": "Строгая база",
      "description": "Чёткие линии и дисциплинированная палитра.",
      "items": {
        "top": "белая водолазка",
        "bottom": "чёрные брюки со стрелкой",
        "layer": "двубортное пальто",
        "shoes": "чёрные ботинки",
      },
      "styles": ["minimal", "tailored"],
      "colors": ["black", "white"],
      "silhouettes": ["structured", "tailored"],
      "item_types": ["turtleneck", "trousers", "coat", "boots"],
    },
  ]


def scenario_occasion(scenario: str) -> list[str]:
  return [scenario]


def generate_outfit_recommendations(
  db: Session,
  user_id: str,
  count: int,
  scenario: str,
) -> list[Recommendation]:
  # Новый путь: рекомендации зависят от style_profile.analysis
  profile = db.execute(select(StyleProfile).where(StyleProfile.user_id == user_id)).scalar_one()
  return generate_recommendations(db, profile, count=count, scenario=scenario)


def billing_status_payload(db: Session, user: User) -> dict[str, Any]:
  now = datetime.now(timezone.utc)
  plus = is_plus_available(user, now)
  limits = get_or_refresh_limits(db, user.id)
  return {
    "plan": user.plan,
    "status": user.subscription_status,
    "trial_ends_at": user.trial_ends_at.isoformat(),
    "is_plus_available": plus,
    # Дополнительные поля не ломают существующий клиент
    "limits": {
      "photo_analysis_used": limits.photo_analysis_used,
      "photo_analysis_limit": PLUS_MONTHLY_PHOTO_ANALYSES,
      "recommendation_batches_used": limits.recommendation_batches_used,
      "recommendation_batches_limit": PLUS_MONTHLY_GENERATION_BATCHES,
      "period_start": limits.period_start.isoformat(),
      "period_end": limits.period_end.isoformat(),
    },
  }
