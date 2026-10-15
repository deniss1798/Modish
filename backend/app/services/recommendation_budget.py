"""Cooperative request-local computation budget, separate from claim leases.

SQL gets the remaining deadline. Streaming/scoring/composition loops cooperate
between items/batches; an individual Python operation is not preempted.
"""
from time import monotonic
from sqlalchemy import text

RANKING_SECONDS = 15.0
TIMEOUT_MESSAGE = 'Подбор занял слишком много времени. Повторите попытку позже.'
_DEADLINE = 'recommendation_ranking_deadline'
_SQL_BUDGET = 'recommendation_sql_budget_seconds'


class RecommendationRankingTimeout(RuntimeError):
    pass


def begin_ranking(db, *, seconds=RANKING_SECONDS):
    if seconds <= 0:
        raise ValueError('Ranking budget must be positive')
    db.info[_DEADLINE] = monotonic() + seconds
    db.info.pop(_SQL_BUDGET, None)
    check_ranking_budget(db)


def check_ranking_budget(db):
    deadline = db.info.get(_DEADLINE)
    if deadline is None:
        return
    remaining = deadline - monotonic()
    if remaining <= 0:
        raise RecommendationRankingTimeout()
    # SET is configuration, allowed in READ ONLY. Refresh at most once per
    # remaining whole second, never an extra SQL round-trip per candidate.
    seconds = int(remaining)
    if db.get_bind().dialect.name == 'postgresql' and db.info.get(_SQL_BUDGET) != seconds:
        milliseconds = max(1, int(remaining * 1000))
        db.execute(text(f"SET LOCAL statement_timeout = '{milliseconds}ms'"))
        db.info[_SQL_BUDGET] = seconds
