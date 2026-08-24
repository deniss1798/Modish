from __future__ import annotations

import re
from typing import Any

CANON_COLORS = frozenset(
  {
    "black",
    "white",
    "cream",
    "navy",
    "grey",
    "blue",
    "brown",
    "green",
    "burgundy",
    "red",
    "pink",
    "yellow",
    "orange",
    "purple",
    "multicolor",
  }
)

_COLOR_ALIASES: dict[str, str] = {
  "gray": "grey",
  "graphite": "grey",
  "charcoal": "grey",
  "silver": "grey",
  "offwhite": "cream",
  "ivory": "cream",
  "beige": "cream",
  "sand": "cream",
  "ecru": "cream",
  "indigo": "blue",
  "denim": "blue",
  "olive": "green",
  "khaki": "green",
  "mint": "green",
  "wine": "burgundy",
  "maroon": "burgundy",
  "bordeaux": "burgundy",
  "lilac": "purple",
  "violet": "purple",
  "lavender": "purple",
  "fuchsia": "pink",
  "rose": "pink",
  "coral": "orange",
  "peach": "orange",
  "gold": "yellow",
  "mustard": "yellow",
  # --- русские цвета (Admitad-фиды: Befree, Baon, Sela и др.) ---
  "черный": "black",
  "чёрный": "black",
  "белый": "white",
  "молочный": "cream",
  "бежевый": "cream",
  "кремовый": "cream",
  "песочный": "cream",
  "слоновая_кость": "cream",
  "экрю": "cream",
  "телесный": "cream",
  "синий": "blue",
  "голубой": "blue",
  "васильковый": "blue",
  "бирюзовый": "blue",
  "индиго": "navy",
  "джинсовый": "blue",
  "деним": "blue",
  "серый": "grey",
  "графитовый": "grey",
  "графит": "grey",
  "серебристый": "grey",
  "серебряный": "grey",
  "антрацит": "grey",
  "коричневый": "brown",
  "шоколадный": "brown",
  "кофейный": "brown",
  "терракотовый": "brown",
  "капучино": "brown",
  "карамельный": "brown",
  "зеленый": "green",
  "зелёный": "green",
  "хаки": "green",
  "оливковый": "green",
  "мятный": "green",
  "изумрудный": "green",
  "фисташковый": "green",
  "салатовый": "green",
  "бордовый": "burgundy",
  "бордо": "burgundy",
  "винный": "burgundy",
  "вишневый": "burgundy",
  "вишнёвый": "burgundy",
  "марсала": "burgundy",
  "красный": "red",
  "алый": "red",
  "розовый": "pink",
  "пудровый": "pink",
  "фуксия": "pink",
  "малиновый": "pink",
  "коралловый": "orange",
  "персиковый": "orange",
  "оранжевый": "orange",
  "желтый": "yellow",
  "жёлтый": "yellow",
  "горчичный": "yellow",
  "золотой": "yellow",
  "золотистый": "yellow",
  "лимонный": "yellow",
  "фиолетовый": "purple",
  "сиреневый": "purple",
  "лиловый": "purple",
  "лавандовый": "purple",
  "баклажановый": "purple",
  "мультиколор": "multicolor",
  "разноцветный": "multicolor",
  "цветной": "multicolor",
  "ванильный": "cream",
  "тауп": "brown",
  "сливовый": "purple",
  "банановый": "yellow",
  "табачный": "brown",
  "мокко": "brown",
  "какао": "brown",
}

# Приставки-модификаторы: «тёмно-синий» → «синий», "light grey" → "grey"
_COLOR_PREFIXES = (
  "светло",
  "темно",
  "тёмно",
  "ярко",
  "бледно",
  "нежно",
  "пастельно",
  "насыщенно",
  "light",
  "dark",
  "pale",
  "bright",
  "deep",
)

