"""Правила источника: в ленту только одежда (без сумок, аксессуаров, косметики и т.п.)."""

from __future__ import annotations

# (rule_type, rule_value) — значения для blocked_* проверяются как подстрока в category/title.
DEFAULT_CLOTHING_ONLY_RULES: tuple[tuple[str, str], ...] = (
  ("blocked_category", "аксессуар"),
  ("blocked_category", "accessories"),
  ("blocked_category", "сумк"),
  ("blocked_category", "bag"),
  ("blocked_category", "рюкзак"),
  ("blocked_keyword", "косметик"),
  ("blocked_keyword", "парфюм"),
  ("blocked_keyword", "украшен"),
  ("blocked_keyword", "бижутер"),
  ("blocked_keyword", "jewelry"),
  ("blocked_keyword", "посуда"),
  ("blocked_keyword", "игруш"),
  # Спортмастер и др.: не одежда
  ("blocked_category", "инвентарь"),
  ("blocked_category", "снаряжен"),
  ("blocked_category", "тренаж"),
  ("blocked_keyword", "тренажер"),
  ("blocked_keyword", "гантел"),
  ("blocked_keyword", "гиря"),
  ("blocked_keyword", "велосипед"),
  ("blocked_keyword", "лыжи"),
  ("blocked_keyword", "сноуборд"),
  ("blocked_keyword", "ракетк"),
  ("blocked_keyword", "мяч"),
  # Demix / спорт: инвентарь и аксессуары, не одежда
  ("blocked_keyword", "эспандер"),
  ("blocked_keyword", "шейкер"),
  ("blocked_keyword", "коврик для"),
  ("blocked_keyword", "массажер"),
  ("blocked_keyword", "скакалк"),
  ("blocked_keyword", "утяжелител"),
  ("blocked_keyword", "очки для плавания"),
  ("blocked_keyword", "шапочк для плавания"),
  ("blocked_keyword", "кинезио-тейп"),
  ("hide_without_image", "1"),
  ("hide_without_size", "1"),
)
