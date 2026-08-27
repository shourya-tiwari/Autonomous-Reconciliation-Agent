"""Component 3 — LLM Reasoning Layer.

Input: the ``unmatched-ambiguous`` records from the deterministic layer ONLY
(cost/latency control). For each, the model is asked a single structured
question — does this record match exactly one of its candidates? — and returns
``{decision, confidence, rationale}``.

Outcome mapping:
* a confident ``match`` (``confidence >= LLM_CONFIDENCE_MIN``, naming a real
  candidate) → ``resolved-match``, the pair is auto-resolved.
* anything else → ``escalated``: the record goes to a human, with the model's
  rationale attached. Low confidence never forces a decision.

Every call is logged to the audit trail with the input context, the raw model
output, and the mapped outcome.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from enum import StrEnum

from recon.audit import AuditLogger
from recon.ingest import CanonicalTxn, IngestResult
from recon.matching import Bucket, MatchReport

from .llm_client import (
    CandidateView,
    GeminiReasoner,
    ReasoningError,
    ReasoningRequest,
    ReasoningResult,
    ReasoningTimeout,
    TxnView,
)
from .prompts import Decision

__all__ = [
    "Decision",
    "GeminiReasoner",
    "Outcome",
    "ReasoningError",
    "ReasoningOutcome",
    "ReasoningReport",
    "ReasoningRequest",
    "ReasoningResult",
    "ReasoningTimeout",
    "run_reasoning",
]


class Outcome(StrEnum):
    RESOLVED_MATCH = "resolved-match"
    ESCALATED = "escalated"


@dataclass(frozen=True)
class ReasoningOutcome:
    record_id: str
    outcome: Outcome
    matched_to: tuple[str, ...]
    confidence: float
    rationale: str
    llm_decision: str
    llm_source: str          # "cache" | "gemini" | "fallback"
    prompt_version: str


@dataclass
class ReasoningReport:
    outcomes: list[ReasoningOutcome] = field(default_factory=list)
    stats: dict[str, int] = field(default_factory=dict)

    def by_id(self, record_id: str) -> ReasoningOutcome | None:
        return next((o for o in self.outcomes if o.record_id == record_id), None)

    def with_outcome(self, outcome: Outcome) -> list[ReasoningOutcome]:
        return [o for o in self.outcomes if o.outcome is outcome]

    def summary(self) -> dict[str, int]:
        out = {o.value: 0 for o in Outcome}
        for o in self.outcomes:
            out[o.outcome.value] += 1
        out["total"] = len(self.outcomes)
        return {**out, "llm_source": dict(self.stats)}


def run_reasoning(
    match_report: MatchReport,
    ingest: IngestResult,
    audit: AuditLogger | None = None,
    reasoner: GeminiReasoner | None = None,
) -> ReasoningReport:
    if audit is None:
        audit = AuditLogger(path=None)
    reasoner = reasoner or GeminiReasoner()
    index: dict[str, CanonicalTxn] = {t.txn_id: t for t in ingest.records}

    report = ReasoningReport()
    for decision in match_report.in_bucket(Bucket.AMBIGUOUS):
        record = index.get(decision.record_id)
        candidates = [
            CandidateView(txn=TxnView.of(index[c.txn_id]), score=c.score.as_dict())
            for c in decision.candidates
            if c.txn_id in index
        ]
        if record is None or not candidates:
            report.outcomes.append(
                _escalate(decision.record_id, "no candidates to reason over", "n/a", "none")
            )
            _log(audit, report.outcomes[-1], inputs={"candidates": 0})
            continue

        request = ReasoningRequest(
            record=TxnView.of(record),
            candidates=tuple(candidates),
            deterministic_note=decision.rationale,
        )
        try:
            # `reasoner` may be wrapped with retry (task 1.7); a ReasoningError
            # here means retries are exhausted — escalate this one record and
            # keep going rather than aborting the whole batch.
            result = reasoner.reason(request)
        except ReasoningError as exc:
            report.outcomes.append(
                _escalate(
                    decision.record_id,
                    f"LLM call failed after retries ({exc}); escalated for human review",
                    reasoner.prompt_version,
                    "error",
                )
            )
            _log(audit, report.outcomes[-1], inputs={"error": str(exc)})
            continue

        scores = {c.txn.txn_id: c.score for c in candidates}
        outcome = _map_outcome(decision.record_id, result, scores)
        report.outcomes.append(outcome)
        _log(
            audit,
            outcome,
            inputs={
                "record": request.record.__dict__,
                "candidates": [c.txn.txn_id for c in candidates],
                "llm_raw": result.raw or {
                    "decision": result.decision,
                    "confidence": result.confidence,
                },
            },
        )

    report.stats = dict(reasoner.stats)
    return report


def _map_outcome(
    record_id: str, result: ReasoningResult, candidate_scores: dict
) -> ReasoningOutcome:
    """A confident LLM match auto-resolves ONLY when the pairing has no residual
    money variance. A cross-currency or amount-dispute match is still the same
    transaction — but the unreconciled amount (forex gain/loss, a shortfall)
    needs a human, so it escalates *with the LLM's finding attached*."""
    matched = result.matched_candidate_id
    if result.is_confident_match and matched in candidate_scores:
        score = candidate_scores[matched]
        clean_amount = score.get("same_currency", False) and score.get("amount", 0.0) >= 0.7
        if clean_amount:
            return ReasoningOutcome(
                record_id, Outcome.RESOLVED_MATCH, (matched,), result.confidence,
                result.rationale, str(result.decision), result.source, result.prompt_version,
            )
        return ReasoningOutcome(
            record_id, Outcome.ESCALATED, (), result.confidence,
            f"LLM identifies this as {matched} but a residual amount variance needs "
            f"review — {result.rationale}",
            str(result.decision), result.source, result.prompt_version,
        )
    return ReasoningOutcome(
        record_id, Outcome.ESCALATED, (), result.confidence, result.rationale,
        str(result.decision), result.source, result.prompt_version,
    )


def _escalate(record_id: str, reason: str, prompt_version: str, source: str) -> ReasoningOutcome:
    return ReasoningOutcome(
        record_id=record_id,
        outcome=Outcome.ESCALATED,
        matched_to=(),
        confidence=0.0,
        rationale=reason,
        llm_decision="unsure",
        llm_source=source,
        prompt_version=prompt_version,
    )


def _log(audit: AuditLogger, outcome: ReasoningOutcome, *, inputs: dict) -> None:
    audit.log(
        record_id=outcome.record_id,
        stage="reason",
        decision=outcome.outcome.value,
        confidence=outcome.confidence,
        source=f"{outcome.llm_source}:{outcome.prompt_version}",
        matched_to=list(outcome.matched_to),
        inputs=inputs,
        rationale=outcome.rationale,
    )