# Стемы для свободного текста (палитра из AI-анализа фото, составные названия)
_COLOR_STEMS: tuple[tuple[str, str], ...] = (
  ("черн", "black"),
  ("чёрн", "black"),
  ("black", "black"),
  ("беж", "cream"),
  ("кремов", "cream"),
  ("молочн", "cream"),
  ("песочн", "cream"),
  ("cream", "cream"),
  ("beige", "cream"),
  ("ivory", "cream"),
  ("бел", "white"),
  ("white", "white"),
  ("navy", "navy"),
  ("индиго", "navy"),
  ("голуб", "blue"),
  ("бирюз", "blue"),
  ("васильков", "blue"),
  ("син", "blue"),
  ("blue", "blue"),
  ("графит", "grey"),
  ("grey", "grey"),
  ("gray", "grey"),
  ("коричн", "brown"),
  ("шоколад", "brown"),
  ("кофейн", "brown"),
  ("терракот", "brown"),
  ("brown", "brown"),
  ("зелен", "green"),
  ("зелён", "green"),
  ("хаки", "green"),
  ("олив", "green"),
  ("мятн", "green"),
  ("изумруд", "green"),
  ("green", "green"),
  ("olive", "green"),
  ("бордо", "burgundy"),
  ("винн", "burgundy"),
  ("вишн", "burgundy"),
  ("burgundy", "burgundy"),
  ("красн", "red"),
  ("алый", "red"),
  ("red", "red"),
  ("розов", "pink"),
  ("пудров", "pink"),
  ("малинов", "pink"),
  ("pink", "pink"),
  ("коралл", "orange"),
  ("персиков", "orange"),
  ("оранж", "orange"),
  ("orange", "orange"),
  ("желт", "yellow"),
  ("жёлт", "yellow"),
  ("горчичн", "yellow"),
  ("золот", "yellow"),
  ("yellow", "yellow"),
  ("фиолет", "purple"),
  ("сирен", "purple"),
  ("лилов", "purple"),
  ("лаванд", "purple"),
  ("purple", "purple"),
  ("violet", "purple"),
  ("сер", "grey"),
)


def canon_color_token(raw: str) -> str:
  """Один цвет (любой язык, с модификаторами) → канонический цвет или ''."""
  s = str(raw or "").strip().lower()
  if not s:
    return ""
  s = re.sub(r"[\s\-–—]+", "_", s)
  if s in _COLOR_ALIASES:
    return _COLOR_ALIASES[s]
  if s in CANON_COLORS:
    return s
  # отбрасываем приставку («темно_синий» → «синий»)
  for pref in _COLOR_PREFIXES:
    if s.startswith(pref):
      rest = s[len(pref):].lstrip("_")
      if rest in _COLOR_ALIASES:
        return _COLOR_ALIASES[rest]
      if rest in CANON_COLORS:
        return rest
      if rest:
        s = rest
      break
  # поиск по стемам (для свободного текста)
  for stem, canon in _COLOR_STEMS:
    if stem in s:
      return canon
  return ""


def canon_colors_from_text(values: Any) -> list[str]:
  """Свободный список фраз (палитра из фото-анализа и т.п.) → канонические цвета."""
  if values is None:
    return []
  if isinstance(values, str):
    values = _split_listish(values)
  out: list[str] = []
  for x in values or []:
    c = canon_color_token(str(x))
    if c and c not in out:
      out.append(c)
  return out

