"""Adult-only catalog gate. The app does not support children's clothing."""
import re
from ..models import Product

_KIDS = re.compile(r'детск|\bдети\b|для\s+детей|мальч|девоч|малыш|младен|подрост|школьн|новорожден|\b(?:kids?|junior|youth|boys?|girls?|baby|babies|toddler|teens?|children)\b',re.I)
_FIELDS=('title','category','category_name','subcategory','merchant_category','merchant_subcategory')

def product_is_adult(product: Product) -> bool:
    if any(_KIDS.search(str(getattr(product,k,'') or '')) for k in _FIELDS): return False
    for raw in (getattr(product,'raw_params_json',None),getattr(product,'feed_raw_json',None)):
        if not isinstance(raw,dict):continue
        for key,value in raw.items():
            key=str(key).lower()
            if any(term in key for term in ('age','возраст','пол','gender','category','категор')) and _KIDS.search(str(value or '')):
                return False
    lead=str(getattr(product,'description','') or '').split('\n',1)[0][:240]
    if _KIDS.search(lead):return False
    sizes=[str(s).strip() for s in (getattr(product,'available_sizes',None) or [])]
    # Height ranges like 122-128, 134-140 are children's sizes, not adult XL.
    ranges=[re.fullmatch(r'(\d{3})\s*[-–/]\s*(\d{3})',s) for s in sizes]
    if sizes and all(ranges) and min(int(m[1]) for m in ranges)<158 and max(int(m[2]) for m in ranges)<=176:
        return False
    if sizes and all(re.fullmatch(r'\d{2,3}',s) for s in sizes):
        heights=[int(s) for s in sizes]
        if min(heights)>=80 and min(heights)<=164 and max(heights)<=176:
            return False
    return True


def adult_catalog_conditions():
    from sqlalchemy import func,or_
    # Cheap preselection protects the limited candidate pool. The richer Python
    # gate handles source-specific fields and height ranges before returning rows.
    text=func.lower(Product.title+' '+func.coalesce(Product.category,''))
    markers=('детск','мальч','девоч','малыш','младен','подрост','школьн','новорожден','kids','junior',' boys',' girls','baby','toddler','youth','children')
    return [~or_(*(text.contains(marker) for marker in markers))]
