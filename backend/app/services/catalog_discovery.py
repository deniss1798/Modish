"""Explicit browse-only catalogue; unknown size never becomes a fit claim.

This read service does not initialize profiles, log recommendation exposures or
train taste. Its only relaxed admission rule is exact size matching, and only
callers of the dedicated discovery routes use it.
"""
from __future__ import annotations
from ..money import as_rubles

import base64
from dataclasses import dataclass
from datetime import datetime, timezone
import hashlib
import json

from sqlalchemy import func, select

from ..models import ActivationSession, FitProfile, Product, StyleProfile, UserProductState
from ..schemas.api_product import product_to_api
from .activation_policy import EXCLUDED_SOURCES, SEGMENT_BUDGETS
from .catalog.catalog_quality import product_is_feed_eligible
from .catalog.rule_filters import load_rules_by_source_id, product_gender_compatible, product_passes_source_rules
from .catalog.size_resolver import resolve_product_sizes
from .catalog.stock_freshness import availability_snapshot, available_product_conditions
from .garment_roles import product_role
from .outfit_quality import is_sports_item
from .product_identity import product_identity_keys
from .product_search import search_conditions

DISCOVERY_FEATURE = 'catalogue_discovery_v1'
SIZE_NOTICE = 'Размер нужно уточнить у магазина'
MAX_PRICE = 25000
MIN_PRICE = 300
MAX_PAGE_SIZE = 40
BATCH_SIZE = 200
_ROLES = {'shoes': 'shoes', 'outerwear': 'outerwear', 'tops': 'top',
          'bottoms': 'bottom', 'dresses': 'one_piece'}
_ALLOWED_ROLES = frozenset(_ROLES.values())
_ALIASES = {'обувь': 'shoes', 'верхняя одежда': 'outerwear',
            'верхняя_одежда': 'outerwear', 'верхний_слой': 'outerwear',
            'платья': 'dresses'}


class DiscoveryError(ValueError):
  def __init__(self, message, *, status=422):
    super().__init__(message)
    self.status = status


@dataclass(frozen=True)
class DiscoveryScope:
  gender_target: str
  gender_basis: str
  budget_max: int
  budget_basis: str


def _scope(db, user):
  # Missing/unconfirmed Fit does not block browsing and is never filled in here.
  fit = db.scalar(select(FitProfile).where(FitProfile.user_id == user.id))
  gender = getattr(fit, 'gender_target', None)
  gender_confirmed = gender in {'womenswear', 'menswear'}
  budget = getattr(fit, 'budget_max', None)
  if budget is not None and int(budget) > 0:
    maximum, basis = min(MAX_PRICE, int(budget)), 'profile'
  else:
    profile = db.scalar(select(StyleProfile).where(StyleProfile.user_id == user.id))
    segment = getattr(profile, 'price_segment', None)
    maximum = SEGMENT_BUDGETS.get(segment, SEGMENT_BUDGETS['mass'])
    basis = 'profile_segment' if segment in SEGMENT_BUDGETS else 'catalogue_default'
  return DiscoveryScope(gender if gender_confirmed else 'womenswear',
      'profile' if gender_confirmed else 'catalogue_default', maximum, basis)


def _category(value):
  value = str(value or '').strip().lower()
  value = _ALIASES.get(value, value)
  if value and value not in _ROLES:
    raise DiscoveryError('Неизвестная категория каталога')
  return value


def _hidden(db, user, now):
  ids = set(db.scalars(select(UserProductState.product_id).where(
      UserProductState.user_id == user.id, UserProductState.hidden_until.is_not(None),
      UserProductState.hidden_until > now)))
  activation = db.get(ActivationSession, user.id)
  ids.update(pid for pid, reaction in (getattr(activation, 'swipes_json', None) or {}).items()
             if reaction in {'dislike', 'unavailable'})
  return ids


def _hidden_keys(db, hidden):
  keys = set()
  ids = sorted(hidden)
  for offset in range(0, len(ids), BATCH_SIZE):
    with db.execute(select(Product.id, Product.original_url, Product.source,
            Product.group_id, Product.vendor_code, Product.image_url)
            .where(Product.id.in_(ids[offset:offset + BATCH_SIZE]))) as rows:
      for row in rows:
        keys.update(product_identity_keys(row))
  return keys


def _allowed(product, scope, rules, now, *, category='', minimum=MIN_PRICE, maximum=None):
  source = str(product.source or '').strip().lower()
  if not source or source in EXCLUDED_SOURCES:
    return False
  if str(product.currency or '').upper() not in {'RUB', 'RUR'}:
    return False
  if not minimum <= as_rubles(product.price) <= (scope.budget_max if maximum is None else maximum):
    return False
  if product.gender_target not in {scope.gender_target, 'unisex'} or not product_gender_compatible(product, scope.gender_target):
    return False
  role = product_role(product)
  if role not in _ALLOWED_ROLES or (category and role != _ROLES[category]):
    return False
  return (product_is_feed_eligible(product, checked_at=now)
      and availability_snapshot(product, checked_at=now).available
      and product_passes_source_rules(product, rules) and not is_sports_item(product))


def discovery_product_to_api(product):
  sizes = []
  for size in resolve_product_sizes(product):
    sizes.append({'raw_size': size.raw_size,
        'normalized_size': size.normalized_size if size.known else None,
        'size_system': size.size_system if size.known else 'UNKNOWN',
        'availability': size.availability})
  known_systems = {size['size_system'] for size in sizes}
  # A denormalized legacy system column must not override current evidence.
  system = next(iter(known_systems)) if len(known_systems) == 1 and 'UNKNOWN' not in known_systems else None
  return {**product_to_api(product), 'size_system': system, 'catalogue_discovery': True,
      'size_verification': 'not_verified', 'merchant_sizes': sizes,
      'size_notice': SIZE_NOTICE}