# Canonical categories align with outfit_service candidate() lookups (Russian + English fallbacks).
_CATEGORY_ALIASES: dict[str, str] = {
  "tshirt": "футболки",
  "t-shirt": "футболки",
  "t_shirt": "футболки",
  "tee": "футболки",
  "tees": "футболки",
  "top": "футболки",
  "tops": "футболки",
  "tshirts": "футболки",
  "футболка": "футболки",
  "футболки": "футболки",
  "shirt": "рубашки",
  "shirts": "рубашки",
  "рубашка": "рубашки",
  "рубашки": "рубашки",
  "blouse": "рубашки",
  "jean": "джинсы",
  "jeans": "джинсы",
  "denim": "джинсы",
  "джинсы": "джинсы",
  "trouser": "брюки",
  "trousers": "брюки",
  "pants": "брюки",
  "chinos": "брюки",
  "брюки": "брюки",
  "outerwear": "верхний_слой",
  "outer": "верхний_слой",
  "jacket": "верхний_слой",
  "coat": "верхний_слой",
  "layer": "верхний_слой",
  "верхний_слой": "верхний_слой",
  "верхнийслой": "верхний_слой",
  "пальто": "верхний_слой",
  "куртка": "верхний_слой",
  "куртки": "верхний_слой",
  "жакет": "верхний_слой",
  "пиджак": "верхний_слой",
  "shoe": "обувь",
  "shoes": "обувь",
  "sneakers": "обувь",
  "boots": "обувь",
  "sneaker": "обувь",
  "boot": "обувь",
  "обувь": "обувь",
  "bag": "сумки",
  "bags": "сумки",
  "сумка": "сумки",
  "сумки": "сумки",
  "accessory": "аксессуары",
  "accessories": "аксессуары",
  "аксессуар": "аксессуары",
  "аксессуары": "аксессуары",
  "hoodie": "худи",
  "hoodies": "худи",
  "худи": "худи",
  "sweatshirt": "худи",
  "dress": "платья",
  "dresses": "платья",
  "платье": "платья",
  "платья": "платья",
  "skirt": "юбки",
  "skirts": "юбки",
  "юбка": "юбки",
  "sport": "спорт",
  "sportswear": "спорт",
  "спорт": "спорт",
  # --- реальные категории Admitad-фидов (Befree, Baon, Sela, Aim Clo и др.) ---
  "майка": "футболки",
  "майки": "футболки",
  "топ": "футболки",
  "топы": "футболки",
  "поло": "футболки",
  "лонгслив": "футболки",
  "лонгсливы": "футболки",
  "блуза": "рубашки",
  "блузы": "рубашки",
  "блузка": "рубашки",
  "блузки": "рубашки",
  "сорочка": "рубашки",
  "сорочки": "рубашки",
  "шорты": "шорты",
  "shorts": "шорты",
  "брюки_спортивные": "спорт",
  "легинсы": "спорт",
  "леггинсы": "спорт",
  "свитшот": "худи",
  "свитшоты": "худи",
  "толстовка": "худи",
  "толстовки": "худи",
  "джемпер": "джемперы",
  "джемперы": "джемперы",
  "джемпера": "джемперы",
  "свитер": "джемперы",
  "свитеры": "джемперы",
  "свитера": "джемперы",
  "пуловер": "джемперы",
  "пуловеры": "джемперы",
  "водолазка": "джемперы",
  "водолазки": "джемперы",
  "кардиган": "верхний_слой",
  "кардиганы": "верхний_слой",
  "бомбер": "верхний_слой",
  "бомберы": "верхний_слой",
  "ветровка": "верхний_слой",
  "ветровки": "верхний_слой",
  "пуховик": "верхний_слой",
  "пуховики": "верхний_слой",
  "плащ": "верхний_слой",
  "плащи": "верхний_слой",
  "тренч": "верхний_слой",
  "тренчи": "верхний_слой",
  "жилет": "верхний_слой",
  "жилеты": "верхний_слой",
  "пиджаки_и_жакеты": "верхний_слой",
  "жакеты": "верхний_слой",
  "пиджаки": "верхний_слой",
  "костюм": "костюмы",
  "костюмы": "костюмы",
  "комбинезон": "комбинезоны",
  "комбинезоны": "комбинезоны",
  "сарафан": "платья",
  "сарафаны": "платья",
  "кроссовки": "обувь",
  "кеды": "обувь",
  "ботинки": "обувь",
  "туфли": "обувь",
  "сандалии": "обувь",
  "босоножки": "обувь",
  "лоферы": "обувь",
  "сапоги": "обувь",
  "сабо_и_мюли": "обувь",
  "сабо": "обувь",
  "мюли": "обувь",
  "тапочки": "обувь",
  "украшения": "аксессуары",
  "украшение": "аксессуары",
  "бижутерия": "аксессуары",
  "ремни": "аксессуары",
  "ремень": "аксессуары",
  "очки": "аксессуары",
  "шапки": "аксессуары",
  "шапка": "аксессуары",
  "шарфы": "аксессуары",
  "шарф": "аксессуары",
  "перчатки": "аксессуары",
  "кепки": "аксессуары",
  "кепка": "аксессуары",
  "панамы": "аксессуары",
  "аксессуары_для_волос": "аксессуары",
  "галстуки": "аксессуары",
  "кошельки": "аксессуары",
  "рюкзаки": "сумки",
  "рюкзак": "сумки",
  # бельё и купальники — отдельные категории, чтобы не попадать в обычную ленту одежды
  "носки": "бельё",
  "трусы": "бельё",
  "бюстгальтеры": "бельё",
  "бюстгальтер": "бельё",
  "белье": "бельё",
  "бельё": "бельё",
  "нижнее_белье": "бельё",
  "нижнее_бельё": "бельё",
  "колготки": "бельё",
  "пижамы": "бельё",
  "пижама": "бельё",
  "термобелье": "бельё",
  "термобельё": "бельё",
  "плавки": "купальники",
  "купальники": "купальники",
  "купальник": "купальники",
  "лифы_купальные": "купальники",
  "пляжная_одежда": "купальники",
  "парки": "верхний_слой",
  "парка": "верхний_слой",
  "шубы": "верхний_слой",
  "шуба": "верхний_слой",
  "дубленки": "верхний_слой",
  "дублёнки": "верхний_слой",
  "анораки": "верхний_слой",
  "анорак": "верхний_слой",
  "ботильоны": "обувь",
  "кроссовки_и_кеды": "обувь",
  "угги": "обувь",
  "слипоны": "обувь",
  "эспадрильи": "обувь",
  "боди": "футболки",
  "платки": "аксессуары",
  "варежки": "аксессуары",
  "береты": "аксессуары",
  "часы": "аксессуары",
}

