"""Tests for recon.eval — the metrics are computed correctly and honestly.

The scoring code is the one place where a bug would flatter the submission, so
these tests pin the arithmetic against hand-built cases as well as checking the
real run.
"""

from __future__ import annotations

import json

import pytest

from recon.agent import FinalBucket, run_pipeline
from recon.agent.orchestrator import PipelineResult, RecordOutcome
from recon.audit import AuditLogger
from recon.eval.metrics import compute, load_ground_truth, load_settlement_groups, render_summary
from recon.rag import PolicyIndex


@pytest.fixture(scope="module")
def index() -> PolicyIndex:
    idx = PolicyIndex()
    try:
        idx.query("input tax credit", k=1)
    except Exception as exc:  # noqa: BLE001
        pytest.skip(f"embedding model unavailable offline: {exc}")
    return idx


@pytest.fixture(scope="module")
def scored(index):
    result = run_pipeline(audit=AuditLogger(path=None), index=index)
    return result, compute(result, setup_seconds=1.0)


# --- ground-truth loading ---------------------------------------------


def test_ground_truth_loads():
    truth = load_ground_truth()
    assert len(truth) > 600
    assert {"record_id", "source", "true_match_id", "expected_bucket", "case"} <= set(
        next(iter(truth.values()))
    )


def test_settlement_groups_load():
    groups = load_settlement_groups()
    assert groups
    assert all(isinstance(members, frozenset) and members for members in groups.values())


# --- arithmetic on hand-built cases ----------------------------------


def _outcome(rid, source, bucket, matched=()):
    return RecordOutcome(rid, source, bucket, "match", "exact", 1.0, tuple(matched))


def test_precision_and_recall_arithmetic():
    truth = {
        "a": {"record_id": "a", "source": "invoice", "true_match_id": "x",
              "expected_bucket": "auto_resolved", "case": "clean"},
        "b": {"record_id": "b", "source": "invoice", "true_match_id": "y",
              "expected_bucket": "auto_resolved", "case": "clean"},
        "c": {"record_id": "c", "source": "invoice", "true_match_id": "z",
              "expected_bucket": "escalated", "case": "fx"},
    }
    result = PipelineResult(
        outcomes=[
            _outcome("a", "invoice", FinalBucket.AUTO_RESOLVED, ["x"]),   # correct
            _outcome("b", "invoice", FinalBucket.AUTO_RESOLVED, ["WRONG"]),  # wrong pairing
            _outcome("c", "invoice", FinalBucket.ESCALATED),              # not asserted
        ],
        elapsed_seconds=1.0,
    )
    m = compute(result, truth, {})
    assert m.matches_asserted == 2
    assert m.matches_correct == 1
    assert m.matches_expected == 3
    assert m.match_precision == 0.5          # 1 of 2 asserted
    assert m.match_recall == pytest.approx(0.3333, abs=1e-4)  # 1 of 3 true
    assert m.bucket_accuracy == pytest.approx(1.0)  # buckets are all right


def test_bucket_accuracy_counts_misbucketed():
    truth = {
        "a": {"record_id": "a", "source": "bank", "true_match_id": "",
              "expected_bucket": "exception", "case": "refund"},
    }
    result = PipelineResult(
        outcomes=[_outcome("a", "bank", FinalBucket.AUTO_RESOLVED)], elapsed_seconds=1.0
    )
    m = compute(result, truth, {})
    assert m.bucket_accuracy == 0.0
    assert m.misbucketed[0]["expected"] == "exception"
    assert m.misbucketed[0]["got"] == "auto_resolved"


def test_record_missing_from_ground_truth_is_flagged_not_ignored():
    result = PipelineResult(
        outcomes=[_outcome("ghost", "invoice", FinalBucket.AUTO_RESOLVED)], elapsed_seconds=1.0
    )
    m = compute(result, {}, {})
    assert m.bucket_accuracy == 0.0
    assert m.misbucketed[0]["case"] == "not-in-ground-truth"


def test_setup_time_is_reported_separately_not_folded_in():
    result = PipelineResult(
        outcomes=[_outcome("a", "invoice", FinalBucket.AUTO_RESOLVED)], elapsed_seconds=2.0
    )
    m = compute(result, {}, {}, setup_seconds=8.0)
    assert m.elapsed_seconds == 2.0
    assert m.setup_seconds == 8.0
    assert m.throughput_rps == 0.5              # 1 record / 2s of pipeline
    assert m.throughput_rps_incl_setup == 0.1   # 1 record / 10s total


def test_empty_run_does_not_divide_by_zero():
    m = compute(PipelineResult(outcomes=[], elapsed_seconds=0.0), {}, {})
    assert m.n_records == 0
    assert m.throughput_rps == 0.0
    assert m.bucket_accuracy == 0.0


# --- the real run ----------------------------------------------------


def test_bucket_accuracy_on_the_corpus_is_total(scored):
    _, m = scored
    assert m.misbucketed == []
    assert m.bucket_accuracy == 1.0


def test_precision_is_perfect_and_recall_is_the_escalation_tradeoff(scored):
    _, m = scored
    assert m.match_precision == 1.0, "asserted a wrong pairing — this must never happen"
    assert 0.7 < m.match_recall < 1.0  # the shortfall is escalations, by design
    assert m.matches_expected > m.matches_correct


def test_settlement_groups_all_matched(scored):
    _, m = scored
    assert m.settlements_total > 0
    assert m.settlement_accuracy == 1.0


def test_shares_sum_to_one(scored):
    _, m = scored
    total = (
        m.auto_resolved_pct + m.escalated_pct + m.exception_pct + m.ignored_pct + m.failed_pct
    )
    assert total == pytest.approx(1.0, abs=0.005)


def test_llm_sees_only_a_small_slice(scored):
    _, m = scored
    assert m.llm_call_count / m.n_records < 0.25
    assert m.rag_call_count / m.n_records < 0.15


def test_failure_modes_are_reflected_in_the_metrics(scored):
    _, m = scored
    assert m.retry_count == 1        # the injected timeout, absorbed
    assert m.failed_pct > 0          # the malformed row, rejected


def test_metrics_serialise_to_json(scored):
    _, m = scored
    payload = json.loads(json.dumps(m.as_dict()))
    assert payload["bucket_accuracy"] == m.bucket_accuracy
    assert "misbucketed" in payload


def test_summary_is_readable_and_states_the_tradeoff(scored):
    result, m = scored
    text = render_summary(m, result)
    assert "# Reconciliation run" in text
    assert "Bucket accuracy" in text
    assert "not 100%, on purpose" in text  # recall is explained, not hidden
    assert "Failure modes handled" in text
    assert "None." in text  # no misbucketed records
