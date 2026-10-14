"""Run the seven acceptance scenarios against guarded, isolated fixtures."""
from pathlib import Path
import json, os, sys, unittest
from unittest.mock import patch

ROOT = Path(__file__).resolve().parents[1]
CASES = [
    ('1_exact_size_before_score', 'test_shopping_intent.ShoppingIntentTests.test_exact_size_price_color_and_stock_precede_score'),
    ('2_hard_budget', 'test_shopping_intent.ShoppingIntentTests.test_dress_hard_budget_excludes_12000_before_ranking'),
    ('3_no_heels', 'test_shopping_intent.ShoppingIntentTests.test_no_heel_requires_explicit_flat_evidence'),
    ('4_context_not_taste', 'test_personalization_dna_context.PersonalizationDNAContextTests.test_context_changes_order_without_changing_permanent_taste'),
    ('5_exposure_aware_brand', 'test_behavior_profile.BehaviorProfileTests.test_three_of_four_differs_from_three_of_a_hundred'),
    ('6_missing_brand_dna', 'test_shopping_intent.ShoppingIntentTests.test_missing_favourite_brand_still_uses_product_dna'),
    ('7_too_expensive_session_only', 'test_shopping_intent.ShoppingIntentTests.test_task_feedback_is_price_or_session_only_and_never_a_style_dislike'),
]

def main():
    os.chdir(ROOT/'backend'); sys.path[:0] = [str(ROOT/'backend'), str(ROOT/'backend/tests')]
    env = {key: os.environ[key] for key in ('SYSTEMROOT','WINDIR','PATH','TEMP','TMP') if key in os.environ}
    env.update(DATABASE_URL='sqlite:///:memory:', JWT_SECRET='SYNTHETIC_ISOLATED_JWT', APP_ENV='test', PYTHON_DOTENV_DISABLED='1')
    report = []
    with patch.dict(os.environ, env, clear=True), patch('dotenv.load_dotenv'):
        for scenario, name in CASES:
            result = unittest.TextTestRunner(verbosity=1).run(unittest.defaultTestLoader.loadTestsFromName(name))
            report.append({'scenario': scenario, 'test': name, 'passed': result.wasSuccessful()})
    document = {'environment': 'local_sqlite_isolated_fixtures', 'real_catalog_validated': False,
        'production_changed': False, 'scenarios': report, 'passed': all(row['passed'] for row in report)}
    (ROOT/'docs/modish2/intent-acceptance-20261008.json').write_text(json.dumps(document,indent=2)+'\n')
    print(json.dumps(document)); return 0 if document['passed'] else 1

if __name__ == '__main__': raise SystemExit(main())
