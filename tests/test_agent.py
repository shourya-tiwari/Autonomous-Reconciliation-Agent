"""Tests for recon.agent — the orchestrated loop and both injected failure modes.

The point of these tests is the claim the submission actually rests on: the run
completes, every input line lands in exactly one terminal bucket, and neither
injected failure mode stops it.
"""

from __future__ import annotations

import pytest

from recon.agent import FinalBucket, RetryingReasoner, RetryPolicy, call_with_retry, run_pipeline
from recon.agent.orchestrator import _armed_timeout_ids
from recon.audit import AuditLogger
from recon.rag import PolicyIndex
from recon.reasoning import GeminiReasoner, ReasoningError
from recon.reasoning.llm_client import ReasoningTimeout

FAST = RetryPolicy(attempts=3, base_delay=0.0)


@pytest.fixture(scope="module")
def index() -> PolicyIndex:
    idx = PolicyIndex()
    try:
        idx.query("input tax credit", k=1)
    except Exception as exc:  # noqa: BLE001 - offline model load failure
        pytest.skip(f"embedding model unavailable offline: {exc}")
    return idx


@pytest.fixture(scope="module")
def run(index):
    audit = AuditLogger(path=None)
    result = run_pipeline(audit=audit, index=index, retry_policy=FAST)
    return result, audit


# --- retry primitives ---------------------------------------------------


def test_retry_returns_on_first_success():
    calls = []
    assert call_with_retry(lambda: calls.append(1) or "ok", policy=FAST) == "ok"
    assert len(calls) == 1


def test_retry_absorbs_a_transient_failure():
    attempts = {"n": 0}

    def flaky():
        attempts["n"] += 1
        if attempts["n"] == 1:
            raise ReasoningTimeout("boom")
        return "recovered"

    assert call_with_retry(flaky, policy=FAST, sleep=lambda _: None) == "recovered"
    assert attempts["n"] == 2


def test_retry_reraises_after_exhausting_attempts():
    def always():
        raise ReasoningTimeout("still down")

    with pytest.raises(ReasoningTimeout, match="still down"):
        call_with_retry(always, policy=FAST, sleep=lambda _: None)


def test_non_transient_errors_are_not_retried():
    attempts = {"n": 0}

    def bad_request():
        attempts["n"] += 1
        raise ReasoningError("malformed request")

    with pytest.raises(ReasoningError):
        call_with_retry(bad_request, policy=FAST, sleep=lambda _: None)
    assert attempts["n"] == 1  # tried once, gave up — retrying would just waste time


def test_backoff_grows_and_is_capped():
    policy = RetryPolicy(attempts=6, base_delay=1.0, multiplier=2.0, max_delay=4.0)
    assert [policy.delay_for(i) for i in range(1, 6)] == [1.0, 2.0, 4.0, 4.0, 4.0]


def test_retrying_reasoner_reports_the_record_it_retried(tmp_path):
    from recon.reasoning.llm_client import CandidateView, ReasoningRequest, TxnView

    seen = []
    inner = GeminiReasoner(cache_dir=tmp_path, replay_only=True, fail_once_ids={"pay_x"})
    wrapper = RetryingReasoner(
        inner,
        policy=FAST,
        on_retry=lambda rid, attempt, exc, delay: seen.append((rid, attempt)),
        sleep=lambda _: None,
    )
    view = TxnView("pay_x", "gateway", "10.00", "INR", "2026-06-01", "Acme", "order_1", "captured")
    other = TxnView("INV-1", "invoice", "10.00", "INR", "2026-06-01", "Acme", "order_1", "booked")
    request = ReasoningRequest(view, (CandidateView(other, {"total": 0.5}),), "note")

    result = wrapper.reason(request)
    assert seen == [("pay_x", 1)]
    assert wrapper.retries[0][0] == "pay_x"
    assert result.source == "fallback"  # the retry went through
    assert wrapper.model == inner.model  # attribute delegation


