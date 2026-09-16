"""Only clothing and footwear belong in the stylist's catalogue."""
import html
import re

from ..catalog_normalize import normalize_category

_NON_FASHION = re.compile(
    r'подушк|наволоч|одеял|постель|простын|полотен|скатерт|салфет|'
    r'сыворотк|шампун|космет|маска\s+для|крем\s+для|гель\s+для|'
    r'парфюм|духи\b|дезодорант|помад|тушь\b|бальзам|лосьон|'
    r'блюдо|тарелк|посуда|кастрюл|сковород|чайник|чашк|стакан|бокал|'
    r'коврик|ковер|плед|штор|свеч[аи]|светильник|игрушк|гирлянд|'
    r'чехол|вешалк|органайзер|стельк|шнурк|средство\s+для|'
    r'\b(?:pillow|cushion|serum|cosmetics|shampoo|tableware|bedding|towel|curtain|skincare)\b', re.I)
_GARMENT = re.compile(
    r'футболк|рубашк|сорочк|блуз|поло\b|майк|\bтоп\b|лонгслив|'
    r'джемпер|свитер|пуловер|водолаз|свитшот|худи|толстов|'
    r'брюк|штан|джинс|шорт|юбк|леггин|легин|лосин|плать|сарафан|'
    r'пиджак|жакет|куртк|пальто|пуховик|плащ|ветровк|парка|жилет|кардиган|'
    r'костюм|комбинезон|купальник|плавки|пижам|халат|бюстгальтер|трусы|'
    r'туфл|лофер|мокасин|кроссов|\bкед[ыа]?\b|ботин|сапог|сандал|босонож|балетк|'
    r'\b(?:shirt|t-shirt|blouse|polo|sweater|hoodie|trousers|pants|jeans|shorts|'
    r'skirt|dress|coat|jacket|cardigan|jumpsuit|sneakers|shoes|boots|loafers)\b', re.I)
_CATEGORIES = {'футболки','рубашки','блузки','топы','джемперы','худи','брюки',
    'джинсы','шорты','юбки','платья','костюмы','комбинезоны','верхняя одежда',
    'куртки','пальто','обувь','купальники','бельё'}


def product_is_fashion(product) -> bool:
    title = html.unescape(str(getattr(product, 'title', '') or '')).lower().replace('ё', 'е')
    if _NON_FASHION.search(title):
        return False
    if _GARMENT.search(title):
        return True
    categories = ' '.join(str(getattr(product, key, '') or '') for key in
        ('category', 'category_name', 'subcategory', 'merchant_category', 'merchant_subcategory'))
    if _NON_FASHION.search(categories):
        return False
    return bool(_GARMENT.search(categories)) or any(
        normalize_category(str(getattr(product, key, '') or '')) in _CATEGORIES
        for key in ('category', 'category_name', 'subcategory'))


def fashion_catalog_conditions():
    """Preselect before LIMIT so home/beauty stock cannot exhaust the pool."""
    from sqlalchemy import func, or_
    from ..models import Product
    text = func.lower(Product.title + ' ' + func.coalesce(Product.category, ''))
    markers = ('футбол','рубаш','блуз','поло','майк','топ','джемпер','свитер','худи',
        'толстов','лонгслив','брюк','штан','джинс','шорт','юбк','плать','сарафан',
        'куртк','пальто','пухов','ветров','плащ','жилет','кардиган','жакет','пиджак',
        'костюм','комбинез','обув','кроссов','туфл','ботин','сапог','лофер','кеды',
        'босонож','сандал','купаль','водолаз','свитшот','пуловер','балетк','мокасин',
        'shirt','blouse','sweater','hoodie','pants','trousers','jeans','shorts','skirt',
        'dress','jacket','coat','shoes','sneaker','boots','loafers')
    return [or_(*(text.contains(marker) for marker in markers))]
