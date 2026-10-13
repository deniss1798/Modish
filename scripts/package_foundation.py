"""Catalog Foundation layered on build13, preserving repeat and filter fixes."""
from pathlib import Path
import hashlib
import io
import json
import tarfile
import argparse

ROOT = Path(__file__).resolve().parents[1]
OVERLAY = (
    'app/api/catalog_admin.py', 'app/models.py', 'app/catalog_normalize.py', 'app/log_redaction.py',
    'app/integrations/admitad/feed_parser.py', 'app/services/catalog_audience.py',
    'app/services/catalog/feed_import_service.py', 'app/services/catalog/feed_row_mapper.py',
    'app/services/catalog/normalized_product.py', 'app/services/catalog/product_normalizer.py',
    'app/services/catalog/catalog_records.py', 'app/services/catalog/merchant_adapters.py',
    'app/services/catalog/quality_gate.py', 'app/services/catalog/versions.py',
    'alembic/versions/20260921_0022_catalog_records.py', 'tests/test_catalog_foundation.py',
    'app/services/activation_service.py', 'tests/test_activation.py',
    'app/api/onboarding.py', 'app/api/fit_profile.py', 'app/api/products.py',
    'app/services/catalog/size_evidence.py', 'app/services/catalog/stock_freshness.py',
    'app/services/catalog/renormalize_service.py', 'app/services/catalog/catalog_quality.py',
    'app/services/feed_filters.py', 'app/services/candidate_retrieval_service.py',
    'app/services/embedding_service.py', 'app/services/outfit_engine_v2.py', 'app/services/outfit_service.py',
    'app/schemas/api_product.py', 'tests/test_size_evidence.py', 'tests/test_stock_freshness.py',
    'tests/test_build11_fixes.py', 'tests/test_styling_regressions.py',
    'tests/test_catalog_device_regressions.py', 'tests/test_candidate_retrieval.py',
    'tests/test_mie_scoring.py', 'tests/test_outfit_engine_v2.py',
    'tests/test_recommendation_exclusions.py', 'tests/test_embedding_service.py', 'tests/test_feedback_semantics.py',
    'app/api/admin_products.py', 'tests/test_admin_stock_import.py',
    'tests/recommendation_benchmark/test_offline_benchmark.py',
    'app/api/affiliate.py', 'app/services/product_analytics_service.py',
    'app/services/user_twin_service.py', 'app/services/mie_scoring.py',
    'app/services/catalog/fashion_taxonomy.py', 'app/services/catalog/product_dna.py',
    'tests/test_activation_metrics.py', 'tests/test_analytics_measurement.py',
    'tests/test_product_dna.py', 'tests/test_product_dna_integration.py',
    'app/services/recommendation_context.py', 'app/services/recommendation_engine.py',
    'app/services/recommendation_config.py', 'app/services/product_search.py',
    'app/services/outfit_quality.py', 'tests/test_personalization_dna_context.py',
    'app/services/activation_policy.py', 'app/services/profile_repository.py',
    'app/services/personalization_scoring.py', 'app/services/product_event_service.py',
    'app/schemas/product_event.py', 'app/api/recommendations.py',
    'tests/test_architecture_regressions.py',
    'app/api/serializers.py', 'app/services/onboarding_service.py',
    'app/schemas/fit_sizes.py', 'app/services/fit_size_profile.py',
    'app/services/product_eligibility.py', 'app/services/catalog/size_resolver.py',
    'alembic/versions/20261007_0023_fit_sizes.py',
    'tests/fit_fixtures.py', 'tests/test_size_resolver.py',
    'tests/test_availability_state.py', 'tests/test_fit_size_profile.py',
    'tests/test_exact_eligibility.py', 'tests/test_feed_filters.py',
    'tests/test_recommendation_v3.py', 'tests/test_release6_regressions.py',
    'tests/test_user_twin_service.py', 'tests/test_activation_category_filters.py',
    'app/main.py', 'app/api/shopping_intents.py', 'app/api/outfits.py',
    'app/schemas/shopping_intent.py', 'app/services/shopping_intent_parser.py',
    'app/services/shopping_intent_service.py', 'app/services/intent_eligibility.py',
    'app/services/intent_outfits.py', 'tests/test_shopping_intent.py',
    'alembic/versions/20261008_0024_shopping_intents.py',
    'app/services/behavior_profile.py', 'app/api/taste_profile.py',
    'app/services/intent_explanations.py', 'app/services/intent_metrics.py', 'app/api/analytics.py',
    'tests/test_behavior_profile.py', 'tests/test_intent_http.py',
    'tests/test_size_evidence_loss.py', 'tests/test_semantic_ranking_performance.py',
    'tests/test_wow_admission_performance.py',
    'tests/test_size_admission_performance.py',
    'tests/test_five_factor_ranking.py', 'tests/test_mie_active_ranking.py',
    'tests/test_optional_dislike_reason.py', 'tests/test_intent_outfit_size_matches.py',
    'tests/test_ranking_explanation_contract.py',
    'tests/test_activation_size_reasons.py',
    'app/services/catalog/candidate_scan.py', 'tests/test_size_candidate_scan.py',
    'app/middleware.py', 'tests/test_rate_limit_lifecycle.py',
    'app/services/product_identity.py', 'tests/test_product_identity_resilience.py',
    'tests/test_intent_identity_order.py',
)


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument('--name', default='foundation-v11')
    args = parser.parse_args()
    assert args.name.replace('-', '').isalnum(), 'Use a simple release name'
    deployed = json.loads((ROOT/'docs/modish2/build13-server-manifest.json').read_text())
    record = json.loads((ROOT/'docs/modish2/build13-deployment-record.json').read_text())
    assert record['status'] == 'deployed'
    base_archive = ROOT/'.tmp/modish-build13-server.tar'
    assert hashlib.sha256(base_archive.read_bytes()).hexdigest() == record['archive_sha256']
    with tarfile.open(base_archive) as archive:
        assert json.load(archive.extractfile('source-manifest.json')) == deployed
        entries = {n: archive.extractfile(n).read() for n in deployed['files']}
    assert all(hashlib.sha256(entries[n]).hexdigest() == sha for n, sha in deployed['files'].items())
    deployed_identity = entries['backend/app/services/product_identity.py']
    for name in OVERLAY:
        entries['backend/'+name] = (ROOT/'backend'/name).read_bytes().replace(b'\r\n', b'\n')
    # Local credential-configuration work is independent of Catalog Foundation.
    # Retain the deployed import enablement; include only the harmless redactor.
    name = 'backend/app/services/catalog/feed_import_service.py'
    code = entries[name].decode()
    for fragment in (
        'from ...config import get_admitad_export_config\n',
        '    if source.network == "admitad" and get_admitad_export_config() is None:\n      raise ValueError("Admitad export is disabled")\n',
    ):
        assert fragment in code
        code = code.replace(fragment, '')
    entries[name] = code.encode()
    # The next catalogue release must not undo the live duplicate-card fix.
    assert b'RecommendationEventV2.created_at >= now - timedelta(days=30)' in entries['backend/app/api/products.py']
    # Preserve every deployed identity/dedup rule. The only approved change
    # keeps malformed historic image URLs opaque instead of aborting a feed.
    old_parse = b'  url = urlsplit("https:" + value if value.startswith("//") else value)\n'
    new_parse = (b'  try:\n    url = urlsplit("https:" + value if value.startswith("//") else value)\n'
        b'  except ValueError:\n'
        b'    # Hidden/previously viewed rows may predate URL validation. Keep their\n'
        b'    # opaque identity without allowing one malformed image to abort retrieval.\n'
        b'    return value\n')
    assert deployed_identity.count(old_parse) == 1
    assert entries['backend/app/services/product_identity.py'] == deployed_identity.replace(old_parse, new_parse)
    assert 'backend/tests/test_feed_repeat_regressions.py' in entries
    assert 'backend/tests/test_activation_category_filters.py' in entries
    assert b'allow_category_fallback=bool(filters and filters.categories)' in entries['backend/app/services/activation_service.py']
    manifest = {'release': 'catalog-foundation', 'base_image': record['image_id'],
        'overlay': list(OVERLAY), 'migration': '20261008_0024',
        'files': {n: hashlib.sha256(data).hexdigest() for n, data in entries.items()}}
    entries['source-manifest.json'] = json.dumps(manifest, indent=2).encode()
    target = ROOT/f'.tmp/modish-{args.name}.tar'
    if target.exists():
        raise FileExistsError(f'Release archive is immutable: {target.name}; use a new name')
    with tarfile.open(target, 'w') as archive:
        for name, data in entries.items():
            info = tarfile.TarInfo(name)
            info.size = len(data)
            info.mode = 0o755 if name.endswith('.sh') else 0o644
            archive.addfile(info, io.BytesIO(data))
    (ROOT/f'docs/modish2/{args.name}-server-manifest.json').write_text(json.dumps(manifest, indent=2)+'\n')
    print(json.dumps({'sha256': hashlib.sha256(target.read_bytes()).hexdigest(), 'files': len(manifest['files'])}))


if __name__ == '__main__':
    main()
