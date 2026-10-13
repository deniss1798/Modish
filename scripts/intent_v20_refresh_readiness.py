"""Current-clock readiness on the real isolated catalogue; no command arguments.

Ten controlled profiles use actual 15 Style Swipes (10 likes, 5 dislikes).
Only complete_wow may complete activation. Every test user's transaction is
rolled back; catalogue objects may never be flushed or committed. Output is
aggregate counts and hashes, with no product/source URLs or individual IDs.
"""
from __future__ import annotations

from collections import Counter
from copy import deepcopy
from datetime import datetime, timezone
import hashlib
import json
import os
from pathlib import Path
import re
import sys
from time import perf_counter
from uuid import uuid4


SOURCES = ("baon", "shoppinglive", "serginnetti", "postmeridiem")
PROFILES = tuple(
    [("RU", size, str(36 + index), "mid")
     for index, size in enumerate(("42", "44", "46", "48", "50"))]
    + [("LETTER", size, str(36 + index), "mass")
       for index, size in enumerate(("XS", "S", "M", "L", "XL"))]
)
TASKS = (
    ("trousers", "Брюки до 7000", "single_item", None),
    ("dress", "Платье до 8000", "single_item", None),
    ("office", "Образ в офис до 20000", "outfit", "office"),
    ("wedding_no_dress_no_heel", "Образ на свадьбу до 20000 без платья и каблуков",
     "outfit", "wedding"),
)
PROTECTED_TABLES = ("products", "catalog_records", "product_sources", "catalog_sync_runs")
FROZEN_CLOCK_ENV = frozenset({
    "AUDIT_FIXED_NOW_UTC", "AUDIT_CLOCK_BASIS", "AUDIT_REPLAY_AS_OF_UTC",
    "AUDIT_AS_OF_UTC", "AUDIT_HISTORICAL_REPLAY", "FAKETIME", "LD_PRELOAD",
})
PG_CONNECTION_ENV = frozenset({
    "PGHOST", "PGHOSTADDR", "PGPORT", "PGDATABASE", "PGUSER", "PGPASSWORD",
    "PGSERVICE", "PGSERVICEFILE", "PGPASSFILE", "PGOPTIONS", "PGSSLMODE",
})


class ReadinessFailure(RuntimeError):
    """A code-owned failure label, safe to emit without raw exception text."""

    def __init__(self, reason):
        super().__init__(reason)
        self.reason = reason


def require(condition, reason):
    if not condition:
        raise ReadinessFailure(reason)


def values_hash(values):
    return hashlib.sha256(json.dumps(values, sort_keys=True, ensure_ascii=True,
                                     separators=(",", ":"), default=str).encode()).hexdigest()


def size_evidence_bucket():
    return {"products": Counter(), "size_failure_reasons_by_token": Counter(),
            "availability_by_size_token": Counter()}


def note_size_evidence(bucket, evidence):
    bucket["products"]["total"] += 1
    bucket["products"]["no_resolved_size_tokens"] += not bool(evidence)
    bucket["products"]["has_known_size"] += any(size.known for size in evidence)
    bucket["products"]["has_known_available_size"] += any(
        size.known and size.availability == "available" for size in evidence)
    bucket["size_failure_reasons_by_token"].update(size.reason or "known" for size in evidence)
    bucket["availability_by_size_token"].update(size.availability for size in evidence)


def serialize_size_evidence(buckets):
    return {key: {field: dict(counts) for field, counts in bucket.items()}
            for key, bucket in sorted(buckets.items())}


def requested_size_state(evidence, requested, system):
    """Size identity and per-size stock remain separate from whole-item stock."""
    if requested is None:
        return "missing_confirmed_profile_size"
    if not evidence:
        return "no_resolved_size_tokens"
    exact = [size for size in evidence if size.known and size.normalized_size == requested
             and size.size_system == system]
    if exact:
        states = {size.availability for size in exact}
        return ("exact_known_size_available" if "available" in states else
                "exact_known_size_unavailable" if states == {"unavailable"} else
                "exact_known_size_availability_unknown")
    if not any(size.known for size in evidence):
        return "size_system_or_token_unconfirmed"
    if any(size.known and size.normalized_size == requested for size in evidence):
        return "requested_size_system_mismatch"
    return "requested_size_not_present"