_CANONICAL_CATEGORIES = frozenset(_CATEGORY_ALIASES.values())

# Onboarding / fit-profile interest tags (English keys from TZ)
_INTEREST_TAG_ALIASES: dict[str, str] = {
  "casual": "футболки",
  "sport": "спорт",
  "office": "брюки",
  "streetwear": "футболки",
  "outerwear": "верхний_слой",
  "street": "футболки",
  "minimal": "футболки",
  "classic": "рубашки",
  "smart_casual": "рубашки",
  "smart casual": "рубашки",
}

_STYLE_SCENARIO_ALIASES: dict[str, str] = {
  "daily": "daily",
  "office": "office",
  "evening": "evening",
  "casual": "daily",
  "minimal": "daily",
  "classic": "office",
  "street": "daily",
  "smart_casual": "office",
  "smart casual": "office",
}

_MALE_HINTS = (
  "мужск",
  "mens",
  "men's",
  " men ",
  "male",
  "для мужчин",
)
_FEMALE_HINTS = (
  "женск",
  "womens",
  "women's",
  " women ",
  "female",
  "для женщин",
  "ladies",
)


def normalize_category(raw: str | None) -> str:
  if raw is None:
    return ""
  s = str(raw).strip().lower().replace(" ", "_").replace("-", "_")
  if not s:
    return ""
  if s in _CATEGORY_ALIASES:
    return _CATEGORY_ALIASES[s]
  if s in _CANONICAL_CATEGORIES:
    return s
  # Составные названия («домашние_шорты», «джемперы_и_свитеры»,
  # «Мужское/Футболки», «кожаные_куртки») — ищем знакомое слово внутри
  s_split = re.sub(r"[/>,|]+", "_", s)
  words = [w for w in s_split.split("_") if w and w not in ("и", "для", "с")]
  if len(words) > 1:
    for w in reversed(words):  # главное слово обычно последнее
      if w in _CATEGORY_ALIASES:
        return _CATEGORY_ALIASES[w]
      if w in _CANONICAL_CATEGORIES:
        return w
    for w in words:
      if w in _CATEGORY_ALIASES:
        return _CATEGORY_ALIASES[w]
      if w in _CANONICAL_CATEGORIES:
        return w
  return s


def normalize_product_colors(raw: list[str] | None) -> list[str]:
  out: list[str] = []
  for x in raw or []:
    c = canon_color_token(str(x))
    if c and c not in out:
      out.append(c)
  return out


def normalize_colors_value(raw: Any) -> list[str]:
  if raw is None:
    return []
  if isinstance(raw, str):
    items = _split_listish(raw)
  elif isinstance(raw, list):
    items = raw
  else:
    items = [str(raw)]
  return normalize_product_colors([str(x) for x in items if str(x).strip()])


_SIZE_SPLIT_RE = re.compile(r"[,;|/]+")


def _split_listish(s: str) -> list[str]:
  parts = [p.strip() for p in _SIZE_SPLIT_RE.split(s) if p.strip()]
  return parts if parts else ([s.strip()] if s.strip() else [])


_LETTER_SIZE_RE = re.compile(r"^(XXS|XS|S|M|L|XL|XXL|XXXL|\d?XL)$", re.I)
_NUMERIC_SIZE_RE = re.compile(r"^(\d{2})$")
_WAIST_RE = re.compile(r"^W\d{2,3}$", re.I)


def normalize_size_token(tok: str, *, size_system: str | None = None) -> str:
  t = str(tok).strip()
  if not t:
    return ""
  u = t.upper().replace(" ", "")
  if _WAIST_RE.match(u):
    return u
  if _LETTER_SIZE_RE.match(u):
    return u
  if _NUMERIC_SIZE_RE.match(u):
    return u
  return u


def normalize_sizes(raw: Any, *, size_system: str | None = None) -> list[str]:
  if raw is None:
    return []
  if isinstance(raw, str):
    items = _split_listish(raw)
  elif isinstance(raw, list):
    items = [str(x) for x in raw]
  else:
    items = [str(raw)]
  out: list[str] = []
  for x in items:
    s = normalize_size_token(x, size_system=size_system)
    if s and s not in out:
      out.append(s)
  return out


