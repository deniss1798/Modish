"""Roles describe the garment being sold, not the outfit in its photograph."""
import re
from ..catalog_normalize import normalize_category

def product_role(p) -> str | None:
    title=str(getattr(p,'title','') or '').lower().replace('ё','е')
    cats={normalize_category(str(getattr(p,k,'') or '')) for k in ('category','category_name','subcategory')}
    # Multi-piece sets and bib overalls require additional layers we do not model.
    if re.search(r'комбинез|полукомбинез|комплект|\b(?:suit|jumpsuit|overalls|dungarees|set)\b',title) or cats & {'костюмы','комбинезоны'}:
        return None
    if re.search(r'плать|сарафан',title):return 'one_piece'
    if re.search(r'куртк|пальто|пухов|ветров|парка|плащ|жилет|кардиган|\b(?:coat|jacket|cardigan|parka)\b',title):return 'outerwear'
    if re.search(r'брюк|штан|джинс|джоггер|шорт|юбк|леггин|легин|лосин|\b(?:pants|trousers|jeans|joggers|shorts|skirt|leggings)\b',title):return 'bottom'
    if re.search(r'рубаш|сорочк|поло|футболк|майк|блуз|джемпер|свитер|свитшот|толстов|худи|водолаз|лонгслив|\b(?:топ|shirt|polo|t-shirt|top|sweater|hoodie|sweatshirt|tank)\b',title):return 'top'
    # Brand names such as DC Shoes must not override the sold "Майка" above.
    if re.search(r'туфл|лофер|мокасин|кроссов|\bкед[ыа]?\b|ботин|сапог|сандал|босонож|балетк|\b(?:shoes|sneakers|loafers|boots)\b',title):return 'shoes'
    if re.search(r'\bdress\b',title) or 'платья' in cats:return 'one_piece'
    if 'обувь' in cats:return 'shoes'
    if cats & {'брюки','джинсы','шорты','юбки'}:return 'bottom'
    if cats & {'футболки','рубашки','джемперы','худи','блузки','топы'}:return 'top'
    return None
