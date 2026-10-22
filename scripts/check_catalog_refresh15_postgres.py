"""Real-catalogue QA on one new UUID-bound build15 PostgreSQL copy only.

Ten discovery profiles roll back. One synthetic HTTP owner completes real Style
Swipe and is removed with its own dependent rows. No merchant/AI request is made.
Unknown merchant sizes remain discovery-only and never receive a personal claim.
"""
from __future__ import annotations

import argparse
from contextlib import ExitStack
from datetime import datetime, timezone
import json
import logging
import os
from pathlib import Path
import re
import socket
import sys
from time import perf_counter
from unittest.mock import patch
from uuid import UUID, uuid4

ARCHIVE = '5184f378245db0073154369f1b6e083c8d584cb9006788993cd4727866e26c24'
HOST = 'modish_foundation_test_pg'
PG_ENV = {'PGHOST','PGHOSTADDR','PGPORT','PGDATABASE','PGUSER','PGPASSWORD',
          'PGSERVICE','PGSERVICEFILE','PGPASSFILE','PGOPTIONS','PGSSLMODE'}
PROFILES = (
    ('mass_m',10000,'M','LETTER'),('mass_l',10000,'L','LETTER'),
    ('economy_s',4000,'S','LETTER'),('economy_xl',4000,'XL','LETTER'),
    ('mid_m',25000,'M','LETTER'),('mid_xl',25000,'XL','LETTER'),
    ('numeric_ru_46',10000,'46','RU'),('numeric_unconfirmed_48',15000,'48',None),
    ('unknown_size',10000,None,None),('no_fit',None,None,None),
)
REPORT = {'scope':'real_catalogue_uuid_clone_build15','status':'running',
          'production_database_connected':False,'profile_count':10,'profiles':[],
          'http':{},'external_network_attempts':0,'fixture_cleanup_completed':False,
          'full_outfit_readiness_assumed':False}


class CheckError(RuntimeError):
    pass


def require(condition, code):
    if not condition:
        raise CheckError(code)


def environment_guard(run_id):
    # This function never imports application configuration or opens a socket.
    require(bool(re.fullmatch(r'[0-9a-f]{32}',run_id)) and UUID(run_id).version == 4,
            'uuid4_run_required')
    require(os.getenv('REFRESH_RUN_ID') == run_id and os.getenv('AUDIT_ARCHIVE_SHA256') == ARCHIVE,
            'matching_run_and_reviewed_archive_required')
    require(bool(re.fullmatch(r'sha256:[0-9a-f]{64}',os.getenv('AUDIT_IMAGE_ID',''))),
            'immutable_audited_image_required')
    require(os.getenv('APP_ENV') == 'test' and os.getenv('PYTHON_DOTENV_DISABLED') == '1'
            and os.getenv('PYTHONDONTWRITEBYTECODE') == '1'
            and not os.getenv('AUDIT_FIXED_NOW_UTC') and not PG_ENV.intersection(os.environ),
            'isolated_clean_test_environment_required')
    require(os.getenv('STYLE_VISUAL_ENABLED','false') == 'false'
            and os.getenv('CLOSED_ALPHA_ENABLED','false') == 'false'
            and os.getenv('ADMITAD_ENABLED','false') == 'false', 'external_features_disabled_required')
    from sqlalchemy.engine import make_url
    url = make_url(os.environ['DATABASE_URL'])
    require(url.get_backend_name() == 'postgresql' and url.host == HOST and url.port in (None,5432)
            and url.database == 'modish_refresh15_' + run_id
            and url.username == 'refresh15_' + run_id and not url.query,
            'exact_uuid_clone_database_and_role_required')
    return url