def validate_database_url(value):
    from sqlalchemy.engine import make_url
    url = make_url(value)
    require(url.get_backend_name() == "postgresql", "isolated_postgresql_required")
    require(url.host == "modish_foundation_test_pg", "isolated_host_required")
    require(url.database == url.username == "modish_test", "isolated_database_user_required")
    require(url.port in (None, 5432), "isolated_port_required")
    require(not url.query, "database_query_overrides_forbidden")
    return url


def validate_environment():
    require(len(sys.argv) == 1, "arguments_forbidden")
    require(os.environ.get("PYTHON_DOTENV_DISABLED") == "1", "dotenv_disabled_required")
    require(os.environ.get("APP_ENV") == "test", "test_environment_required")
    require(not FROZEN_CLOCK_ENV.intersection(os.environ), "frozen_or_historical_clock_forbidden")
    require(not any(key.startswith("AUDIT_") and any(token in key for token in
                    ("FIXED", "FROZEN", "HISTORICAL", "REPLAY", "AS_OF")) for key in os.environ),
            "frozen_or_historical_clock_forbidden")
    require(not PG_CONNECTION_ENV.intersection(os.environ), "postgres_environment_overrides_forbidden")
    validate_database_url(os.environ["DATABASE_URL"])
    require(bool(re.fullmatch(r"sha256:[0-9a-f]{64}", os.environ["AUDIT_IMAGE_ID"])),
            "immutable_image_id_required")
    require(bool(re.fullmatch(r"[0-9a-f]{64}", os.environ["AUDIT_ARCHIVE_SHA256"])),
            "archive_sha256_required")


def begin_transaction(db, *, read_only=False):
    from sqlalchemy import text
    db.execute(text("SET TRANSACTION ISOLATION LEVEL REPEATABLE READ" +
                    (", READ ONLY" if read_only else "")))
    db.execute(text("SET LOCAL statement_timeout = '120s'"))
    db.execute(text("SET LOCAL lock_timeout = '1s'"))
    db.execute(text("SET LOCAL TIME ZONE 'UTC'"))
    identity = db.execute(text("SELECT current_database(), current_user, inet_server_port()")).one()
    require(tuple(identity) == ("modish_test", "modish_test", 5432),
            "actual_database_identity_mismatch")


def catalogue_snapshot(db):
    """Canonical JSONB hashes cover every protected row, including stock times.

    Fixed table names are code constants. Raw JSON, credentials and URLs remain
    inside PostgreSQL; only the count and aggregate digest leave the server.
    """
    from sqlalchemy import text
    result = {}
    for table in PROTECTED_TABLES:
        count, digest = db.execute(text(
            "SELECT count(*), md5(coalesce(string_agg(md5(to_jsonb(t)::text), "
            "'|' ORDER BY t.id), '')) FROM " + table + " AS t"
        )).one()
        result[table] = {"rows": count, "state_sha256": values_hash(digest)}
    return result


def read_catalogue_summary():
    from sqlalchemy import func, select
    from app.db import SessionLocal
    from app.models import Product
    from app.services import activation_service as activation
    from app.services.catalog.size_resolver import resolve_product_sizes
    from app.services.catalog.stock_freshness import available_product_conditions
    checked = datetime.now(timezone.utc)
    source_sizes, category_sizes, sources = {}, {}, {}
    with SessionLocal() as db:
        try:
            begin_transaction(db, read_only=True)
            snapshot = catalogue_snapshot(db)
            for source, count, active, oldest, newest in db.execute(select(
                Product.source, func.count(), func.sum(Product.is_active),
                func.min(Product.last_seen_in_feed_at), func.max(Product.last_seen_in_feed_at),
            ).group_by(Product.source).order_by(Product.source)):
                key = source if source in SOURCES else "other_sources"
                bucket = sources.setdefault(key, {"stored": 0, "active": 0,
                    "oldest_observation": None, "newest_observation": None})
                bucket["stored"] += count
                bucket["active"] += int(active or 0)
                for field, value, choose in (("oldest_observation", oldest, min),
                                             ("newest_observation", newest, max)):
                    if value:
                        stamp = value.isoformat()
                        bucket[field] = choose(bucket[field], stamp) if bucket[field] else stamp
            fresh = 0
            query = select(Product).where(*available_product_conditions(checked_at=checked))
            for product in db.scalars(query.execution_options(yield_per=200)):
                fresh += 1
                evidence = resolve_product_sizes(product)
                source = product.source if product.source in SOURCES else "other_sources"
                category = product.category if product.category in activation.CATEGORIES else "other_categories"
                note_size_evidence(source_sizes.setdefault(source, size_evidence_bucket()), evidence)
                note_size_evidence(category_sizes.setdefault(category, size_evidence_bucket()), evidence)
            return snapshot, {"checked_at": checked.isoformat(), "sources": sources,
                "fresh_available_skus": fresh,
                "size_evidence_scope": "fresh_available_products_resolved_size_tokens",
                "source_size_evidence": serialize_size_evidence(source_sizes),
                "category_size_evidence": serialize_size_evidence(category_sizes)}
        finally:
            db.rollback()


