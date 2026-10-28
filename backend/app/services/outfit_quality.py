"""Non-negotiable catalogue checks for wearable, scenario-appropriate outfits."""
import re
from functools import lru_cache

from ..models import Product
from ..money import as_rubles
from ..catalog_normalize import normalize_category
from .garment_roles import product_role

FORMAL_SHOE_TERMS = ("туфл", "лофер", "балетк", "мокасин", "оксфорд", "дерби", "монки", "ботильон", "pumps", "loafers", "oxford", "derby", "dress shoes", "leather shoes")
FORMAL_SCENARIOS = {"office", "evening", "restaurant", "wedding", "date", "party"}


def garment_text(product: Product) -> str:
  # Exact field values keep cached text current after live catalogue edits.
  # No Product, descriptions or merchant JSON are retained by the cache.
  fields = tuple(str(getattr(product, name, '') or '') for name in
    ('title', 'category', 'category_name', 'subcategory', 'merchant_category', 'merchant_subcategory'))
  return _garment_text_from_fields(fields)


@lru_cache(maxsize=4096)
def _garment_text_from_fields(fields: tuple[str, ...]) -> str:
  # Descriptions often cross-sell other categories; use the actual item fields.
  parts = [fields[0]]
  for raw_value in fields[1:]:
    value = raw_value.lower()
    # Shared navigation is not a garment attribute. Keep all other context
    # (children, beach, sport, homewear) for the existing strict exclusions.
    value = re.sub(r'обувь[ _]+и[ _]+аксессуары', 'обувь', value)
    parts.append(value)
  return ' '.join(parts).lower().replace('ё','е')


_EXCLUDED = re.compile(
  r"бель|трус|бюстгальтер|лифчик|стринг|боди|корсет|боксеры|кальсон|термо|пижам|ночн(?:ая|ые|ой)|"
  r"домашн|тапоч|тапки|купаль|плавк|плаван|пляж|носк[иао]|колгот|чулк|"
  r"детск|мальч|девоч|малыш|перчат|аксессуар|сумк|рюкзак|боксерк|борцовк|чешки|"
  r"куртк|пухов|пальто|плащ|ветровк|анорак|парка|шуб[аыу]|жилет|кардиган|"
  r"\b(?:underwear|briefs|boxers|bra|lingerie|thermal|baselayer|pajamas?|pyjamas?|"
  r"sleepwear|nightwear|loungewear|slippers?|swim\w*|beach\w*|socks?|tights|kids?|junior|bags?|accessor\w*|coat|parka|puffer|windbreaker)\b"
)
_SPORT = re.compile(r"спортив|трениров|фитнес|бегов|бег[ау]|баскетбол|футбол(?!к)|борц|бокс[а ]|"
                    r"\b(?:sport|sports|gym|running|training|athletic|basketball|football|boxing)\b")
_CASUAL_ONLY = re.compile(r"кроссов|кед[ыа ]|шорт|худи|толстов|свитшот|футболк|майк|\bтоп\b|\btank\b|поло|леггин|легин|лосин|джоггер|сланц|шлепан|"
                          r"\b(?:sneakers?|trainers?|shorts?|hoodie|sweatshirt|t-shirt|polo|leggings?|joggers?|slides?)\b")
_TECHNICAL = re.compile(r"горнолыж|лыжн|сноуборд|треккин|рыболов|охотнич|\b(?:ski|skiing|snowboard|trekking|fishing|hunting)\b")

# Many merchants omit season. Use the sold garment and explicit attributes,
# never a cross-selling sentence such as "wear this coat with sandals".
_COLD = re.compile(r"пух|утеплен|зимн|стеган|овчин|дублен|шуб|синтепон|холлофайбер|"
                   r"\b(?:winter|puffer|padded|insulated|down)\b")
_OPEN_SHOES = re.compile(r"сандал|босонож|шлепан|сланц|сабо|мюли|открыт.{0,12}(?:нос|пят)|"
                         r"\b(?:sandals?|slides?|flip.flops?|mules?|open.toe)\b")
_LIGHT_SHOES = re.compile(r"туфл|балетк|лофер|мокасин|эспадрил|"
                          r"\b(?:pumps?|flats?|loafers?|moccasins?|espadrilles?)\b")


def _weather_text(product: Product) -> str:
  attributes = ' '.join(str(value)
    for raw in (product.raw_params_json, product.feed_raw_json) if isinstance(raw, dict)
    for key, value in raw.items()
    if any(term in str(key).lower() for term in ('сезон', 'утепл', 'наполн', 'season', 'insulation', 'filling')))
  return f'{garment_text(product)} {product.season or ""} {attributes}'.lower().replace('ё', 'е')