def normalize_size_system(raw: str | None) -> str | None:
  if raw is None or not str(raw).strip():
    return None
  s = str(raw).strip().upper().replace(" ", "_")
  aliases = {
    "INT": "INT",
    "INTERNATIONAL": "INT",
    "EU": "EU",
    "EURO": "EU",
    "US": "US",
    "USA": "US",
    "UK": "UK",
    "RU": "RU",
    "RUS": "RU",
    "LETTER": "LETTER",
    "LETTERS": "LETTER",
  }
  return aliases.get(s, s if len(s) <= 16 else s[:16])


def normalize_interest_category(tag: str | None) -> str:
  if tag is None:
    return ""
  s = str(tag).strip().lower().replace(" ", "_")
  if not s:
    return ""
  if s in _INTEREST_TAG_ALIASES:
    return _INTEREST_TAG_ALIASES[s]
  return normalize_category(s)


def normalize_style_scenario(tag: str | None) -> str:
  if tag is None:
    return ""
  s = str(tag).strip().lower().replace(" ", "_")
  if not s:
    return ""
  return _STYLE_SCENARIO_ALIASES.get(s, s)


def normalize_gender_target(raw: str | None, *, title: str = "", category: str = "") -> str | None:
  if raw:
    g = str(raw).strip().lower()
    if g in ("male", "m", "man", "mens", "menswear", "мужской", "муж"):
      return "menswear"
    if g in ("female", "f", "woman", "womens", "womenswear", "женский", "жен"):
      return "womenswear"
    if g in ("unisex", "uni", "унисекс"):
      return "unisex"
    if g in ("menswear", "womenswear"):
      return g
  return infer_gender_from_text(f"{title} {category}")


_FEMALE_LEANING_CATEGORIES = frozenset({"платья", "юбки"})


def resolve_product_gender(
  *,
  gender_target: str | None = None,
  title: str = "",
  category: str = "",
  category_name: str = "",
  merchant_category: str = "",
) -> str | None:
  """Определяет пол товара: поле gender_target → категория → текст."""
  gt = normalize_gender_target(
    gender_target,
    title=title,
    category=f"{category} {category_name}",
  )
  if gt in ("menswear", "womenswear", "unisex"):
    return gt
  for raw in (category, category_name, merchant_category):
    c = normalize_category(str(raw or "").strip())
    if c in _FEMALE_LEANING_CATEGORIES:
      return "womenswear"
  return infer_gender_from_text(
    f"{title} {category} {category_name} {merchant_category}"
  )


def product_gender_from_model(product) -> str | None:
  """Product ORM → resolved gender."""
  return resolve_product_gender(
    gender_target=getattr(product, "gender_target", None),
    title=getattr(product, "title", "") or "",
    category=getattr(product, "category", "") or "",
    category_name=getattr(product, "category_name", "") or "",
    merchant_category=getattr(product, "merchant_category", "") or "",
  )


def infer_gender_from_text(text: str) -> str | None:
  t = (text or "").lower()
  if not t.strip():
    return None
  has_m = any(h in t for h in _MALE_HINTS)
  has_f = any(h in t for h in _FEMALE_HINTS)
  if has_m and not has_f:
    return "menswear"
  if has_f and not has_m:
    return "womenswear"
  return None


_LETTER_SIZE_ORDER: dict[str, int] = {
  "XXS": 0,
  "XS": 1,
  "S": 2,
  "M": 3,
  "L": 4,
  "XL": 5,
  "XXL": 6,
  "XXXL": 7,
}


def letter_size_index(size: str) -> int | None:
  u = normalize_size_token(size)
  if not u:
    return None
  return _LETTER_SIZE_ORDER.get(u.upper())


def infer_size_system(sizes: list[str]) -> str | None:
  if not sizes:
    return None
  letters = 0
  nums = 0
  waist = 0
  for z in sizes:
    u = str(z).upper()
    if _WAIST_RE.match(u):
      waist += 1
    elif _LETTER_SIZE_RE.match(u):
      letters += 1
    elif _NUMERIC_SIZE_RE.match(u):
      nums += 1
  if waist >= max(1, len(sizes) // 2):
    return "US"
  if letters >= max(1, len(sizes) // 2):
    return "LETTER"
  if nums >= max(1, len(sizes) // 2):
    return "EU"
  return None
