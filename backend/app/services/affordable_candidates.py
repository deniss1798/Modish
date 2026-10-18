"""Bounded high-affinity candidates with a small affordable purchase reserve.

Admission remains the caller's responsibility. This selector does not relax a
size, stock, source or compatibility rule, and it never queries the database.
"""
import heapq


class AffordableCandidates:
    def __init__(self, *, limit, reserve=3):
        if not 1 <= limit <= 50 or not 0 <= reserve <= 3:
            raise ValueError('Invalid affordable candidate limits')
        self.limit = limit
        self.reserve = min(reserve, limit)
        self._best = []
        self._cheap = []
        self._best_ids = set()
        self._cheap_ids = set()

    @staticmethod
    def _offer(heap, ids, entry, capacity):
        if capacity == 0 or entry[1] in ids:
            return
        if len(heap) < capacity:
            heapq.heappush(heap, entry)
            ids.add(entry[1])
        elif entry[:2] > heap[0][:2]:
            removed = heapq.heapreplace(heap, entry)
            ids.remove(removed[1])
            ids.add(entry[1])

    def add(self, value, *, key, score, price):
        self._offer(self._best, self._best_ids, (score, key, value), self.limit)
        self._offer(self._cheap, self._cheap_ids, (-price, key, (score, value)), self.reserve)

    def ranked(self):
        best = sorted(self._best, key=lambda row: row[:2], reverse=True)
        selected = {key: (score, key, value)
            for score, key, value in best[:self.limit - self.reserve]}
        for _, key, (score, value) in self._cheap:
            selected[key] = (score, key, value)
        # A cheap candidate may already be among the strongest. Fill those
        # overlapping places rather than returning an unnecessarily short pool.
        for score, key, value in best:
            if len(selected) >= self.limit:
                break
            selected.setdefault(key, (score, key, value))
        return sorted(selected.values(), key=lambda row: row[:2], reverse=True)