def install_network_guard(stack):
    # Libpq's C connection is independently pinned by URL and SQL identity.
    # Python sockets may resolve/connect only the same internal PostgreSQL host.
    original_dns, original_connect = socket.getaddrinfo, socket.socket.connect
    original_connect_ex, original_create = socket.socket.connect_ex, socket.create_connection
    addresses = {row[4][0] for row in original_dns(HOST,5432,type=socket.SOCK_STREAM)}

    def allowed(host, port):
        if isinstance(host,bytes):
            host = host.decode('ascii','replace')
        return host in ({HOST} | addresses) and str(port) == '5432'

    def denied():
        REPORT['external_network_attempts'] += 1
        raise CheckError('unexpected_external_network_attempt')

    def dns(host, port, *args, **kwargs):
        if not allowed(host,port):
            denied()
        return original_dns(host,port,*args,**kwargs)

    def connect(sock, address):
        if isinstance(address,tuple) and not allowed(address[0],address[1]):
            denied()
        return original_connect(sock,address)

    def connect_ex(sock, address):
        if isinstance(address,tuple) and not allowed(address[0],address[1]):
            denied()
        return original_connect_ex(sock,address)

    def create(address, *args, **kwargs):
        if not allowed(address[0],address[1]):
            denied()
        return original_create(address,*args,**kwargs)

    for target, replacement in (('socket.getaddrinfo',dns),('socket.socket.connect',connect),
                                ('socket.socket.connect_ex',connect_ex),('socket.create_connection',create)):
        stack.enter_context(patch(target,replacement))


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--run-id',required=True)
    parser.add_argument('--isolated-write-test',required=True,action='store_true')
    args = parser.parse_args()
    require(__debug__, 'python_optimization_forbidden')
    url = environment_guard(args.run_id)
    logging.disable(logging.CRITICAL)
    sys.path.insert(0,str(Path('/app') if Path('/app/app').is_dir()
                          else Path(__file__).resolve().parents[1]/'backend'))
    started = perf_counter()
    with ExitStack() as stack:
        # python-dotenv1.0.1 does not implement PYTHON_DOTENV_DISABLED; patch its
        # loader throughout this smoke as well as requiring an audited clean image.
        stack.enter_context(patch('dotenv.load_dotenv',return_value=False))
        install_network_guard(stack)
        from sqlalchemy import delete,event,func,select,text
        from fastapi.testclient import TestClient
        from app.db import Base,SessionLocal,engine
        from app.main import app
        from app.api.deps import create_access_token
        from app.models import (ActivationSession,FitProfile,Product,RecommendationEventV2,
                                TasteProfile,User)
        from app.services.catalog_discovery import catalogue_page,catalogue_detail,SIZE_NOTICE
        from app.services.catalog.catalog_quality import product_is_feed_eligible
        from app.services.catalog.stock_freshness import availability_snapshot,available_product_conditions
        from app.services.catalog.rule_filters import load_rules_by_source_id,product_passes_source_rules
        from app.services.garment_roles import product_role
        from app.services.product_identity import product_identity_keys
        from app.services.fit_size_profile import update_size_profile,submitted_sizes
        from app.services.personal_size_gate import evaluate_personal_size
        from app.services.product_eligibility import evaluate_eligibility
        from app.services.activation_policy import EXCLUDED_SOURCES
        from app.services.activation_service import _required_roles_available
        from app.services.outfit_quality import is_sports_item
        from app.money import as_rubles
        require(engine.url == url, 'actual_engine_url_mismatch')

        def transaction_bounds(connection):
            connection.exec_driver_sql("SET LOCAL statement_timeout='45s'")
            connection.exec_driver_sql("SET LOCAL lock_timeout='3s'")

        event.listen(engine,'begin',transaction_bounds)
        stack.callback(event.remove,engine,'begin',transaction_bounds)
        stack.callback(engine.dispose)
        with SessionLocal() as db:
            require(tuple(db.execute(text('SELECT current_database(),current_user,inet_server_port()')).one())
                    == (url.database,url.username,5432), 'actual_database_identity_mismatch')
            require(db.scalar(text('SELECT version_num FROM alembic_version')) == '20261015_0028',
                    'live_clone_schema_0028_required')
            before = {model.__tablename__:db.scalar(select(func.count()).select_from(model))
                      for model in (User,FitProfile,TasteProfile,RecommendationEventV2)}

        def discovery_rows(db,user,category,budget):
            first = catalogue_page(db,user,category=category,max_price=999999,limit=5)
            require(first['personalized'] is False and first['size_verification'] == 'not_verified',
                    'discovery_must_not_claim_personal_fit')
            require(first['scope']['budget_max'] <= min(25000,budget or 10000),
                    'discovery_budget_expansion')
            rows = list(first['products'])
            require(len(rows) <= 5, 'discovery_page_size_exceeded')
            if first['has_more']:
                require(bool(first['next_cursor']), 'discovery_cursor_missing')
                second = catalogue_page(db,user,category=category,max_price=999999,limit=5,
                                        cursor=first['next_cursor'])
                require(second['personalized'] is False and second['size_verification'] == 'not_verified',
                        'discovery_second_page_fit_claim')
                rows.extend(second['products'])
            seen_ids,seen_keys,unknown = set(),set(),0
            rules = load_rules_by_source_id(db)
            for row in rows:
                require(row['catalogue_discovery'] is True and row['size_verification'] == 'not_verified'
                        and row['size_notice'] == SIZE_NOTICE, 'discovery_size_notice_missing')
                require(not any(key in row for key in ('fit_score','size_matches','reasons')),
                        'discovery_unproven_fit_explanation')
                product = db.get(Product,row['id'])
                require(product is not None and product_is_feed_eligible(product)
                        and availability_snapshot(product).available, 'discovery_invalid_or_stale_sku')
                require(product.gender_target in {'womenswear','unisex'}
                        and str(product.source).lower() not in EXCLUDED_SOURCES
                        and product_passes_source_rules(product,rules) and not is_sports_item(product),
                        'discovery_source_or_segment_violation')
                require(300 <= as_rubles(row['price']) <= min(25000,budget or 10000),
                        'discovery_invalid_price_or_budget')
                if category:
                    require(product_role(product) == category, 'discovery_wrong_category')
                keys = product_identity_keys(product)
                require(product.id not in seen_ids and not seen_keys.intersection(keys),
                        'discovery_cross_page_identity_repeat')
                seen_ids.add(product.id);seen_keys.update(keys)
                for size in row['merchant_sizes']:
                    if size['size_system'] == 'UNKNOWN':
                        unknown += 1
                        require(size['normalized_size'] is None, 'unknown_size_was_normalized')
            if rows:
                detail = catalogue_detail(db,user,rows[0]['id'])
                require(detail['size_verification'] == 'not_verified' and detail['size_notice'] == SIZE_NOTICE,
                        'discovery_detail_fit_claim')
            return {'category':category or 'all','first_page':len(first['products']),
                    'checked_rows':len(rows),'unknown_unit_tokens':unknown}

        totals = {'all':0,'shoes':0,'outerwear':0}
        REPORT['phase'] = 'ten_profile_discovery'
        for name,budget,size,system in PROFILES:
            with SessionLocal() as db:
                try:
                    owner = User(id=str(uuid4()),email='qa15-'+uuid4().hex+'@example.invalid',password_hash='unused')
                    db.add(owner);db.flush()
                    if budget is not None:
                        fit = FitProfile(id=str(uuid4()),user_id=owner.id,height_cm=170,
                                         gender_target='womenswear',clothing_size=size,budget_max=budget)
                        update_size_profile(fit,sizes=submitted_sizes(clothing_size=size,clothing_system=system,
                            shoe_size='38',shoe_system='EU'),height_confirmed=True)
                        db.add(fit);db.flush()
                    evidence = [discovery_rows(db,owner,category,budget) for category in ('','shoes','outerwear')]
                    if budget is None:
                        require(all(row['checked_rows'] == 0 for row in evidence), 'unconfirmed_gender_was_invented')
                    for row in evidence:
                        totals[row['category']] += row['checked_rows']
                    require(db.scalar(select(func.count()).select_from(TasteProfile)) == before['taste_profiles']
                            and db.scalar(select(func.count()).select_from(RecommendationEventV2))
                            == before['recommendation_events_v2'], 'discovery_trained_taste_or_created_events')
                    REPORT['profiles'].append({'profile':name,'budget':budget,'clothing_size':size,
                                               'size_system':system,'categories':evidence})
                finally:
                    db.rollback()
        require(all(value > 0 for value in totals.values()), 'fresh_discovery_category_coverage_missing')
        REPORT['discovery_totals'] = totals

        owner = str(uuid4())
        fixture_started = False
        try:
            with SessionLocal() as db:
                require(db.get(User,owner) is None, 'new_synthetic_owner_required')
                db.add(User(id=owner,email='qa15-http-'+uuid4().hex+'@example.invalid',password_hash='unused'))
                fixture_started = True
                db.commit()
            headers = {'Authorization':'Bearer ' + create_access_token(owner)}
            with TestClient(app,raise_server_exceptions=False) as client:
                def request(method,path,expected=200,**kwargs):
                    stages = {'/health/ready':'readiness','/catalog/status':'catalogue_status',
                        '/products/brands':'brands','/feed':'feed','/onboarding/preferences':'preferences',
                        '/fit-profile/me':'fit_patch','/catalog/discovery':'discovery',
                        '/onboarding/style-swipe':'style_swipe','/onboarding/complete':'completion'}
                    REPORT['http_stage'] = stages.get(path,'discovery_detail')
                    response = client.request(method,path,headers=headers,**kwargs)
                    REPORT['last_http_status'] = response.status_code
                    require(response.status_code == expected, 'http_unexpected_status')
                    return response.json()

                REPORT['phase'] = 'reported_profile_http'
                health = request('GET','/health/ready')
                require(health.get('version') == '1.0.1-build15' and health.get('database') == 'ok',
                        'build15_http_readiness_required')
                status = request('GET','/catalog/status')
                require(status.get('status') == 'ready' and status.get('scope') == 'global'
                        and status.get('personalized') is False, 'fresh_global_catalogue_status_required')
                brands = request('GET','/products/brands',params={'limit':10})
                require(isinstance(brands,list) and bool(brands), 'fresh_brands_missing')
                require(client.get('/catalog/discovery').status_code == 401
                        and client.get('/catalog/discovery',headers={'Authorization':'Bearer invalid'}).status_code == 401,
                        'discovery_authentication_bypass')
                request('GET','/feed',expected=409,params={'limit':2})
                request('POST','/onboarding/preferences',json={
                    'gender_target':'womenswear','age_group':'25-34','clothing_size':'L','shoe_size':'38',
                    'clothing_size_system':'LETTER','shoe_size_system':'EU','styles':['casual','minimalism'],
                    'price_segment':'mid','height':166})
                profile = request('PATCH','/fit-profile/me',json={
                    'height':166,'weight':53,'gender_target':'womenswear','clothing_size':'L',
                    'clothing_size_system':'LETTER','shoe_size':'38','shoe_size_system':'EU',
                    'budget_max':100000,'sizes':{slot:{'value':'L','system':'LETTER'}
                        for slot in ('top','bottom','one_piece')}})
                require(profile['height'] == 166 and profile['weight'] == 53 and profile['budget_max'] == 100000
                        and not profile['missing_fit_requirements'], 'reported_profile_not_preserved')
                require(all(profile['size_profile']['sizes'][slot]['value'] == 'L'
                            and profile['size_profile']['sizes'][slot]['state'] == 'confirmed'
                            for slot in ('top','bottom','one_piece'))
                        and profile['size_profile']['sizes']['shoes']['value'] == '38'
                        and profile['size_profile']['sizes']['shoes']['system'] == 'EU',
                        'reported_category_sizes_not_confirmed')
                for category in ('shoes','outerwear'):
                    body = request('GET','/catalog/discovery',params={'category':category,'limit':2})
                    require(body['products'] and body['personalized'] is False, 'reported_discovery_category_missing')
                    for row in body['products']:
                        require(row['size_notice'] == SIZE_NOTICE and row['size_verification'] == 'not_verified',
                                'http_discovery_unproven_size_claim')
                    pid = body['products'][0]['id']
                    request('GET','/catalog/discovery/'+pid)
                    require(client.get('/catalog/discovery/'+pid).status_code == 401, 'discovery_detail_auth_bypass')
                request('GET','/catalog/discovery',expected=422,params={'limit':41})
                request('GET','/catalog/discovery',expected=422,params={'category':'unsupported'})
                request('GET','/catalog/discovery',expected=422,params={'cursor':'invalid'})
                request('GET','/feed',expected=409,params={'limit':2})

                def strict_rows(rows):
                    seen_ids,seen_keys = set(),set()
                    with SessionLocal() as db:
                        fit = db.scalar(select(FitProfile).where(FitProfile.user_id == owner))
                        rules = load_rules_by_source_id(db)
                        for row in rows:
                            # Feed deliveries wrap their product; onboarding cards are flat.
                            product_payload = row.get('product',row)
                            require(isinstance(product_payload,dict) and 'id' in product_payload,
                                    'personal_api_product_payload_missing')
                            product = db.get(Product,product_payload['id'])
                            require(product is not None and evaluate_eligibility(product,fit).eligible
                                    and evaluate_personal_size(product,fit).eligible
                                    and product.gender_target == 'womenswear'
                                    and str(product.currency).upper() in {'RUB','RUR'}
                                    and 300 <= as_rubles(product.price) <= 25000
                                    and str(product.source).lower() not in EXCLUDED_SOURCES
                                    and product_passes_source_rules(product,rules) and not is_sports_item(product),
                                    'personal_feed_or_swipe_invalid_real_sku')
                            if 'price' in product_payload:
                                require(as_rubles(product_payload['price']) == as_rubles(product.price),
                                        'personal_api_price_mismatch')
                            keys = product_identity_keys(product)
                            require(product.id not in seen_ids and not seen_keys.intersection(keys),
                                    'personal_response_identity_repeat')
                            seen_ids.add(product.id);seen_keys.update(keys)
                    return seen_ids,seen_keys

                deck = request('GET','/onboarding/style-swipe')
                require(isinstance(deck.get('products'),list) and len(deck['products']) >= 15,
                        'reported_profile_has_fewer_than_15_real_swipe_cards')
                selected = deck['products'][:15]
                strict_rows(selected)
                REPORT['phase'] = 'fifteen_real_http_swipes'
                for index,row in enumerate(selected,1):
                    reaction = request('POST','/onboarding/style-swipe',json={'product_id':row['id'],'reaction':'like'})
                    require(reaction.get('rated') == index, 'real_swipe_not_persisted')
                require(reaction.get('can_continue') is True, 'fifteen_real_swipes_not_warm')
                with SessionLocal() as db:
                    fit = db.scalar(select(FitProfile).where(FitProfile.user_id == owner))
                    activation = db.get(ActivationSession,owner)
                    rules = load_rules_by_source_id(db)
                    shoes = _required_roles_available(db,required_roles={'shoes'},
                        conditions=[*available_product_conditions(),Product.gender_target == 'womenswear',
                                    Product.price.between(300,25000)],hidden=set(),hidden_keys=set(),
                        fit=fit,session=activation,rules=rules,filters=None,context=None)
                    taste = db.scalar(select(TasteProfile).where(TasteProfile.user_id == owner))
                    require(taste is not None and any(float(value)>0 for value in (taste.category_weights or {}).values()),
                            'real_swipes_did_not_train_taste')
                    require(db.scalar(select(func.count()).select_from(RecommendationEventV2).where(
                        RecommendationEventV2.user_id == owner,RecommendationEventV2.event_type == 'like')) >= 15,
                        'real_swipe_events_missing')
                wow = request('POST','/onboarding/complete')
                require(wow.get('completed') is True and isinstance(wow.get('outfits'),list), 'warm_completion_failed')
                require(shoes or not wow['outfits'], 'complete_outfit_without_exact_available_shoes')
                if not wow['outfits']:
                    require(bool(wow.get('message')), 'empty_wow_missing_honest_explanation')
                for outfit in wow['outfits']:
                    require('shoes' in outfit['items'], 'complete_outfit_missing_shoes')
                    strict_rows([{'id':pid} for pid in outfit['items'].values()])
                REPORT['phase'] = 'warm_first_feed'
                feed = request('GET','/feed',params={'limit':10})
                require(isinstance(feed,list) and bool(feed), 'reported_warm_first_feed_empty')
                ids,keys = strict_rows(feed)
                second = request('GET','/feed',params=[('limit','10'),*(('exclude_ids',pid) for pid in sorted(ids))])
                next_ids,next_keys = strict_rows(second)
                require(not ids.intersection(next_ids) and not keys.intersection(next_keys),
                        'warm_feed_repeats_excluded_identity')
                REPORT['http'] = {'api_version':'1.0.1-build15','catalogue_status':'ready','brands_returned':len(brands),
                    'cold_feed_status':409,'authentication_status':401,'bad_input_status':422,
                    'reported_profile':{'height':166,'weight':53,'top':'L','bottom':'L','one_piece':'L',
                                        'shoes':'38','shoe_system':'EU','budget':100000},
                    'real_swipes':15,'taste_updated':True,'warm_completed':True,'first_feed_rows':len(feed),
                    'second_feed_rows':len(second),'cross_page_identity_repeats':0,
                    'exact_shoe_candidate_available':shoes,'wow_outfits':len(wow['outfits']),
                    'wow_empty_explained':bool(wow.get('message')) if not wow['outfits'] else None}
        finally:
            if fixture_started:
                with engine.begin() as connection:
                    for table in reversed(Base.metadata.sorted_tables):
                        if 'user_id' in table.c:
                            connection.execute(delete(table).where(table.c.user_id == owner))
                    connection.execute(delete(User).where(User.id == owner))
        with SessionLocal() as db:
            after = {model.__tablename__:db.scalar(select(func.count()).select_from(model))
                     for model in (User,FitProfile,TasteProfile,RecommendationEventV2)}
            require(after == before, 'synthetic_fixture_or_event_rows_left_behind')
            require(db.get(User,owner) is None, 'synthetic_http_owner_not_removed')
        REPORT['fixture_cleanup_completed'] = True
        require(REPORT['external_network_attempts'] == 0, 'unexpected_external_network_attempts_observed')
    REPORT.update(status='passed',run_id=args.run_id,archive_sha256=ARCHIVE,
                  phase='completed',
                  checked_at_utc=datetime.now(timezone.utc).isoformat(),seconds=round(perf_counter()-started,3))
    print(json.dumps(REPORT))


if __name__ == '__main__':
    try:
        main()
    except Exception as error:
        REPORT.update(status='failed',error_type=type(error).__name__,raw_error_omitted=True)
        if isinstance(error,CheckError):
            REPORT['check'] = str(error)
        print(json.dumps(REPORT))
        raise SystemExit(1) from None
