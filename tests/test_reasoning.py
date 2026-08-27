"""Tests for recon.reasoning — runs fully offline (no API key, no network).

The layer has three paths: cache hit, cache miss + replay (deterministic
fallback), and a live Gemini call. Only the first two are exercised here; the
live call is covered by populating the cache with scripts/populate_llm_cache.py.
"""

from __future__ import annotations

import json
from datetime import date
from decimal import Decimal

import pytest

from config import settings
from recon.audit import AuditLogger
from recon.ingest import CanonicalTxn, Direction, Source, load_all
from recon.matching import Bucket, reconcile
from recon.reasoning import Outcome, ReasoningTimeout, run_reasoning
from recon.reasoning.llm_client import (
    CandidateView,
    GeminiReasoner,
    ReasoningError,
    ReasoningRequest,
    TxnView,
)
from recon.reasoning.prompts import PROMPT_VERSION, Decision, ReasoningOutput


def _txn(txn_id: str, source: Source, **kw) -> CanonicalTxn:
    base = {
        "txn_id": txn_id,
        "source": source,
        "ref_id": "order_1",
        "amount": Decimal("1000.00"),
        "currency": "INR",
        "value_date": date(2026, 6, 1),
        "counterparty": "Acme Industries",
        "direction": Direction.INFLOW,
        "status": "captured",
    }
    return CanonicalTxn(**{**base, **kw})


def _request(record: CanonicalTxn, *candidates: CanonicalTxn) -> ReasoningRequest:
    return ReasoningRequest(
        record=TxnView.of(record),
        candidates=tuple(
            CandidateView(TxnView.of(c), {"total": 0.55, "amount": 0.3}) for c in candidates
        ),
        deterministic_note="amount differs by 40%",
    )


def _seed_cache(cache_dir, request: ReasoningRequest, output: ReasoningOutput, model="m") -> str:
    key = request.cache_key(prompt_version=PROMPT_VERSION, model=model)
    cache_dir.mkdir(parents=True, exist_ok=True)
    (cache_dir / f"{key}.json").write_text(
        json.dumps({"key": key, "output": output.model_dump()}), encoding="utf-8"
    )
    return key


# --- cache key ---------------------------------------------------------


def test_cache_key_is_stable_and_field_order_independent():
    a = _txn("INV-1", Source.INVOICE)
    b = _txn("pay_1", Source.GATEWAY)
    r1 = _request(a, b)
    r2 = _request(a, b)
    assert r1.cache_key(prompt_version="v", model="m") == r2.cache_key(prompt_version="v", model="m")


def test_cache_key_changes_with_prompt_version():
    r = _request(_txn("INV-1", Source.INVOICE), _txn("pay_1", Source.GATEWAY))
    assert r.cache_key(prompt_version="v1", model="m") != r.cache_key(prompt_version="v2", model="m")


# --- fallback path (cache miss, replay-only) -------------------------


def test_cache_miss_in_replay_mode_returns_escalation_not_error(tmp_path):
    reasoner = GeminiReasoner(cache_dir=tmp_path, replay_only=True, model="m")
    result = reasoner.reason(_request(_txn("INV-1", Source.INVOICE), _txn("pay_1", Source.GATEWAY)))
    assert result.source == "fallback"
    assert result.decision is Decision.UNSURE
    assert result.confidence == 0.0
    assert "human review" in result.rationale
    assert reasoner.stats["fallback"] == 1


def test_replay_mode_never_needs_an_api_key(tmp_path):
    reasoner = GeminiReasoner(cache_dir=tmp_path, replay_only=True, api_key="", model="m")
    # must not raise
    reasoner.reason(_request(_txn("INV-1", Source.INVOICE), _txn("pay_1", Source.GATEWAY)))


# --- cache hit path -------------------------------------------------


def test_cached_response_is_returned_without_network(tmp_path):
    record, cand = _txn("INV-1", Source.INVOICE), _txn("pay_1", Source.GATEWAY)
    request = _request(record, cand)
    _seed_cache(
        tmp_path,
        request,
        ReasoningOutput(
            decision=Decision.MATCH,
            matched_candidate_id="pay_1",
            confidence=0.91,
            rationale="amounts reconcile once the 2% processor fee is applied",
        ),
    )
    reasoner = GeminiReasoner(cache_dir=tmp_path, replay_only=True, model="m")
    result = reasoner.reason(request)
    assert result.source == "cache"
    assert result.decision is Decision.MATCH
    assert result.is_confident_match
    assert reasoner.stats["cache"] == 1


def test_low_confidence_match_is_not_treated_as_confident(tmp_path):
    request = _request(_txn("INV-1", Source.INVOICE), _txn("pay_1", Source.GATEWAY))
    _seed_cache(
        tmp_path,
        request,
        ReasoningOutput(
            decision=Decision.MATCH,
            matched_candidate_id="pay_1",
            confidence=settings.LLM_CONFIDENCE_MIN - 0.05,
            rationale="probably the same but the shortfall is unexplained",
        ),
    )
    result = GeminiReasoner(cache_dir=tmp_path, replay_only=True, model="m").reason(request)
    assert result.decision is Decision.MATCH
    assert not result.is_confident_match  # below the floor -> must still escalate


# --- live mode guardrails -----------------------------------------


def test_live_mode_without_key_raises_a_clear_error(tmp_path):
    reasoner = GeminiReasoner(cache_dir=tmp_path, replay_only=False, api_key="", model="m")
    with pytest.raises(ReasoningError, match="GEMINI_API_KEY"):
        reasoner.reason(_request(_txn("INV-1", Source.INVOICE), _txn("pay_1", Source.GATEWAY)))


# --- injected failure mode hook ----------------------------------


