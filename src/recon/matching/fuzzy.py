"""Tolerant pair scoring for the deterministic matching layer.

``score_pair`` compares two canonical records and returns a
:class:`ScoreBreakdown` (each component 0..1). The components are combined into a
weighted total, with two hard rules baked in:

* **Different currencies never score as a confident match.** Cross-currency pairs
  (a USD gateway payment vs an INR invoice) can be *candidates* — the reference
  and date still line up — but the amount component is unknowable here, so the
  total is capped below the confident threshold and the pair is left for the LLM.
* **A real amount disagreement caps the total.** Partial captures and FX slips
  keep a matching reference and date; without the cap they would sail through as
  confident matches instead of being escalated.

The absolute weights don't matter, only their ratios; they were tuned so that on
the evaluation corpus each injected discrepancy lands in the bucket
`data/synthetic/mismatch_catalogue.md` says it should.
"""

from __future__ import annotations

from decimal import Decimal

from rapidfuzz import fuzz

from config import settings
from recon.ingest import CanonicalTxn

from .types import Candidate, ScoreBreakdown

# component weights (normalised at combine time to whatever signals are present)
_W_AMOUNT = 0.45
_W_DATE = 0.20
_W_NAME = 0.20
_W_REF = 0.15

# a pair whose amount component is below this has a real money disagreement...
_AMOUNT_DISPUTE = 0.5
# ...and its combined score is then capped here (-> ambiguous, not matched)
_DISPUTE_CAP = 0.60


def _amount_score(a: CanonicalTxn, b: CanonicalTxn) -> tuple[float, bool]:
    """0..1 on how well two amounts agree; second value is same-currency."""
    if a.currency != b.currency:
        return 0.0, False

    hi = max(a.amount, b.amount)
    if hi == 0:
        return 1.0, True
    diff = abs(a.amount - b.amount)
    rel = float(diff / hi)

    if diff <= settings.AMOUNT_ABS_TOLERANCE or rel <= settings.AMOUNT_REL_TOLERANCE:
        # within tolerance: 1.0 at exact, easing to ~0.85 at the tolerance edge
        edge = max(
            rel / settings.AMOUNT_REL_TOLERANCE if settings.AMOUNT_REL_TOLERANCE else 0.0,
            float(diff / settings.AMOUNT_ABS_TOLERANCE) if settings.AMOUNT_ABS_TOLERANCE else 0.0,
        )
        return 1.0 - 0.15 * min(1.0, edge), True

    # outside tolerance: decays fast, so a partial capture (20-60% short) scores low
    return max(0.0, 0.5 - rel), True


def _date_score(a: CanonicalTxn, b: CanonicalTxn) -> float:
    gap = abs((a.value_date - b.value_date).days)
    if gap == 0:
        return 1.0
    if gap > settings.DATE_WINDOW_DAYS:
        return 0.0
    return 1.0 - gap / (settings.DATE_WINDOW_DAYS + 1)


def _name_score(a: CanonicalTxn, b: CanonicalTxn) -> float | None:
    if not a.counterparty or not b.counterparty:
        return None
    ratio = fuzz.token_sort_ratio(a.counterparty.lower(), b.counterparty.lower()) / 100.0
    return ratio if ratio >= settings.NAME_SIMILARITY_MIN else ratio * 0.5


def _ref_score(a: CanonicalTxn, b: CanonicalTxn) -> float:
    if a.ref_id and b.ref_id and a.ref_id == b.ref_id:
        return 1.0
    return 0.0


def score_pair(a: CanonicalTxn, b: CanonicalTxn) -> ScoreBreakdown:
    amount, same_currency = _amount_score(a, b)
    date = _date_score(a, b)
    name = _name_score(a, b)
    ref = _ref_score(a, b)

    parts: list[tuple[float, float]] = [(amount, _W_AMOUNT), (date, _W_DATE), (ref, _W_REF)]
    if name is not None:
        parts.append((name, _W_NAME))
    total = sum(v * w for v, w in parts) / sum(w for _, w in parts)

    if not same_currency or amount < _AMOUNT_DISPUTE:
        total = min(total, _DISPUTE_CAP)

    return ScoreBreakdown(
        total=total, amount=amount, date=date, name=name, ref=ref, same_currency=same_currency
    )


def candidates_for(
    record: CanonicalTxn, pool: list[CanonicalTxn], *, limit: int = 5, floor: float = 0.35
) -> list[Candidate]:
    """Scored counterparts for ``record`` from ``pool``, best first, above ``floor``."""
    scored = [
        Candidate(txn_id=other.txn_id, source=str(other.source), score=score_pair(record, other))
        for other in pool
    ]
    scored = [c for c in scored if c.score.total >= floor]
    scored.sort(key=lambda c: c.score.total, reverse=True)
    return scored[:limit]


_ZERO = Decimal(0)


def net_of_fees(txns: list[CanonicalTxn]) -> Decimal:
    """Sum of amounts less processor fees — what a settlement actually pays out."""
    return sum((t.amount - (t.fee or _ZERO) for t in txns), _ZERO)
