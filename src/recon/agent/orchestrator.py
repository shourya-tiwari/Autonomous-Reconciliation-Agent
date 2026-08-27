"""The agent loop: ingest → match → reason → ground → log.

``run_pipeline`` drives every stage and reduces the whole corpus to one
:class:`RecordOutcome` per input row, in one of five terminal buckets:

``auto_resolved``  reconciled without a human — deterministically, or by a
                   confident LLM judgment
``escalated``      a real ambiguity a person must settle; the LLM's finding (or
                   the reason it could not be obtained) is attached
``exception``      no counterpart exists; explained and cited from GST policy
``ignored``        no money moved (a failed payment) — correctly excluded
``failed``         the row could not even be parsed; rejected with a reason

Nothing is dropped and nothing is guessed: every input line lands in exactly one
bucket and every transition is written to the audit trail.

Two injected failure modes are handled here rather than merely claimed:

1. **Malformed row** — ingest rejects it with a per-field reason. It becomes a
   ``failed`` outcome, is logged, and the run continues.
2. **LLM API timeout** — the record id in ``injection_manifest.json`` is armed to
   time out once. :class:`~recon.agent.retry.RetryingReasoner` catches it, backs
   off, retries, and succeeds; the retry itself appears in the trail. Had the
   retries been exhausted, that one record would escalate and the run would still
   finish.
"""

from __future__ import annotations

import json
import time
from dataclasses import dataclass, field
from enum import StrEnum
from pathlib import Path

from config import settings
from recon.audit import AuditLogger
from recon.ingest import IngestResult, load_all
from recon.matching import Bucket, MatchReport, reconcile
from recon.rag import GroundingReport, PolicyIndex, run_grounding
from recon.reasoning import GeminiReasoner, Outcome, ReasoningReport, run_reasoning

from .retry import DEFAULT_POLICY, RetryingReasoner, RetryPolicy


class FinalBucket(StrEnum):
    AUTO_RESOLVED = "auto_resolved"
    ESCALATED = "escalated"
    EXCEPTION = "exception"
    IGNORED = "ignored"
    FAILED = "failed"


@dataclass(frozen=True)
class RecordOutcome:
    """Where one input row ended up, and why."""

    record_id: str
    source: str
    bucket: FinalBucket
    stage: str                       # the stage that decided: ingest|match|reason|ground
    method: str
    confidence: float
    matched_to: tuple[str, ...] = ()
    rationale: str = ""


@dataclass
class PipelineResult:
    outcomes: list[RecordOutcome] = field(default_factory=list)
    ingest: IngestResult | None = None
    matching: MatchReport | None = None
    reasoning: ReasoningReport | None = None
    grounding: GroundingReport | None = None
    audit: AuditLogger | None = None
    elapsed_seconds: float = 0.0
    retries: list[tuple[str, int, str]] = field(default_factory=list)

    def by_id(self, record_id: str) -> RecordOutcome | None:
        return next((o for o in self.outcomes if o.record_id == record_id), None)

    def in_bucket(self, bucket: FinalBucket) -> list[RecordOutcome]:
        return [o for o in self.outcomes if o.bucket is bucket]

    def in_bucket_by_name(self, bucket: str) -> list[RecordOutcome]:
        return [o for o in self.outcomes if o.bucket.value == bucket]

    def summary(self) -> dict[str, int | float]:
        counts = {b.value: len(self.in_bucket(b)) for b in FinalBucket}
        total = len(self.outcomes)
        return {
            **counts,
            "total": total,
            "elapsed_seconds": round(self.elapsed_seconds, 2),
            "throughput_rps": round(total / self.elapsed_seconds, 1) if self.elapsed_seconds else 0.0,
        }


def _armed_timeout_ids(data_dir: Path) -> set[str]:
    """The record id the corpus deliberately arms to time out once (failure mode 2)."""
    manifest = data_dir / "injection_manifest.json"
    if not manifest.exists():
        return set()
    target = json.loads(manifest.read_text(encoding="utf-8")).get("api_timeout_record_id")
    return {target} if target else set()


