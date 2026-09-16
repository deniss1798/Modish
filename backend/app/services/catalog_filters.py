"""Explicit feed filters applied to the catalog before ranking or pagination."""
from dataclasses import dataclass
from sqlalchemy import func, or_
from ..models import Product
from ..catalog_normalize import canon_color_token, normalize_category, normalize_size_token, normalize_product_colors

CATEGORY_GROUPS = {
  'outerwear': {'верхний_слой', 'верхняя_одежда', 'куртки', 'пальто', 'пуховики'},
  'tops': {'футболки', 'рубашки', 'джемперы', 'худи', 'свитшоты', 'топы', 'блузки'},
  'bottoms': {'брюки', 'джинсы', 'шорты', 'юбки'},
  'shoes': {'обувь'},
  'dresses': {'платья', 'комбинезоны'},
}
CATEGORY_TERMS = {
  'outerwear': ('куртк', 'пальто', 'пухов', 'ветров', 'бомбер', 'парка', 'верхн'),
  'tops': ('футбол', 'рубаш', 'джемпер', 'худи', 'свит', 'топ', 'блуз', 'поло', 'водолаз'),
  'bottoms': ('брюк', 'брюч', 'джинс', 'шорт', 'юбк', 'леггин'),
  'shoes': ('обув', 'кроссов', 'кед', 'туф', 'лофер', 'ботин', 'сапог', 'балет', 'сандал', 'босонож', 'мокас'),
  'dresses': ('плать', 'сарафан', 'комбинез'),
}

@dataclass(frozen=True)
class CatalogFilters:
  min_price: int | None = None
  max_price: int | None = None
  categories: tuple[str, ...] = ()
  sizes: tuple[str, ...] = ()
  colors: tuple[str, ...] = ()

  @property
  def active(self):
    return self.min_price is not None or self.max_price is not None or bool(self.categories or self.sizes or self.colors)

  def matches(self, p: Product) -> bool:
    if self.min_price is not None and p.price < self.min_price: return False
    if self.max_price is not None and p.price > self.max_price: return False
    if self.categories:
      hay = ' '.join(str(getattr(p,k,'') or '') for k in ('category','category_name','subcategory','title')).lower()
      cats = {normalize_category(getattr(p,k,None)) for k in ('category','category_name','subcategory')}
      if not any(cats & CATEGORY_GROUPS.get(c,set()) or any(t in hay for t in CATEGORY_TERMS.get(c,())) for c in self.categories): return False
    if self.sizes:
      sizes = {normalize_size_token(str(s)) for s in (p.available_sizes or [])}
      if not sizes & {normalize_size_token(s) for s in self.sizes}: return False
    if self.colors:
      colors = set(normalize_product_colors([*(p.colors or []), p.color_family or '', p.color_original or '']))
      if not colors & {canon_color_token(c) for c in self.colors}: return False
    return True

  def sql_conditions(self):
    # Coarse SQL preselection; matches() is authoritative for legacy strings.
    out = []
    if self.min_price is not None: out.append(Product.price >= self.min_price)
    if self.max_price is not None: out.append(Product.price <= self.max_price)
    if self.categories:
      hay = func.lower(func.coalesce(Product.category,'')+' '+func.coalesce(Product.category_name,'')+' '+Product.title)
      terms = {t for c in self.categories for t in CATEGORY_TERMS.get(c,())}
      out.append(or_(*[hay.contains(t) for t in terms]))
    return out
