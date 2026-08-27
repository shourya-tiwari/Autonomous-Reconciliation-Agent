"""Tests for recon.matching — the deterministic layer and its bucketing.

The headline test walks the whole evaluation corpus and asserts every record
lands in the bucket `data/ground_truth/matches.csv` says it should. That file is
the accuracy contract for this layer: it must resolve the clean and
fuzzy-solvable cases with zero errors and punt the genuinely ambiguous ones.
"""

from __future__ import annotations

import csv
from datetime import date
from decimal import Decimal

import pytest

from config import settings
from recon.audit import AuditLogger
from recon.ingest import CanonicalTxn, Direction, IngestResult, Source, load_all
from recon.matching import Bucket, reconcile, score_pair
from recon.matching.exact import exact_pairs

# ground-truth bucket -> acceptable engine buckets
_ACCEPTS: dict[str, set[Bucket]] = {
    "auto_resolved": {Bucket.MATCHED},
    "escalated": {Bucket.AMBIGUOUS},
    "exception": {Bucket.EXCEPTION},
    "ignored": {Bucket.IGNORED},
    "failed": {Bucket.IGNORED},  # the malformed row never reaches matching
}


@pytest.fixture(scope="module")
def ingested() -> IngestResult:
    return load_all()


@pytest.fixture(scope="module")
def report(ingested):
    return reconcile(ingested, AuditLogger(path=None))


@pytest.fixture(scope="module")
def ground_truth() -> dict[str, dict[str, str]]:
    with (settings.GROUND_TRUTH_DIR / "matches.csv").open(newline="", encoding="utf-8") as fh:
        return {r["record_id"]: r for r in csv.DictReader(fh)}


# --- the accuracy contract ----------------------------------------------


def test_every_matched_record_is_in_ground_truth(report, ground_truth):
    for decision in report.decisions:
        assert decision.record_id in ground_truth


def test_bucket_accuracy_is_total(report, ground_truth):
    """100% — the deterministic layer must not misbucket a single record."""
    wrong = []
    for d in report.decisions:
        expected = ground_truth[d.record_id]["expected_bucket"]
        if d.bucket not in _ACCEPTS[expected]:
            wrong.append((d.record_id, ground_truth[d.record_id]["case"], expected, d.bucket.value))
    assert not wrong, f"{len(wrong)} misbucketed: {wrong[:10]}"


@pytest.mark.parametrize(
    ("case", "expected_bucket"),
    [
        ("clean", Bucket.MATCHED),
        ("amount_rounding", Bucket.MATCHED),
        ("timing_offset", Bucket.MATCHED),
        ("name_drift", Bucket.MATCHED),
        ("missing_pg_ref", Bucket.MATCHED),
        ("partial_capture", Bucket.AMBIGUOUS),
        ("duplicate_ref", Bucket.AMBIGUOUS),
        ("fx_rounding", Bucket.AMBIGUOUS),
        ("unmatched_refund", Bucket.EXCEPTION),
        ("unmatched_bank_fee", Bucket.EXCEPTION),
        ("failed_payment", Bucket.IGNORED),
    ],
)
def test_each_case_lands_in_its_bucket(report, ground_truth, case, expected_bucket):
    ids = [rid for rid, r in ground_truth.items() if r["case"] == case]
    assert ids, f"no ground-truth rows for case {case!r}"
    for rid in ids:
        decision = report.by_id(rid)
        assert decision is not None, f"{rid} ({case}) got no decision"
        assert decision.bucket is expected_bucket, (
            f"{rid} ({case}): expected {expected_bucket}, got {decision.bucket} "
            f"— {decision.rationale}"
        )


# --- resolution rate ---------------------------------------------------


def test_matched_pairs_agree_with_ground_truth(report, ground_truth):
    """When the layer asserts a 1:1 match, it is the right counterpart."""
    checked = 0
    for d in report.decisions:
        if d.bucket is not Bucket.MATCHED or d.method not in ("exact", "fuzzy"):
            continue
        truth = ground_truth[d.record_id]["true_match_id"]
        assert d.matched_to == (truth,), f"{d.record_id}: matched {d.matched_to}, truth {truth}"
        checked += 1
    assert checked > 300  # sanity: most of the corpus is 1:1


def test_deterministic_layer_resolves_most_of_the_corpus(report):
    summary = report.summary()
    resolved = summary["matched"]
    assert resolved / summary["total"] > 0.6  # the baseline carries the bulk
    assert summary["unmatched-ambiguous"] > 0  # ...but genuinely punts the hard ones
    assert summary["unmatched-exception"] > 0


def test_running_twice_is_identical(ingested):
    a = reconcile(ingested, AuditLogger(path=None))
    b = reconcile(ingested, AuditLogger(path=None))
    assert [d.record_id for d in a.decisions] == [d.record_id for d in b.decisions]
    assert [d.bucket for d in a.decisions] == [d.bucket for d in b.decisions]


# --- settlement N:1 --------------------------------------------------


def test_settlements_match_the_recorded_groups(report):
    """Every matched settlement's member set is exactly one ground-truth group,
    and every group is accounted for."""
    with (settings.GROUND_TRUTH_DIR / "settlement_groups.csv").open(
        newline="", encoding="utf-8"
    ) as fh:
        groups: dict[str, set[str]] = {}
        for row in csv.DictReader(fh):
            groups.setdefault(row["settlement_id"], set()).add(row["payment_id"])

    matched = [
        frozenset(d.matched_to)
        for d in report.decisions
        if d.method == "settlement-group" and d.bucket is Bucket.MATCHED
    ]
    expected = {frozenset(members) for members in groups.values()}

    assert len(matched) == len(matched_set := set(matched))  # no group matched twice
    assert matched_set == expected


# --- scoring unit tests ---------------------------------------------


def _txn(**kw) -> CanonicalTxn:
    base = {
        "txn_id": "t",
        "source": Source.GATEWAY,
        "ref_id": "order_1",
        "amount": Decimal("1000.00"),
        "currency": "INR",
        "value_date": date(2026, 6, 1),
        "counterparty": "Acme Industries",
        "direction": Direction.INFLOW,
        "status": "captured",
    }
    return CanonicalTxn(**{**base, **kw})


def test_identical_records_score_near_one():
    s = score_pair(_txn(source=Source.INVOICE), _txn())
    assert s.total > 0.95


def test_cross_currency_pair_is_capped_below_confident():
    s = score_pair(_txn(source=Source.INVOICE, currency="USD"), _txn(currency="INR"))
    assert not s.same_currency
    assert s.total < settings.MATCH_CONFIDENT


def test_large_amount_gap_is_capped_below_confident():
    a = _txn(source=Source.INVOICE, amount=Decimal("1000.00"))
    b = _txn(amount=Decimal("400.00"))  # partial capture
    s = score_pair(a, b)
    assert s.total < settings.MATCH_CONFIDENT


def test_rounding_within_tolerance_still_scores_high():
    a = _txn(source=Source.INVOICE, amount=Decimal("1000.00"))
    b = _txn(amount=Decimal("999.60"))
    assert score_pair(a, b).total > settings.MATCH_CONFIDENT


def test_exact_pairs_skips_ambiguous_keys():
    inv = _txn(source=Source.INVOICE, txn_id="INV-1")
    gw1 = _txn(txn_id="pay_1")
    gw2 = _txn(txn_id="pay_2")  # same key as gw1 -> duplicate reference
    pairs, inv_left, gw_left = exact_pairs([inv], [gw1, gw2])
    assert pairs == []
    assert len(inv_left) == 1 and len(gw_left) == 2