def run_pipeline(
    data_dir: Path | None = None,
    *,
    audit: AuditLogger | None = None,
    reasoner: object | None = None,
    index: PolicyIndex | None = None,
    retry_policy: RetryPolicy = DEFAULT_POLICY,
    inject_timeout: bool = True,
    live_llm: bool = False,
) -> PipelineResult:
    directory = Path(data_dir) if data_dir is not None else settings.SYNTHETIC_DIR
    if audit is None:
        audit = AuditLogger(path=None)
    started = time.perf_counter()

    result = PipelineResult(audit=audit)

    # 1 — ingest. Malformed rows become terminal `failed` outcomes (failure mode 1).
    ingest = load_all(directory)
    result.ingest = ingest
    for reject in ingest.rejects:
        audit.log(
            record_id=reject.record_id,
            stage="ingest",
            decision="rejected",
            confidence=1.0,
            source=f"validate:{reject.source}",
            inputs={"row_number": reject.row_number, "raw": reject.raw},
            rationale=reject.reason,
        )
        result.outcomes.append(
            RecordOutcome(
                record_id=reject.record_id,
                source=str(reject.source),
                bucket=FinalBucket.FAILED,
                stage="ingest",
                method="validate",
                confidence=1.0,
                rationale=f"unparseable row, excluded from matching — {reject.reason}",
            )
        )

    # 2 — deterministic matching.
    matching = reconcile(ingest, audit)
    result.matching = matching
    for decision in matching.decisions:
        if decision.bucket is Bucket.MATCHED:
            result.outcomes.append(
                RecordOutcome(
                    decision.record_id, decision.source, FinalBucket.AUTO_RESOLVED,
                    "match", decision.method, decision.confidence,
                    decision.matched_to, decision.rationale,
                )
            )
        elif decision.bucket is Bucket.IGNORED:
            result.outcomes.append(
                RecordOutcome(
                    decision.record_id, decision.source, FinalBucket.IGNORED,
                    "match", decision.method, decision.confidence,
                    rationale=decision.rationale,
                )
            )

    # 3 — LLM reasoning over the ambiguous bucket only, behind retry.
    if reasoner is None:
        reasoner = GeminiReasoner(
            replay_only=not live_llm,
            fail_once_ids=_armed_timeout_ids(directory) if inject_timeout else None,
        )

    def _log_retry(record_id: str, attempt: int, exc: BaseException, delay: float) -> None:
        audit.log(
            record_id=record_id,
            stage="agent",
            decision="retry",
            confidence=0.0,
            source=f"retry:attempt-{attempt}",
            inputs={"error": str(exc), "backoff_seconds": delay},
            rationale=(
                f"transient {type(exc).__name__} on attempt {attempt}; "
                f"retrying after {delay}s"
            ),
        )

    retrying = RetryingReasoner(reasoner, policy=retry_policy, on_retry=_log_retry)
    reasoning = run_reasoning(matching, ingest, audit, retrying)
    result.reasoning = reasoning
    result.retries = list(retrying.retries)
    by_source = {d.record_id: d.source for d in matching.decisions}
    for outcome in reasoning.outcomes:
        bucket = (
            FinalBucket.AUTO_RESOLVED
            if outcome.outcome is Outcome.RESOLVED_MATCH
            else FinalBucket.ESCALATED
        )
        result.outcomes.append(
            RecordOutcome(
                outcome.record_id, by_source.get(outcome.record_id, "?"), bucket,
                "reason", f"llm:{outcome.llm_source}", outcome.confidence,
                outcome.matched_to, outcome.rationale,
            )
        )

    # 4 — RAG grounding over the exception bucket only.
    grounding = run_grounding(matching, ingest, audit, index)
    result.grounding = grounding
    for explanation in grounding.explanations:
        primary = explanation.primary
        result.outcomes.append(
            RecordOutcome(
                explanation.record_id,
                by_source.get(explanation.record_id, "bank"),
                FinalBucket.EXCEPTION,
                "ground",
                f"rag:{explanation.exception_kind}",
                round(primary.score, 4) if primary else 0.0,
                rationale=f"{explanation.summary} {explanation.action}",
            )
        )

    result.elapsed_seconds = time.perf_counter() - started
    audit.log(
        record_id="__run__",
        stage="agent",
        decision="completed",
        confidence=1.0,
        source="orchestrator",
        inputs=result.summary(),
        rationale=(
            f"processed {len(result.outcomes)} records in "
            f"{result.elapsed_seconds:.2f}s; {len(result.retries)} retry(ies)"
        ),
    )
    return result
