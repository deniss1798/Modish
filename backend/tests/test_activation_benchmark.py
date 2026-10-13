"""Method safeguards for the synthetic activation replay, not business targets."""
import importlib.util
from pathlib import Path
import unittest

_path = Path(__file__).resolve().parents[2] / 'scripts' / 'benchmark_activation_feedback.py'
_spec = importlib.util.spec_from_file_location('activation_feedback_benchmark', _path)
benchmark = importlib.util.module_from_spec(_spec)
_spec.loader.exec_module(benchmark)


class ActivationBenchmarkTests(unittest.TestCase):
    def test_empty_rank_is_not_a_perfect_filter_or_relevance_score(self):
        metrics = benchmark.ranking_metrics([], desired_style='casual', valid_ids=set(), disliked_ids=set())
        self.assertTrue(metrics['empty'])
        self.assertIsNone(metrics['hard_filter_pass_rate'])
        self.assertIsNone(metrics['heldout_style_relevance'])

    def test_real_feedback_is_prefix_isolated_reproducible_and_never_uses_holdout(self):
        with benchmark.isolated_runtime():
            early = benchmark.replay_checkpoint('romantic', 10)
            late = benchmark.replay_checkpoint('romantic', 30)
            again = benchmark.replay_checkpoint('romantic', 10)
        self.assertEqual(early, again, 'Late state must not leak into a new early replay.')
        self.assertTrue(early['cold_feed_blocked'])
        self.assertFalse(late['cold_feed_blocked'])
        self.assertEqual(early['recorded_events'], 10)
        self.assertEqual(late['recorded_events'], 30)
        self.assertGreater(late['hard_rejected_count'], 0)
        self.assertEqual(late['heldout_events'], 0)
        self.assertEqual(early['heldout_ids_sha256'], late['heldout_ids_sha256'])
        self.assertGreater(late['learned_weights']['style']['romantic'], 0)
        self.assertLess(late['learned_weights']['style']['casual'], 0)
        self.assertGreater(late['learned_holdout']['heldout_style_relevance'],
                           late['control_holdout']['heldout_style_relevance'])
        self.assertEqual(late['candidate_count_after_exclusions'], late['hard_eligible_count'] - 30)
        self.assertEqual(late['learned_after_exclusions']['disliked_returned'], 0)
        self.assertEqual(late['learned_holdout']['hard_filter_pass_rate'], 1)
        self.assertEqual(late['learned_holdout']['unique'], 20)


if __name__ == '__main__':
    unittest.main()