def no_commit():
    raise ReadinessFailure("readiness_transactions_must_not_commit")


def protected_flush_guard(protected_models):
    def guard(session, _context, _instances):
        require(not any(isinstance(obj, protected_models) for collection in
                        (session.new, session.dirty, session.deleted) for obj in collection),
                "readiness_must_not_change_catalogue")
    return guard


def taste_fit_snapshot(db, user_id, models):
    from sqlalchemy import select
    result = {}
    for model in models:
        rows = db.scalars(select(model).where(model.user_id == user_id).order_by(model.id)).all()
        result[model.__tablename__] = [
            {column.key: deepcopy(getattr(row, column.key)) for column in model.__table__.columns}
            for row in rows]
    return values_hash(result)


def run_profile(index, configuration, initial_catalogue):
    from sqlalchemy import event, func, select
    from app.db import SessionLocal
    from app.models import (ActivationSession, CatalogRecord, CatalogSyncRun, FitProfile,
                            Outfit, Product, ProductSource, StyleProfile, TasteProfile,
                            User, UserTasteFeature)
    from app.schemas.shopping_intent import IntentSpec
    from app.services import activation_service as activation
    from app.services import shopping_intent_service as intents
    from app.services.catalog.rule_filters import load_rules_by_source_id
    from app.services.catalog.size_resolver import resolve_product_sizes
    from app.services.catalog.stock_freshness import availability_snapshot
    from app.services.catalog_filters import CatalogFilters
    from app.services.fit_size_profile import missing_fit_requirements, requested_size_for_product
    from app.services.garment_roles import product_role
    from app.services.intent_eligibility import intent_product_problem
    from app.services.intent_outfits import outfit_problem
    from app.services.outfit_engine_v2 import SCENARIO_SLOT_OPTIONS, _scenario_key
    from app.services.outfit_quality import outfit_compatible, scenario_product_ok
    from app.services.product_eligibility import evaluate_eligibility

    system, size, shoe, segment = configuration
    started = perf_counter()
    uid = str(uuid4())
    row = {"index": index, "clothing_size": size, "clothing_system": system,
           "shoe_size": shoe, "shoe_system": "EU", "segment": segment,
           "activation_completed": False, "style_swipe_rated": 0,
           "wow_outfits": 0, "feed_cards": 0, "filters": {}, "tasks": [],
           "invalid_returned": 0, "test_writes_rolled_back": False}
    with SessionLocal() as db:
        begin_transaction(db)
        product_count = db.scalar(select(func.count()).select_from(Product))
        require(product_count == initial_catalogue["products"]["rows"], "catalogue_changed_between_profiles")
        protected = (Product, CatalogRecord, ProductSource, CatalogSyncRun)
        guard = protected_flush_guard(protected)
        db.commit = no_commit
        event.listen(db, "before_flush", guard)
        try:
            user = User(id=uid, email="refresh-readiness-" + uuid4().hex + "@example.test",
                        password_hash="disabled-isolated-readiness-account")
            db.add(user)
            db.flush()
            session = activation.configure(db, user, age_group="25-34", height=168,
                clothing_size=size, clothing_size_system=system, shoe_size=shoe,
                shoe_size_system="EU", styles=["casual", "minimalism"], price_segment=segment)
            fit = db.scalar(select(FitProfile).where(FitProfile.user_id == uid))
            require(fit is not None and not missing_fit_requirements(fit), "configured_fit_incomplete")
            require(session.completed_at is None, "new_activation_already_completed")
            row["budget"] = fit.budget_max
            rules = load_rules_by_source_id(db)
            diagnostics = {}
            diagnostic_time = datetime.now(timezone.utc)
            diagnostic_query = select(Product).where(Product.source.in_(SOURCES), Product.is_active == 1)
            for product in db.scalars(diagnostic_query.execution_options(yield_per=200)):
                role = product_role(product)
                role = role if role in {"top", "bottom", "one_piece", "shoes", "outerwear"} else "other"
                bucket = diagnostics.setdefault(role, {"requested_size_states": Counter(),
                    "size_failure_reasons_by_token": Counter(), "product_stock_states": Counter()})
                evidence = resolve_product_sizes(product)
                requested, requested_system = requested_size_for_product(fit, product)
                bucket["requested_size_states"][requested_size_state(evidence, requested, requested_system)] += 1
                bucket["size_failure_reasons_by_token"].update(size.reason or "known" for size in evidence)
                availability = availability_snapshot(product, checked_at=diagnostic_time)
                bucket["product_stock_states"][availability.reason or "available"] += 1
            row["size_diagnostics"] = {"scope": "four_sources_stored_active_products_by_role",
                "checked_at": diagnostic_time.isoformat(), "roles": {
                    role: {field: dict(counts) for field, counts in bucket.items()}
                    for role, bucket in sorted(diagnostics.items())}}

            def check_product(product, *, filters=None, role=None):
                require(product is not None, "returned_product_missing")
                require(evaluate_eligibility(product, fit, filters=filters).eligible,
                        "ineligible_returned_product")
                require(activation.product_ok(product, fit, session, rules=rules),
                        "activation_ineligible_returned_product")
                if role is not None:
                    require(product_role(product) == role, "returned_filter_role_mismatch")

            deck = activation.swipe_deck(db, user, session)
            db.flush()
            deck_ids = [product["id"] for product in deck["products"]]
            require(len(deck_ids) == len(set(deck_ids)), "duplicate_bootstrap_cards")
            for pid in deck_ids:
                check_product(db.get(Product, pid))
            row.update(bootstrap_cards=len(deck_ids), bootstrap_ids_sha256=values_hash(deck_ids),
                       eligible_deck_cards_checked=len(deck_ids))
            require(activation.MIN_SWIPES == 15, "style_swipe_protocol_changed")
            if len(deck_ids) < 15:
                row["activation_status"] = "insufficient_real_catalogue_bootstrap"
                row["activation_reason"] = "fewer_than_15_eligible_bootstrap_cards"
            else:
                for position, pid in enumerate(deck_ids[:15]):
                    activation.swipe_feedback(db, user, session, pid,
                                              "like" if position < 10 else "dislike")
                    db.flush()
                reactions = Counter((session.swipes_json or {}).values())
                require(reactions == {"like": 10, "dislike": 5}, "style_swipe_reactions_mismatch")
                require(activation.rated_count(session) == 15 and activation.can_complete(session),
                        "real_style_swipes_cannot_complete")
                wow = activation.complete_wow(db, user, session)
                db.flush()
                require(wow["completed"] and session.completed_at is not None,
                        "real_activation_did_not_complete")
                activation.require_warm_feed(db, user)
                wow_ids = []
                for response in wow["outfits"]:
                    outfit = db.get(Outfit, response["id"])
                    require(outfit is not None and outfit.user_id == uid, "wow_outfit_owner_mismatch")
                    group = {slot: db.get(Product, pid) for slot, pid in response["items"].items()}
                    scenario = _scenario_key(outfit.style_direction)
                    require(any(set(group) == set(slots) for slots in SCENARIO_SLOT_OPTIONS[scenario]),
                            "incomplete_wow_outfit")
                    for slot, product in group.items():
                        check_product(product, role=slot)
                        require(scenario_product_ok(product, scenario), "wow_scenario_mismatch")
                    require(outfit_compatible(list(group.values())), "incompatible_wow_outfit")
                    require(sum(int(p.price or 0) for p in group.values()) <= fit.budget_max,
                            "wow_budget_exceeded")
                    wow_ids.append([group[slot].id for slot in sorted(group)])
                row.update(activation_completed=True, activation_status="completed_real_activation",
                    style_swipe_rated=activation.rated_count(session), reactions=dict(reactions),
                    wow_outfits=len(wow["outfits"]), wow_items_sha256=values_hash(wow_ids),
                    learned_taste_from_real_swipes=True)

            # Internal services support configured Fit even when onboarding cannot
            # finish. Such coverage is explicitly diagnostic, not accessible API.
            row["results_basis"] = ("completed_real_activation" if row["activation_completed"]
                                    else "configured_profile_service_diagnostic")
            row["shopping_intent_api_available"] = row["activation_completed"]
            feed = activation.personalized_feed(db, user, session, limit=20)
            for result in feed:
                check_product(result.product)
            row.update(feed_cards=len(feed), eligible_feed_cards_checked=len(feed),
                       feed_ids_sha256=values_hash([result.product.id for result in feed]))
            for key in ("shoes", "outerwear"):
                filters = CatalogFilters(categories=(key,))
                selected = activation.personalized_feed(db, user, session, limit=20, filters=filters)
                for result in selected:
                    check_product(result.product, filters=filters, role=key)
                row["filters"][key] = len(selected)
            db.flush()
            preferences_before = taste_fit_snapshot(db, uid,
                (FitProfile, StyleProfile, TasteProfile, UserTasteFeature))
            for key, query, expected_type, occasion in TASKS:
                task_started = perf_counter()
                intent = intents.create_intent(db, user, text=query, request_id=uuid4().hex)
                response = intents.intent_response(db, user, intent, remember=True)
                spec = IntentSpec.model_validate(response["constraints"])
                require(spec.intent_type == expected_type and spec.occasion == occasion,
                        "controlled_intent_parse_mismatch")
                if key == "wedding_no_dress_no_heel":
                    require("платья" in spec.excluded_categories and
                            "heels" in spec.excluded_features.get("heel", []),
                            "wedding_exclusions_unparsed")
                invalid = 0
                returned_ids = []
                for result in response["products"]:
                    product = db.get(Product, result["product"]["id"])
                    invalid += int(product is None or bool(intent_product_problem(product, fit, spec)))
                    returned_ids.append(result["product"]["id"])
                for result in response["outfits"]:
                    group = {slot: db.get(Product, pid) for slot, pid in result["items"].items()}
                    invalid += int(any(p is None for p in group.values()) or bool(outfit_problem(group, fit, spec)))
                    returned_ids.extend(result["items"].values())
                require(invalid == 0, "invalid_intent_result")
                row["tasks"].append({"query_kind": key, "intent_type": spec.intent_type,
                    "occasion": spec.occasion, "no_heel": bool(spec.excluded_features.get("heel")),
                    "no_dress": "платья" in spec.excluded_categories,
                    "supported": not spec.unresolved, "needs_clarification": bool(spec.unresolved),
                    "products": len(response["products"]), "outfits": len(response["outfits"]),
                    "empty": not (response["products"] or response["outfits"]),
                    "invalid_returned": invalid, "returned_ids_sha256": values_hash(returned_ids),
                    "seconds": round(perf_counter() - task_started, 3)})
            db.flush()
            require(preferences_before == taste_fit_snapshot(db, uid,
                (FitProfile, StyleProfile, TasteProfile, UserTasteFeature)), "intent_changed_permanent_preferences")
            row["intent_permanent_taste_and_fit_unchanged"] = True
        finally:
            db.rollback()
            event.remove(db, "before_flush", guard)
        # Start a fresh snapshot after rollback, so flushed synthetic users,
        # sessions and tasks cannot survive through the former identity map.
        begin_transaction(db, read_only=True)
        try:
            require(db.get(User, uid) is None and db.get(ActivationSession, uid) is None,
                    "synthetic_user_survived_rollback")
            require(db.scalar(select(func.count()).select_from(Product)) == product_count,
                    "catalogue_count_changed_after_rollback")
        finally:
            db.rollback()
    row["test_writes_rolled_back"] = True
    row["stock_timestamps_modified"] = False
    row["seconds"] = round(perf_counter() - started, 3)
    return row