def _weather_compatible(left: Product, right: Product) -> bool:
  for garment, shoe in ((left, right), (right, left)):
    if product_role(shoe) != 'shoes':
      continue
    text, shoe_text = _weather_text(garment), _weather_text(shoe)
    outerwear = product_role(garment) == 'outerwear'
    # A cardigan can be a light layer; a coat/jacket needs closed footwear.
    outdoor_layer = outerwear and not re.search(r'кардиган|\bcardigan\b', garment_text(garment))
    if (outdoor_layer or _COLD.search(text)) and _OPEN_SHOES.search(shoe_text):
      return False
    if outerwear and _COLD.search(text) and _LIGHT_SHOES.search(shoe_text):
      return False
  return True


def is_sports_item(product: Product) -> bool:
  # The first sentence describes the sold item; later text can cross-sell it.
  lead = re.split(r"[.!?\n]", str(product.description or ""), maxsplit=1)[0][:300]
  attributes = " ".join(str(v) for k, v in (product.raw_params_json or {}).items()
    if any(term in str(k).lower() for term in ("стиль", "назначен", "спорт", "style", "sport")))
  text = f"{garment_text(product)} {lead} {attributes}".lower().replace("ё", "е")
  return bool(_SPORT.search(text) or re.search(r"джоггер|флис|манжет.{0,25}брюк|\b(?:joggers|trackpants|sweatpants|fleece)\b", text)
    or ((product.source or "").lower() in {"sportmaster", "demix"} and product_role(product) == "bottom"))


def pair_compatible(left: Product, right: Product) -> bool:
  a, b = product_role(left), product_role(right)
  if not a or not b or a == b or ({a, b} & {"one_piece"} and {a, b} & {"top", "bottom"}):
    return False
  if not _weather_compatible(left, right):
    return False
  from .catalog.product_dna import reliable_taste_features
  documented_seasons = set().union(*(reliable_taste_features(p, dimensions=('season',)).get('season', set()) for p in (left, right)))
  if {'winter', 'summer'} <= documented_seasons:
    return False
  for item, other in ((left, right), (right, left)):
    text = garment_text(item)
    if _TECHNICAL.search(text):
      return False
    if product_role(item) == "shoes" and re.search(r"мех|овчин|утеплен|дутик|зимн|антигололед|шерстян.{0,12}подклад", text):
      # A generic "dress" title does not prove it is a warm winter garment.
      if product_role(other) == "one_piece" and not re.search(r"вязан|шерст|тепл|\b(?:knitted|wool|warm)\b", garment_text(other)):
        return False
      if re.search(r"футболк|майк|\bтоп\b|сарафан|шорт|льнян|шифон|без[ _]+рукав|бретел|\b(?:t-shirt|tank|linen|chiffon|sundress|sleeveless|shorts)\b", garment_text(other)):
        return False
    if is_sports_item(item):
      text = garment_text(other)
      if any(term in text for term in FORMAL_SHOE_TERMS) or re.search(r"классич|делов|вечерн|\b(?:formal|business)\b", text):
        return False
      if product_role(other) == "top" and re.search(r"рубаш|сороч|блуз|(?<!t-)\bshirt\b", text) and not is_sports_item(other):
        return False
  seasons = {str(p.season or "").lower() for p in (left, right)}
  return not (seasons & {"winter", "зима"} and seasons & {"summer", "лето"})


def outfit_compatible(products: list[Product]) -> bool:
  return all(pair_compatible(a, b) for i, a in enumerate(products) for b in products[i + 1:])


def scenario_product_ok(product: Product, scenario: str) -> bool:
  text = garment_text(product)
  cats = {normalize_category(getattr(product, name, "") or "") for name in ("category", "category_name", "subcategory")}
  brand = (product.brand or "").strip().lower()
  sports_brand = any(name in brand for name in ("demix", "kappa", "puma", "reebok", "nike", "adidas", "outventure", "northland", "everlast", "green hill"))
  sports_source = (product.source or "").lower() in {"sportmaster", "demix"}
  if _EXCLUDED.search(text) or _TECHNICAL.search(text) or as_rubles(product.price) > 500_000:
    return False
  if scenario not in {"gym", "run"} and _SPORT.search(text):
    return False
  if scenario not in {"gym", "run"} and (sports_brand or sports_source) and cats & {"костюмы", "комбинезоны"}:
    return False
  if scenario not in {"gym", "run"} and sports_brand and cats & {"брюки", "шорты"}:
    return False
  if scenario in FORMAL_SCENARIOS:
    if sports_source or is_sports_item(product):
      return False
    if "костюмы" in cats and not any(term in text for term in ("классич", "делов", "брючн", "пиджак", "formal", "business")):
      return False
    if _CASUAL_ONLY.search(text) or re.search(r"потертост|рван|утеплен|мех|непромока|терм|лонгслив|флиc|флис|теплоизоля|дутик|шерстян.{0,12}подклад", text):
      return False
    if sports_brand:
      return False
    if cats & {"брюки", "юбки"} and "трикотаж" in text:
      return False
    if "обувь" in cats and not any(term in text for term in FORMAL_SHOE_TERMS):
      return False
    if scenario in {"evening", "restaurant", "wedding", "party"} and cats & {"джинсы", "джемперы", "худи"}:
      return False
  return True
