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
  ("hide_without_image", "1"),
  ("hide_without_size", "1"),
)