# --- the orchestrated run --------------------------------------------


def test_run_completes_and_accounts_for_every_row(run):
    result, _ = run
    assert result.ingest is not None
    assert len(result.outcomes) == result.ingest.rows_read
    ids = [o.record_id for o in result.outcomes]
    assert len(ids) == len(set(ids)), "a record was bucketed twice"


def test_every_bucket_is_populated(run):
    result, _ = run
    for bucket in FinalBucket:
        assert result.in_bucket(bucket), f"nothing landed in {bucket}"


def test_stage_subsets_are_respected(run):
    """The LLM and RAG stages must see only their slice, not the whole corpus."""
    result, _ = run
    assert len(result.reasoning.outcomes) < len(result.outcomes) * 0.25
    assert len(result.grounding.explanations) < len(result.outcomes) * 0.15


# --- failure mode 1: the malformed row -------------------------------


def test_malformed_row_is_failed_not_fatal(run):
    result, _ = run
    failed = result.in_bucket(FinalBucket.FAILED)
    assert len(failed) == 1
    outcome = failed[0]
    assert outcome.record_id == "pay_MALFORMED0001"
    assert outcome.stage == "ingest"
    assert "amount" in outcome.rationale and "captured_at" in outcome.rationale


def test_malformed_row_is_in_the_audit_trail(run):
    _, audit = run
    entries = [e for e in audit.by_stage("ingest") if e["decision"] == "rejected"]
    assert len(entries) == 1
    assert entries[0]["inputs"]["raw"]["amount"] == "N/A"  # the original row survives


# --- failure mode 2: the LLM API timeout -----------------------------


def test_the_corpus_arms_a_timeout_target():
    from config import settings

    armed = _armed_timeout_ids(settings.SYNTHETIC_DIR)
    assert len(armed) == 1


def test_timeout_is_retried_and_absorbed(run):
    result, _ = run
    assert len(result.retries) == 1
    record_id, attempt, error = result.retries[0]
    assert attempt == 1
    assert "timed out" in error
    # the record still reached a terminal bucket rather than being lost
    outcome = result.by_id(record_id)
    assert outcome is not None
    assert outcome.bucket is not FinalBucket.FAILED


def test_retry_is_visible_in_the_audit_trail(run):
    _, audit = run
    retries = [e for e in audit.by_stage("agent") if e["decision"] == "retry"]
    assert len(retries) == 1
    assert retries[0]["inputs"]["error"]
    assert "retrying" in retries[0]["rationale"]


def test_exhausted_retries_escalate_rather_than_crash(index, tmp_path):
    """If the LLM never recovers, the run still finishes and nothing is lost."""

    class AlwaysTimesOut(GeminiReasoner):
        def reason(self, request):
            raise ReasoningTimeout("provider down")

    result = run_pipeline(
        audit=AuditLogger(path=None),
        index=index,
        reasoner=AlwaysTimesOut(cache_dir=tmp_path, replay_only=True),
        retry_policy=RetryPolicy(attempts=2, base_delay=0.0),
    )
    assert len(result.outcomes) == result.ingest.rows_read
    escalated = result.in_bucket(FinalBucket.ESCALATED)
    assert escalated
    assert all("failed after retries" in o.rationale for o in escalated)


def test_injection_can_be_disabled(index):
    result = run_pipeline(audit=AuditLogger(path=None), index=index, inject_timeout=False)
    assert result.retries == []


# --- audit completeness ---------------------------------------------


def test_trail_covers_every_stage(run):
    _, audit = run
    stages = {e["stage"] for e in audit.entries}
    assert {"ingest", "match", "reason", "ground", "agent"} <= stages


def test_run_is_closed_out_with_a_completion_record(run):
    result, audit = run
    completion = [e for e in audit.by_stage("agent") if e["decision"] == "completed"]
    assert len(completion) == 1
    assert completion[0]["inputs"]["total"] == len(result.outcomes)
