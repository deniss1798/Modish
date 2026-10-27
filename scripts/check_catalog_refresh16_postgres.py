"""Real-catalogue QA on one new UUID-bound build16 PostgreSQL copy only.

Ten discovery profiles roll back. One synthetic HTTP owner completes real Style
Swipe and is removed with its own dependent rows. No merchant/AI request is made.
Unknown merchant sizes remain discovery-only and never receive a personal claim.
"""
from __future__ import annotations

import argparse
from contextlib import ExitStack
from datetime import datetime, timezone
from html import unescape
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

HOST = 'modish_foundation_test_pg'
API_VERSION = '1.0.2-build16'
BASE_MESSAGE = 'Обувь не подобрана. Показана основа образа из одежды.'
PROFILES = (
    ('mass_m',10000,'M','LETTER'),('mass_l',10000,'L','LETTER'),
    ('economy_s',4000,'S','LETTER'),('economy_xl',4000,'XL','LETTER'),
    ('mid_m',25000,'M','LETTER'),('mid_xl',25000,'XL','LETTER'),
    ('numeric_ru_46',10000,'46','RU'),('numeric_unconfirmed_48',15000,'48',None),
    ('unknown_size',10000,None,None),('no_fit',None,None,None),
)
REPORT = {'scope':'real_catalogue_uuid_clone_build16','status':'running','phase':'environment_guard',
          'production_database_connected':False,'profile_count':10,'profiles':[],
          'http':{},'external_network_attempts':0,'fixture_cleanup_completed':False,
          'full_outfit_readiness_assumed':False}


class CheckError(RuntimeError):
    pass


def require(condition, code):
    if not condition:
        raise CheckError(code)