def _fingerprint(user, scope, category, q, source, minimum, maximum, hidden):
  values = {'user': user.id, 'gender': scope.gender_target,
      'category': category, 'q': q, 'source': source, 'min': minimum,
      'max': maximum, 'hidden': sorted(hidden)}
  return hashlib.sha256(json.dumps(values, sort_keys=True,
      ensure_ascii=True, separators=(',', ':')).encode()).hexdigest()


def _cursor(after, fingerprint):
  raw = json.dumps({'v': 1, 'after': after, 'scope': fingerprint}, separators=(',', ':')).encode()
  return base64.urlsafe_b64encode(raw).decode().rstrip('=')


def _read_cursor(value, fingerprint):
  if not value:
    return None
  try:
    raw = base64.b64decode(value + '=' * (-len(value) % 4), altchars=b'-_', validate=True)
    data = json.loads(raw)
    if (not isinstance(data, dict) or set(data) != {'v', 'after', 'scope'}
        or data['v'] != 1 or data['scope'] != fingerprint
        or not isinstance(data['after'], str) or not 1 <= len(data['after']) <= 36):
      raise ValueError('Invalid cursor scope')
    return data['after']
  except (ValueError, TypeError, UnicodeDecodeError) as error:
    raise DiscoveryError('Страница каталога изменилась. Обновите каталог.') from error


def catalogue_page(db, user, *, category=None, q=None, source=None,
                   min_price=None, max_price=None, cursor=None, limit=20):
  if not 1 <= limit <= MAX_PAGE_SIZE:
    raise DiscoveryError('Размер страницы должен быть от 1 до 40')
  if min_price is not None and min_price < 0 or max_price is not None and max_price < 0:
    raise DiscoveryError('Цена не может быть отрицательной')
  if min_price is not None and max_price is not None and min_price > max_price:
    raise DiscoveryError('Минимальная цена больше максимальной')
  if len(str(q or '')) > 160 or len(str(source or '')) > 64 or len(str(cursor or '')) > 1024:
    raise DiscoveryError('Параметр каталога слишком длинный')
  category, q, source = _category(category), ' '.join(str(q or '').lower().split()), str(source or '').strip().lower()
  scope = _scope(db, user)
  minimum = max(MIN_PRICE, min_price or 0)
  maximum = min(scope.budget_max, max_price if max_price is not None else scope.budget_max)
  now = datetime.now(timezone.utc)
  hidden = _hidden(db, user, now)
  fingerprint = _fingerprint(user, scope, category, q, source, minimum, maximum, hidden)
  after = _read_cursor(cursor, fingerprint)
  query = select(Product).where(*available_product_conditions(checked_at=now),
      Product.gender_target.in_([scope.gender_target, 'unisex']),
      Product.price.between(minimum, maximum),
      func.upper(Product.currency).in_(['RUB', 'RUR']),
      func.lower(func.trim(Product.source)).not_in(EXCLUDED_SOURCES))
  if source:
    query = query.where(func.lower(func.trim(Product.source)) == source)
  if q:
    query = query.where(*search_conditions(q))
  rules = load_rules_by_source_id(db)
  seen = _hidden_keys(db, hidden)
  products = []
  cursor_valid = after is None
  last_id = None
  has_more = False
  # Reconstruct prior visible identities in bounded ORM batches rather than
  # carrying an ever-growing client cursor or a process-local pagination cache.
  # Identity strings are O(admitted prefix), and late pages rescan that prefix.
  with db.scalars(query.order_by(Product.id).execution_options(yield_per=BATCH_SIZE)) as rows:
    for product in rows:
      if product.id in hidden or not _allowed(product, scope, rules, now,
          category=category, minimum=minimum, maximum=maximum):
        continue
      keys = product_identity_keys(product)
      if keys & seen:
        continue
      seen.update(keys)
      if after is not None and product.id <= after:
        if product.id == after:
          cursor_valid = True
        continue
      if not cursor_valid:
        raise DiscoveryError('Страница каталога изменилась. Обновите каталог.')
      if len(products) == limit:
        has_more = True
        break
      products.append(discovery_product_to_api(product))
      last_id = product.id
  if not cursor_valid:
    raise DiscoveryError('Страница каталога изменилась. Обновите каталог.')
  return {'products': products, 'next_cursor': _cursor(last_id, fingerprint) if has_more else None,
      'has_more': has_more, 'size_verification': 'not_verified',
      'mode': 'catalogue_discovery', 'personalized': False,
      'scope': {'gender_target': scope.gender_target, 'gender_basis': scope.gender_basis,
                'budget_max': maximum, 'budget_basis': scope.budget_basis}}


def catalogue_detail(db, user, product_id):
  scope = _scope(db, user)
  now = datetime.now(timezone.utc)
  product = db.get(Product, product_id)
  if product is None or not _allowed(product, scope, load_rules_by_source_id(db), now):
    raise DiscoveryError('Товар временно недоступен', status=404)
  hidden = _hidden(db, user, now)
  if product.id in hidden or product_identity_keys(product) & _hidden_keys(db, hidden):
    raise DiscoveryError('Товар временно недоступен', status=404)
  return discovery_product_to_api(product)