def test_fail_once_id_raises_timeout_then_succeeds(tmp_path):
    record, cand = _txn("pay_flaky", Source.GATEWAY), _txn("INV-9", Source.INVOICE)
    request = _request(record, cand)
    _seed_cache(
        tmp_path,
        request,
        ReasoningOutput(decision=Decision.UNSURE, confidence=0.4, rationale="needs review"),
    )
    reasoner = GeminiReasoner(
        cache_dir=tmp_path, replay_only=True, model="m", fail_once_ids={"pay_flaky"}
    )
    with pytest.raises(ReasoningTimeout):
        reasoner.reason(request)
    # the retry (what task 1.7's retry wrapper does) now goes through
    result = reasoner.reason(request)
    assert result.source == "cache"


# --- batch runner over the real corpus ---------------------------


@pytest.fixture(scope="module")
def corpus():
    ingest = load_all()
    match_report = reconcile(ingest, AuditLogger(path=None))
    return ingest, match_report


def test_only_ambiguous_records_are_reasoned_over(corpus, tmp_path):
    ingest, match_report = corpus
    reasoner = GeminiReasoner(cache_dir=tmp_path, replay_only=True)
    report = run_reasoning(match_report, ingest, AuditLogger(path=None), reasoner)
    assert len(report.outcomes) == len(match_report.in_bucket(Bucket.AMBIGUOUS))


def test_fallback_run_escalates_everything_and_forces_nothing(corpus, tmp_path):
    ingest, match_report = corpus
    reasoner = GeminiReasoner(cache_dir=tmp_path, replay_only=True)
    report = run_reasoning(match_report, ingest, AuditLogger(path=None), reasoner)
    assert not report.with_outcome(Outcome.RESOLVED_MATCH)
    assert len(report.with_outcome(Outcome.ESCALATED)) == len(report.outcomes)
    assert report.stats["fallback"] == len(report.outcomes)


def test_every_reasoning_call_is_audited(corpus, tmp_path):
    ingest, match_report = corpus
    audit = AuditLogger(path=None)
    reasoner = GeminiReasoner(cache_dir=tmp_path, replay_only=True)
    report = run_reasoning(match_report, ingest, audit, reasoner)

    entries = audit.by_stage("reason")
    assert len(entries) == len(report.outcomes)
    for e in entries:
        assert e["rationale"]
        assert e["source"].endswith(PROMPT_VERSION)
        assert "record" in e["inputs"] and "llm_raw" in e["inputs"]


def _seed_real(match_report, ingest, tmp_path, output: dict) -> str:
    """Cache `output` against the first ambiguous record's request. Returns its id."""
    index = {t.txn_id: t for t in ingest.records}
    decision = next(d for d in match_report.in_bucket(Bucket.AMBIGUOUS) if d.candidates)
    record = index[decision.record_id]
    cands = [
        CandidateView(TxnView.of(index[c.txn_id]), c.score.as_dict()) for c in decision.candidates
    ]
    request = ReasoningRequest(TxnView.of(record), tuple(cands), decision.rationale)
    output.setdefault("matched_candidate_id", cands[0].txn.txn_id)
    key = request.cache_key(prompt_version=PROMPT_VERSION, model=GeminiReasoner().model)
    tmp_path.mkdir(parents=True, exist_ok=True)
    (tmp_path / f"{key}.json").write_text(
        json.dumps({"key": key, "output": output}), encoding="utf-8"
    )
    return decision.record_id


def test_confident_cross_currency_match_escalates_with_the_finding(corpus, tmp_path):
    """The corpus's ambiguous records all carry a residual amount variance (FX,
    partial capture, duplicate). A confident LLM 'match' must still escalate
    those — but the audit rationale now names the counterpart the LLM found."""
    ingest, match_report = corpus
    rid = _seed_real(
        match_report,
        ingest,
        tmp_path,
        {
            "decision": "match",
            "confidence": 0.93,
            "rationale": "same reference and date; the amount gap is the applied FX rate",
        },
    )
    reasoner = GeminiReasoner(cache_dir=tmp_path, replay_only=True)
    report = run_reasoning(match_report, ingest, AuditLogger(path=None), reasoner)
    outcome = report.by_id(rid)
    assert outcome.outcome is Outcome.ESCALATED
    assert outcome.matched_to == ()
    assert "variance needs review" in outcome.rationale


def test_persistent_llm_failure_escalates_that_record_and_continues(corpus, tmp_path):
    """If the reasoner keeps raising (retries exhausted), the batch escalates the
    failed record and still processes the rest."""
    ingest, match_report = corpus

    class AlwaysFails(GeminiReasoner):
        def reason(self, request):
            raise ReasoningError("gemini call failed: 503")

    reasoner = AlwaysFails(cache_dir=tmp_path, replay_only=True)
    report = run_reasoning(match_report, ingest, AuditLogger(path=None), reasoner)
    assert len(report.outcomes) == len(match_report.in_bucket(Bucket.AMBIGUOUS))
    assert all(o.outcome is Outcome.ESCALATED for o in report.outcomes)
    assert all("failed after retries" in o.rationale for o in report.outcomes)


def test_map_outcome_resolves_a_clean_same_currency_match():
    from recon.reasoning import _map_outcome
    from recon.reasoning.llm_client import ReasoningResult

    result = ReasoningResult(
        decision=Decision.MATCH,
        matched_candidate_id="pay_1",
        confidence=0.9,
        rationale="amount agrees once the documented processor fee is applied",
        source="cache",
        model="m",
        prompt_version=PROMPT_VERSION,
    )
    scores = {"pay_1": {"same_currency": True, "amount": 0.95}}
    outcome = _map_outcome("INV-1", result, scores)
    assert outcome.outcome is Outcome.RESOLVED_MATCH
    assert outcome.matched_to == ("pay_1",)
