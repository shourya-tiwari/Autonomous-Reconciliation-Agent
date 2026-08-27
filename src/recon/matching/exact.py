"""Exact 1:1 matching — the cheap first pass.

Two records match exactly when they agree on all of: a non-empty reference id,
the amount (to the paisa), the currency, and the calendar day. That is strict on
purpose: an exact hit needs no further reasoning and no audit scrutiny, so the
bar is "these are unambiguously the same transaction".

``exact_pairs`` returns the matched pairs and the two leftover pools. Anything a
row could exactly-match more than one of is *not* returned as an exact hit — it
is left in the pool for the fuzzy pass to surface as ambiguous (this is how the
duplicate-reference case is caught rather than mis-resolved).
"""

from __future__ import annotations

from collections import defaultdict

from recon.ingest import CanonicalTxn

_ExactKey = tuple[str, str, str, str]  # (ref_id, amount, currency, iso date)


def _key(txn: CanonicalTxn) -> _ExactKey | None:
    if not txn.ref_id:
        return None
    return (txn.ref_id, str(txn.amount), txn.currency, txn.value_date.isoformat())


def exact_pairs(
    left: list[CanonicalTxn], right: list[CanonicalTxn]
) -> tuple[list[tuple[CanonicalTxn, CanonicalTxn]], list[CanonicalTxn], list[CanonicalTxn]]:
    """Match ``left`` against ``right`` on the exact key.

    Returns ``(pairs, left_remaining, right_remaining)``. A key that maps to more
    than one record on either side is skipped entirely — ambiguous, not exact.
    """
    right_by_key: dict[_ExactKey, list[CanonicalTxn]] = defaultdict(list)
    for txn in right:
        key = _key(txn)
        if key is not None:
            right_by_key[key].append(txn)

    left_by_key: dict[_ExactKey, list[CanonicalTxn]] = defaultdict(list)
    for txn in left:
        key = _key(txn)
        if key is not None:
            left_by_key[key].append(txn)

    pairs: list[tuple[CanonicalTxn, CanonicalTxn]] = []
    consumed_left: set[str] = set()
    consumed_right: set[str] = set()

    for key, l_txns in left_by_key.items():
        r_txns = right_by_key.get(key, [])
        if len(l_txns) == 1 and len(r_txns) == 1:
            pairs.append((l_txns[0], r_txns[0]))
            consumed_left.add(l_txns[0].txn_id)
            consumed_right.add(r_txns[0].txn_id)

    left_remaining = [t for t in left if t.txn_id not in consumed_left]
    right_remaining = [t for t in right if t.txn_id not in consumed_right]
    return pairs, left_remaining, right_remaining
