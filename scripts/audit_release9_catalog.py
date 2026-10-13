"""Read-only catalogue diagnostics, restricted to the release 9 test database."""
import os, json, sys
from sqlalchemy import select, func, cast, String
from sqlalchemy.engine import make_url
assert make_url(os.environ['DATABASE_URL']).host=='modish_release9_test_pg'
sys.path.insert(0,'/app')
import dotenv
dotenv.load_dotenv=lambda *a,**k: False
from app.db import SessionLocal
from app.models import Product, ActivationSession, FitProfile
from app.services.activation_service import product_ok, EXCLUDED_SOURCES, CATEGORIES
from app.services.catalog.rule_filters import load_rules_by_source_id, product_passes_source_rules
from app.services.activation_service import candidate_pool
from app.services.garment_roles import product_role
from app.services.outfit_quality import scenario_product_ok
from app.models import User
from app.catalog_normalize import normalize_category
with SessionLocal() as db:
    result={}
    q=select(Product).where(Product.is_active==1,Product.is_available==1,Product.gender_target=='womenswear',Product.price.between(300,4000),func.lower(Product.source).notin_(EXCLUDED_SOURCES))
    result['sources']=[list(r) for r in db.execute(select(Product.source,Product.gender_target,func.count()).group_by(Product.source,Product.gender_target))]
    result['budget_active']=db.scalar(select(func.count()).select_from(q.subquery()))
    result['categories']=[list(r) for r in db.execute(select(Product.category,func.count()).where(Product.id.in_(q.with_only_columns(Product.id))).group_by(Product.category))]
    result['size_counts']={s:db.scalar(select(func.count()).select_from(q.where(cast(Product.available_sizes,String).ilike(f'%"{s}"%')).subquery())) for s in ('XS','S','M','L','XL','42','44','36','38')}
    result['samples']=[{'title':p.title,'sizes':p.available_sizes,'gender':p.gender_target,'category':p.category,'source':p.source,'price':p.price} for p in db.scalars(q.limit(12))]
    activation=db.scalar(select(ActivationSession).order_by(ActivationSession.created_at.desc()))
    fit=db.scalar(select(FitProfile).where(FitProfile.user_id==activation.user_id))
    rules=load_rules_by_source_id(db)
    result['fit']={'size':fit.clothing_size,'shoe':activation.shoe_size,'budget':fit.budget_max,'gender':fit.gender_target}
    result['xs_candidates']=[{'title':p.title,'sizes':p.available_sizes,'category':p.category,'source':p.source,'ok':product_ok(p,fit,activation),'rules':product_passes_source_rules(p,rules)} for p in db.scalars(q.where(cast(Product.available_sizes,String).ilike('%"XS"%')).limit(12))]
    user=db.get(User,activation.user_id)
    pool=candidate_pool(db,user,activation)
    from collections import Counter
    result['pool_roles']=dict(Counter(product_role(p) for p in pool))
    result['scenario_roles']={s:dict(Counter(product_role(p) for p in pool if scenario_product_ok(p,s))) for s in ('daily','evening')}
    shoes=list(db.scalars(select(Product).where(Product.is_active==1,Product.gender_target=='womenswear',Product.price.between(300,25000),func.lower(Product.source).notin_(EXCLUDED_SOURCES),cast(Product.available_sizes,String).ilike('%"38"%'))))
    shoes=[p for p in shoes if product_role(p)=='shoes']
    result['known_shoes']=[{'title':p.title,'source':p.source,'category':p.category,'canonical':normalize_category(p.category),'sizes':p.available_sizes,'price':p.price,'ok':product_ok(p,fit,activation),'rules':product_passes_source_rules(p,rules)} for p in shoes[:10]]
    result['known_shoes_total']=len(shoes)
    from app.models import ProductSource
    result['shoppinglive_rules']=[{'type':r.rule_type,'value':r.rule_value} for source in db.scalars(select(ProductSource).where(ProductSource.code=='shoppinglive')) for r in rules.get(source.id,[])]
    result.pop('categories');result.pop('samples');result.pop('xs_candidates');result.pop('sources')
    print(json.dumps(result,ensure_ascii=False))