def environment_guard(run_id, archive_sha256):
    # This function never imports application configuration or opens a socket.
    require(bool(re.fullmatch(r'[0-9a-f]{32}',run_id)) and UUID(run_id).version == 4,
            'uuid4_run_required')
    require(bool(re.fullmatch(r'[0-9a-f]{64}',archive_sha256)), 'archive_sha256_required')
    require(os.getenv('REFRESH_RUN_ID') == run_id
            and os.getenv('AUDIT_ARCHIVE_SHA256') == archive_sha256,
            'matching_run_and_reviewed_archive_required')
    require(bool(re.fullmatch(r'sha256:[0-9a-f]{64}',os.getenv('AUDIT_IMAGE_ID',''))),
            'immutable_audited_image_required')
    require(os.getenv('APP_ENV') == 'test' and os.getenv('PYTHON_DOTENV_DISABLED') == '1'
            and os.getenv('PYTHONDONTWRITEBYTECODE') == '1'
            and 'AUDIT_FIXED_NOW_UTC' not in os.environ
            and not any(key.upper().startswith('PG') for key in os.environ),
            'isolated_clean_test_environment_required')
    require(os.getenv('STYLE_VISUAL_ENABLED','false') == 'false'
            and os.getenv('CLOSED_ALPHA_ENABLED','false') == 'false'
            and os.getenv('ADMITAD_ENABLED','false') == 'false', 'external_features_disabled_required')
    from sqlalchemy.engine import make_url
    require(bool(os.getenv('DATABASE_URL')), 'isolated_database_url_required')
    url = make_url(os.environ['DATABASE_URL'])
    require(url.drivername in {'postgresql','postgresql+psycopg'}
            and url.host == HOST and url.port in (None,5432)
            and url.database == 'modish_refresh16_' + run_id
            and url.username == 'refresh16_' + run_id and not url.query,
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
    parser.add_argument('--archive-sha256',required=True)
    parser.add_argument('--isolated-write-test',required=True,action='store_true')
    args = parser.parse_args()
    require(__debug__, 'python_optimization_forbidden')
    REPORT.update(run_id=args.run_id, archive_sha256=args.archive_sha256)
    url = environment_guard(args.run_id,args.archive_sha256)
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
        from app.services.activation_policy import EXCLUDED_SOURCES,product_ok
        from app.services.activation_service import _required_roles_available,candidate_pool
        from app.services.outfit_quality import is_sports_item,outfit_compatible,scenario_product_ok,garment_text
        from app.services.complement_policy import COMPLEMENT_ROLES,complements_anchor
        from app.services.outfit_engine_v2 import _hard_ok,_colors,NEUTRAL_COLORS
        from app.services.recommendation_context import context_for
        from app.schemas.shopping_intent import IntentSpec
        from app.services.intent_eligibility import intent_facts,intent_product_problem
        from app.money import as_rubles
        require(engine.url == url, 'actual_engine_url_mismatch')

        def transaction_bounds(connection):
            connection.exec_driver_sql("SET LOCAL statement_timeout='45s'")
            connection.exec_driver_sql("SET LOCAL lock_timeout='3s'")

        event.listen(engine,'begin',transaction_bounds)
        stack.callback(event.remove,engine,'begin',transaction_bounds)
        stack.callback(engine.dispose)
        REPORT['phase'] = 'database_identity_and_physical_schema'
        with SessionLocal() as db:
            require(tuple(db.execute(text('SELECT current_database(),current_user,inet_server_port()')).one())
                    == (url.database,url.username,5432), 'actual_database_identity_mismatch')
            require(db.scalar(text('SELECT version_num FROM alembic_version')) == '20261015_0028',
                    'live_clone_schema_0028_required')
            physical_tables = list(db.scalars(text(
                "SELECT tablename FROM pg_tables WHERE schemaname='public' ORDER BY tablename")))
            require(len(physical_tables) == 31
                    and set(physical_tables) == set(Base.metadata.tables) | {'alembic_version'}
                    and all(re.fullmatch(r'[a-z][a-z0-9_]{0,62}',name) for name in physical_tables),
                    'exact_31_public_schema_tables_required')
            columns = list(db.execute(text(
                'SELECT table_name,column_name,data_type,numeric_precision,numeric_scale,is_nullable,column_default '
                "FROM information_schema.columns WHERE table_schema='public' AND "
                "((table_name='products' AND column_name IN ('price','old_price')) "
                "OR (table_name='outfits' AND column_name='total_price') "
                "OR (table_name='fit_profiles' AND column_name='gender_target') "
                "OR (table_name='users' AND column_name='recommendation_revision'))")))
            require(len(columns) == 5, 'physical_0028_migration_columns_required')
            for table,column,datatype,precision,scale,nullable,default in columns:
                if column in {'price','old_price','total_price'}:
                    require((datatype,precision,scale) == ('numeric',14,2), 'physical_numeric_14_2_required')
                elif column == 'gender_target':
                    require(nullable == 'YES' and default is None, 'physical_nullable_gender_without_default_required')
                else:
                    require(datatype == 'integer' and nullable == 'NO' and default in {'0','0::integer'},
                            'physical_recommendation_revision_default_required')
            actual_owner_tables = set(db.scalars(text(
                "SELECT table_name FROM information_schema.columns WHERE table_schema='public' AND column_name='user_id'")))
            require(actual_owner_tables == {table.name for table in Base.metadata.sorted_tables if 'user_id' in table.c},
                    'physical_synthetic_owner_cleanup_schema_mismatch')
            before = {name:db.scalar(text('SELECT count(*) FROM public."'+name+'"')) for name in physical_tables}
            REPORT['cleanup_table_count'] = len(before)
            REPORT['physical_schema_0028_verified'] = True

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
                    owner = User(id=str(uuid4()),email='qa16-'+uuid4().hex+'@example.invalid',password_hash='unused')
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
                db.add(User(id=owner,email='qa16-http-'+uuid4().hex+'@example.invalid',password_hash='unused'))
                fixture_started = True
                db.commit()
            headers = {'Authorization':'Bearer ' + create_access_token(owner)}
            with TestClient(app,raise_server_exceptions=False) as client:
                def request(method,path,expected=200,**kwargs):
                    stages = {'/health/ready':'readiness','/catalog/status':'catalogue_status',
                        '/products/brands':'brands','/feed':'feed','/onboarding/preferences':'preferences',
                        '/fit-profile/me':'fit_patch','/catalog/discovery':'discovery',
                        '/onboarding/style-swipe':'style_swipe','/onboarding/complete':'completion',
                        '/outfits/generate':'outfit_generation','/outfits':'outfit_list',
                        '/shopping-intents':'blue_pumps_intent'}
                    stage = ('complements' if path.endswith('/complements') else
                             stages.get(path,'discovery_detail'))
                    REPORT['http_stage'] = stage
                    response = client.request(method,path,headers=headers,**kwargs)
                    REPORT['last_http_status'] = response.status_code
                    require(response.status_code == expected, 'http_'+stage+'_unexpected_status')
                    return response.json()

                REPORT['phase'] = 'reported_profile_http'
                health = request('GET','/health/ready')
                require(health.get('version') == API_VERSION and health.get('database') == 'ok',
                        'build16_http_readiness_required')
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
                        activation = db.get(ActivationSession,owner)
                        rules = load_rules_by_source_id(db)
                        for row in rows:
                            # Feed deliveries wrap their product; onboarding cards are flat.
                            product_payload = row.get('product',row)
                            require(isinstance(product_payload,dict) and 'id' in product_payload,
                                    'personal_api_product_payload_missing')
                            product = db.get(Product,product_payload['id'])
                            require(product is not None and evaluate_eligibility(product,fit).eligible
                                    and evaluate_personal_size(product,fit).eligible
                                    and product_is_feed_eligible(product)
                                    and availability_snapshot(product).available
                                    and product_ok(product,fit,activation,rules=rules)
                                    and product.gender_target == 'womenswear'
                                    and str(product.currency).upper() in {'RUB','RUR'}
                                    and 300 <= as_rubles(product.price) <= min(25000,as_rubles(fit.budget_max))
                                    and str(product.source).lower() not in EXCLUDED_SOURCES
                                    and product_passes_source_rules(product,rules) and not is_sports_item(product),
                                    'personal_feed_or_swipe_invalid_real_sku')
                            if 'price' in product_payload:
                                require(as_rubles(product_payload['price']) == as_rubles(product.price),
                                        'personal_api_price_mismatch')
                            if 'title' in product_payload:
                                require(product_payload['title'] == unescape(product.title or ''),
                                        'personal_api_title_not_actual_catalogue_title')
                            if 'outfit_slot' in product_payload:
                                require(product_payload['outfit_slot'] == product_role(product),
                                        'personal_api_role_not_actual_garment_role')
                            keys = product_identity_keys(product)
                            require(product.id not in seen_ids and not seen_keys.intersection(keys),
                                    'personal_response_identity_repeat')
                            seen_ids.add(product.id);seen_keys.update(keys)
                    return seen_ids,seen_keys

                def strict_outfits(outfits, *, label, exact_shoes_available):
                    require(isinstance(outfits,list) and len(outfits) <= 3,
                            label+'_outfit_batch_contract')
                    counts = {'complete':0,'clothing_base':0,'items':0}
                    for outfit in outfits:
                        require(isinstance(outfit,dict) and isinstance(outfit.get('items'),dict)
                                and isinstance(outfit.get('products'),dict), label+'_outfit_payload_missing')
                        slots = set(outfit['items'])
                        is_base = slots in ({'top','bottom'},{'one_piece'})
                        is_complete = slots in ({'top','bottom','shoes'},{'one_piece','shoes'})
                        require(is_base or is_complete, label+'_invalid_outfit_slots')
                        if is_base:
                            require(outfit.get('completeness') == 'clothing_base'
                                    and outfit.get('missing_slots') == ['shoes']
                                    and outfit.get('completion_message') == BASE_MESSAGE,
                                    label+'_clothing_base_not_honestly_labelled')
                            counts['clothing_base'] += 1
                        else:
                            require(exact_shoes_available and outfit.get('completeness') == 'complete'
                                    and outfit.get('missing_slots') == []
                                    and outfit.get('completion_message') is None,
                                    label+'_complete_outfit_without_verified_footwear')
                            counts['complete'] += 1
                        require(set(outfit['products']) == slots, label+'_outfit_product_slots_mismatch')
                        payloads = []
                        with SessionLocal() as db:
                            fit = db.scalar(select(FitProfile).where(FitProfile.user_id == owner))
                            products = []
                            for slot,pid in outfit['items'].items():
                                payload = outfit['products'][slot]
                                require(isinstance(payload,dict) and payload.get('id') == pid,
                                        label+'_outfit_product_id_mismatch')
                                product = db.get(Product,pid)
                                require(product is not None and product_role(product) == slot,
                                        label+'_outfit_garment_slot_mismatch')
                                require(scenario_product_ok(product,outfit.get('style_direction') or 'daily'),
                                        label+'_outfit_scenario_violation')
                                products.append(product);payloads.append(payload)
                            total = sum((as_rubles(p.price) for p in products),as_rubles(0))
                            require(outfit_compatible(products), label+'_outfit_incompatible_garments')
                            require(0 < total <= as_rubles(fit.budget_max)
                                    and as_rubles(outfit.get('total_price')) == total,
                                    label+'_outfit_total_price_or_budget_violation')
                        strict_rows(payloads)
                        counts['items'] += len(payloads)
                    return counts

                def clothing_composition_observed(products,maximum):
                    # A positive expectation includes the actual composition
                    # restrictions; a top and bottom merely existing is not
                    # enough to promise a wearable personal clothing base.
                    colors = set().union(*(_colors(p) for p in products))
                    texts = [garment_text(p) for p in products]
                    warm = any(any(word in text for word in
                        ('утеплен','мех','теплоизоля','шерстяной подклад')) for text in texts)
                    light = any(any(word in text for word in
                        ('льнян','шифон','сандал','босонож','linen','chiffon')) for text in texts)
                    identities = [product_identity_keys(p) for p in products]
                    return (all(context_for('daily').allows(p) for p in products)
                        and outfit_compatible(products)
                        and len(colors - (NEUTRAL_COLORS | {'cream','brown'})) <= 1
                        and not (warm and light)
                        and all(not keys.intersection(other) for i,keys in enumerate(identities)
                                for other in identities[i+1:])
                        and 0 < sum((as_rubles(p.price) for p in products),as_rubles(0)) <= as_rubles(maximum))

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
                REPORT['phase'] = 'real_personal_candidate_probe'
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
                    # This is an observed, bounded real pool, not a claim that
                    # every merchant or every personal size is covered.
                    pool = [p for p in candidate_pool(db,db.get(User,owner),activation,
                                outfit_scenario='daily',context=context_for('daily'))
                            if _hard_ok(p,fit=fit,rules_by_source=rules)]
                    by_role = {role:[p for p in pool if product_role(p) == role]
                               for role in ('top','bottom','one_piece','shoes')}
                    compatible_clothing = any(clothing_composition_observed([p],fit.budget_max)
                        for p in by_role['one_piece']) or any(
                        clothing_composition_observed([top,bottom],fit.budget_max)
                        for top in by_role['top'] for bottom in by_role['bottom'])
                    REPORT['personal_candidate_probe'] = {
                        'scope':'bounded_strict_size_daily_candidate_pool',
                        'real_pool_rows':len(pool),
                        'roles':{role:len(rows) for role,rows in by_role.items()},
                        'compatible_clothing_base_observed':compatible_clothing,
                        'exact_shoe_candidate_available':shoes,
                        'unavailable_roles':[role for role,rows in by_role.items() if not rows],
                        'absence_is_not_full_catalogue_proof':True}
                REPORT['phase'] = 'warm_completion_outfits'
                wow = request('POST','/onboarding/complete')
                require(wow.get('completed') is True and isinstance(wow.get('outfits'),list), 'warm_completion_failed')
                wow_counts = strict_outfits(wow['outfits'],label='wow',exact_shoes_available=shoes)
                require(not compatible_clothing or bool(wow['outfits']),
                        'compatible_real_clothing_pool_but_empty_wow')
                if not wow['outfits']:
                    require(bool(wow.get('message')), 'empty_wow_missing_honest_explanation')
                REPORT['outfit_checks'] = {'wow':wow_counts}
                REPORT['phase'] = 'warm_generated_outfits'
                generated = request('POST','/outfits/generate',params={'count':3,'scenario':'daily'})
                generated_counts = strict_outfits(generated,label='generated',exact_shoes_available=shoes)
                # Rotation may exhaust alternatives after WOW. The generated
                # batch may be empty, while valid existing looks stay visible.
                listed = request('GET','/outfits')
                listed_counts = strict_outfits(listed,label='listed',exact_shoes_available=shoes)
                require(not compatible_clothing or bool(listed),
                        'compatible_real_clothing_pool_but_empty_outfit_list')
                REPORT['outfit_checks'].update(generated=generated_counts,listed=listed_counts)
                REPORT['outfit_checks']['generated_empty_existing_retained'] = not bool(generated) and bool(listed)
                REPORT['phase'] = 'blue_pumps_shopping_intent'
                intent_request_id = str(uuid4())
                intent = request('POST','/shopping-intents',json={
                    'text':'синие туфли','request_id':intent_request_id})
                constraints = intent.get('constraints') or {}
                require(intent.get('text') == 'синие туфли' and intent.get('state') == 'ready'
                        and constraints.get('intent_type') == 'single_item'
                        and constraints.get('requested_categories') == ['обувь']
                        and constraints.get('required_features',{}).get('footwear_type') == ['pumps']
                        and constraints.get('colors') == ['blue']
                        and constraints.get('unresolved') == [],
                        'blue_pumps_noun_or_color_unrecognized')
                require(isinstance(intent.get('products'),list) and intent.get('outfits') == [],
                        'blue_pumps_single_item_contract')
                strict_rows(intent['products'])
                with SessionLocal() as db:
                    from app.models import ShoppingIntent
                    stored = db.get(ShoppingIntent,intent.get('id'))
                    require(stored is not None and stored.user_id == owner
                            and stored.request_id == intent_request_id,
                            'blue_pumps_intent_not_owned_or_persisted')
                    fit = db.scalar(select(FitProfile).where(FitProfile.user_id == owner))
                    spec = IntentSpec.model_validate(constraints)
                    for row in intent['products']:
                        product = db.get(Product,row['product']['id'])
                        facts = intent_facts(product)
                        require(product_role(product) == 'shoes'
                                and facts.get('footwear_type') == {'pumps'}
                                and 'blue' in facts.get('color',set())
                                and intent_product_problem(product,fit,spec,facts=facts) is None,
                                'blue_pumps_results_relaxed_requested_stock_facts')
                if not intent['products']:
                    require(bool(intent.get('message')), 'empty_blue_pumps_missing_honest_explanation')
                REPORT['shopping_intent'] = {'request_text':'синие туфли','request_id_is_uuid':True,
                    'recognized_footwear_type':'pumps','recognized_color':'blue',
                    'unsupported_conditions':[],'state':intent['state'],
                    'real_products':len(intent['products']),
                    'exact_match_unavailable':not bool(intent['products']),
                    'empty_explained':bool(intent.get('message')) if not intent['products'] else None}
                REPORT['phase'] = 'real_anchor_complements'
                REPORT['complements'] = []
                for anchor_kind,role,pattern in (
                    ('skirt','bottom',r'(?<!\w)(?:юбк\w*|skirts?)(?!\w)'),
                    ('top','top',r'(?<!\w)(?:рубашк\w*|сорочк\w*|поло|футболк\w*|майк\w*|блуз\w*|'
                     r'джемпер\w*|свитер\w*|свитшот\w*|толстовк\w*|худи|водолазк\w*|лонгслив\w*|'
                     r'пуловер\w*|топ\w*|shirts?|t[ -]shirts?|tops?|sweaters?|hoodies?|tanks?|blouses?)(?!\w)')):
                    anchors = [p for p in pool if product_role(p) == role and re.search(pattern,
                        re.split(r'\s+(?:с|со|к|под|для|with|for)\s+',
                            str(p.title or '').lower().replace('ё','е'),maxsplit=1)[0])]
                    if not anchors:
                        REPORT['complements'].append({'anchor_kind':anchor_kind,'status':'unavailable',
                            'reason':'no_strict_known_anchor_in_observed_real_pool','real_results':0})
                        continue
                    anchor = sorted(anchors,key=lambda p:p.id)[0]
                    strict_rows([{'id':anchor.id}])
                    related = request('GET','/products/'+anchor.id+'/complements',params={'limit':12})
                    require(isinstance(related,list) and len(related) <= 12, 'complements_response_contract')
                    strict_rows(related)
                    observed_compatible = [p for p in pool
                        if p.id != anchor.id and not product_identity_keys(p).intersection(product_identity_keys(anchor))
                        and complements_anchor(anchor,p)
                        and as_rubles(p.price)+as_rubles(anchor.price) <= as_rubles(100000)]
                    roles = set()
                    with SessionLocal() as db:
                        fit = db.scalar(select(FitProfile).where(FitProfile.user_id == owner))
                        for row in related:
                            product = db.get(Product,row['id'])
                            actual_role = product_role(product)
                            require(product.id != anchor.id and actual_role != role
                                    and actual_role in COMPLEMENT_ROLES[role]
                                    and not product_identity_keys(product).intersection(product_identity_keys(anchor))
                                    and complements_anchor(anchor,product)
                                    and as_rubles(product.price)+as_rubles(anchor.price) <= as_rubles(fit.budget_max),
                                    'complements_repeated_anchor_role_or_invalid_pair')
                            require(row.get('title') == unescape(product.title or ''),
                                    'complements_title_not_actual_catalogue_title')
                            roles.add(actual_role)
                    REPORT['complements'].append({'anchor_kind':anchor_kind,'status':'checked',
                        'anchor_id':anchor.id,'anchor_title':unescape(anchor.title or ''),'anchor_role':role,
                        'real_results':len(related),'result_roles':sorted(roles),
                        'compatible_options_observed_in_pool':len(observed_compatible),
                        'exact_options_unavailable':not bool(related),'anchor_role_repeats':0})
                REPORT['phase'] = 'warm_first_feed'
                feed = request('GET','/feed',params={'limit':10})
                require(isinstance(feed,list) and bool(feed), 'reported_warm_first_feed_empty')
                ids,keys = strict_rows(feed)
                second = request('GET','/feed',params=[('limit','10'),*(('exclude_ids',pid) for pid in sorted(ids))])
                next_ids,next_keys = strict_rows(second)
                require(not ids.intersection(next_ids) and not keys.intersection(next_keys),
                        'warm_feed_repeats_excluded_identity')
                REPORT['http'] = {'api_version':API_VERSION,'catalogue_status':'ready','brands_returned':len(brands),
                    'cold_feed_status':409,'authentication_status':401,'bad_input_status':422,
                    'reported_profile':{'height':166,'weight':53,'top':'L','bottom':'L','one_piece':'L',
                                        'shoes':'38','shoe_system':'EU','budget':100000},
                    'real_swipes':15,'taste_updated':True,'warm_completed':True,'first_feed_rows':len(feed),
                    'second_feed_rows':len(second),'cross_page_identity_repeats':0,
                    'exact_shoe_candidate_available':shoes,'wow_outfits':len(wow['outfits']),
                    'wow_empty_explained':bool(wow.get('message')) if not wow['outfits'] else None}
        finally:
            if fixture_started:
                REPORT['cleanup_stage'] = 'delete_only_synthetic_owner_dependents'
                with engine.begin() as connection:
                    for table in reversed(Base.metadata.sorted_tables):
                        if 'user_id' in table.c:
                            connection.execute(delete(table).where(table.c.user_id == owner))
                    connection.execute(delete(User).where(User.id == owner))
                with SessionLocal() as db:
                    after = {name:db.scalar(text('SELECT count(*) FROM public."'+name+'"')) for name in physical_tables}
                    require(after == before, 'all_table_counts_not_restored_after_synthetic_fixture')
                    require(db.get(User,owner) is None, 'synthetic_http_owner_not_removed')
                    for table in Base.metadata.sorted_tables:
                        if 'user_id' in table.c:
                            require(db.scalar(select(func.count()).select_from(table).where(table.c.user_id == owner)) == 0,
                                    'synthetic_owner_dependent_rows_left_behind_'+table.name)
                REPORT['fixture_cleanup_completed'] = True
                REPORT['cleanup_stage'] = 'all_owner_dependents_removed_and_all_table_counts_restored'
        require(REPORT['external_network_attempts'] == 0, 'unexpected_external_network_attempts_observed')
    REPORT.update(status='passed',run_id=args.run_id,archive_sha256=args.archive_sha256,
                  phase='completed',
                  checked_at_utc=datetime.now(timezone.utc).isoformat(),seconds=round(perf_counter()-started,3))
    print(json.dumps(REPORT))


if __name__ == '__main__':
    try:
        main()
    except Exception as error:
        REPORT.update(status='failed',failed_phase=REPORT.get('phase'),
                      error_type=type(error).__name__,raw_error_omitted=True,
                      check=str(error) if isinstance(error,CheckError) else 'unexpected_phase_exception')
        print(json.dumps(REPORT))
        raise SystemExit(1) from None
