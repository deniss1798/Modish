"""A diversity slate must not starve because its last separator was spent."""
from collections import Counter
from functools import lru_cache
from itertools import product
from math import ceil
from types import SimpleNamespace
import unittest

from app.services.activation_service import CATEGORIES, diverse_products


class DiversityCompletionTests(unittest.TestCase):
  def rows(self, counts):
    return [SimpleNamespace(id=f'{category}-{index}', category=CATEGORIES[category], brand='BAON')
            for category, count in enumerate(counts) for index in range(count)]

  def assert_diverse(self, rows, limit):
    counts = Counter(row.category for row in rows)
    self.assertTrue(all(count <= max(3, ceil(limit / 4)) for count in counts.values()))
    self.assertTrue(all(len({p.category for p in rows[index:index + 3]}) > 1
                        for index in range(len(rows) - 2)))
    self.assertEqual(len({p.id for p in rows}), len(rows))

  def test_four_category_blocks_fill_twenty_without_relaxing_diversity(self):
    # The old greedy code returns 18: only the last category remains and the
    # last two cards already use it, although four groups of five are feasible.
    for explore in (False, True):
      with self.subTest(exploration=explore):
        sources = {}
        rows = diverse_products(self.rows([5, 5, 5, 5]), 20,
            exploration=explore, selection_sources=sources)
        self.assertEqual(len(rows), 20)
        self.assert_diverse(rows, 20)
        self.assertEqual(set(sources), {p.id for p in rows})
        self.assertTrue(all(index % 5 == 4 for index, row in enumerate(rows)
                            if sources[row.id] == 'soft_exploration'))

  def test_sparse_pool_stays_honestly_short_and_explicit_category_fallback_is_preserved(self):
    rows = self.rows([20])
    self.assertEqual(len(diverse_products(rows, 20)), 2)
    self.assertEqual(len(diverse_products(rows, 20, allow_category_fallback=True)), 20)

  def test_fifth_card_policy_still_explores_when_multiple_viable_choices_exist(self):
    sources = {}
    rows = diverse_products(self.rows([15] * 6), 20, exploration=True, selection_sources=sources)
    self.assertEqual(len(rows), 20)
    self.assertEqual([index for index, row in enumerate(rows)
        if sources[row.id] == 'soft_exploration'], [4, 9, 14, 19])
    self.assert_diverse(rows, 20)

  def test_small_inventory_matrix_matches_independent_complete_search(self):
    # The reference enumerates sequences and has no separator-capacity formula.
    # It detects both avoidable starvation and accidentally relaxed constraints.
    for counts in product(range(5), repeat=3):
      for limit in (3, 5, 8, 12):
        cap = max(3, ceil(limit / 4))
        available = tuple(min(count, cap) for count in counts)
        @lru_cache(None)
        def best(left, last=-1, run=0):
          maximum = 0
          for category, count in enumerate(left):
            if count == 0 or category == last and run == 2:
              continue
            next_counts = list(left)
            next_counts[category] -= 1
            maximum = max(maximum, 1 + best(tuple(next_counts), category,
                run + 1 if category == last else 1))
          return maximum
        expected = min(limit, best(available))
        for explore in (False, True):
          with self.subTest(counts=counts, limit=limit, exploration=explore):
            rows = diverse_products(self.rows(counts), limit, exploration=explore)
            self.assertEqual(len(rows), expected)
            self.assert_diverse(rows, limit)


if __name__ == '__main__':
  unittest.main()