def readiness_gates(profiles):
    require(len(profiles) == 10, "ten_profiles_required")
    def has_task(profile, key):
        return any(task["query_kind"] == key and task["supported"] and task["outfits"] > 0
                   for task in profile["tasks"])
    return {
        "all_profiles_activation_completed": all(p["activation_completed"] for p in profiles),
        "all_profiles_wow_at_least_3": all(p["wow_outfits"] >= 3 for p in profiles),
        "all_profiles_feed_at_least_10": all(p["feed_cards"] >= 10 for p in profiles),
        "all_profiles_shoes_and_outerwear": all(all(p["filters"].get(key, 0) > 0
            for key in ("shoes", "outerwear")) for p in profiles),
        "all_profiles_have_office_outfit": all(has_task(p, "office") for p in profiles),
        "all_profiles_wedding_no_heel": all(has_task(p, "wedding_no_dress_no_heel") for p in profiles),
        "all_intent_queries_supported": all(t["supported"] for p in profiles for t in p["tasks"]),
        "no_invalid_returned": all(p["invalid_returned"] == 0 and
            all(t["invalid_returned"] == 0 for t in p["tasks"]) for p in profiles),
        "all_test_writes_rolled_back": all(p["test_writes_rolled_back"] for p in profiles),
    }


def main():
    validate_environment()
    sys.path.insert(0, "/app")
    sys.path.insert(0, str(Path(__file__).resolve().parent.parent / "backend"))
    from app.db import engine
    validate_database_url(engine.url)
    started = datetime.now(timezone.utc)
    snapshot, summary = read_catalogue_summary()
    report = {"scope": "isolated_postgresql_real_catalogue_current_clock_readiness",
              "started_at": started.isoformat(), "clock_basis": "current_wall_clock",
              "clock_is_fixed_for_read_gates": False, "historical_replay": False,
              "image_id": os.environ["AUDIT_IMAGE_ID"],
              "archive_sha256": os.environ["AUDIT_ARCHIVE_SHA256"],
              "catalogue_is_synthetic": False, "profile_and_reactions_are_controlled": True,
              "reaction_protocol": {"real_style_swipes": 15, "like": 10, "dislike": 5},
              "catalogue_fingerprint_algorithm": "sha256_of_ordered_postgresql_md5_row_aggregate",
              "catalogue_before": snapshot, **summary, "profiles": []}
    for index, configuration in enumerate(PROFILES, 1):
        report["profiles"].append(run_profile(index, configuration, snapshot))
        print("readiness_profile_completed " + str(index), file=sys.stderr, flush=True)
    after, _ = read_catalogue_summary()
    require(after == snapshot, "catalogue_changed_during_readiness")
    report.update(catalogue_after=after, catalogue_unchanged=True,
                  stock_timestamps_modified=False, test_writes_rolled_back=True,
                  synthetic_users_rolled_back=True, checks_passed=True,
                  finished_at=datetime.now(timezone.utc).isoformat())
    report["release_gate"] = readiness_gates(report["profiles"])
    report["product_ready"] = all(report["release_gate"].values())
    report["result"] = "passed" if report["product_ready"] else "real_catalogue_coverage_failed"
    report["limits"] = [
        "Ten controlled profiles and reactions measure exact-size coverage, not human taste quality.",
        "Incomplete activation intent results are internal configured-profile diagnostics; the API remains gated.",
        "Current clocks remain live; stock may age out while the audit runs.",
        "This is isolated service validation, not production request latency or provider availability verification.",
    ]
    print(json.dumps(report, ensure_ascii=True), flush=True)


if __name__ == "__main__":
    try:
        main()
    except Exception as exc:
        print(json.dumps({"scope": "isolated_postgresql_real_catalogue_current_clock_readiness",
                          "result": "failed", "error_type": type(exc).__name__,
                          "reason": exc.reason if isinstance(exc, ReadinessFailure) else "raw_error_omitted",
                          "raw_error_omitted": True}), flush=True)
        raise SystemExit(1) from None
