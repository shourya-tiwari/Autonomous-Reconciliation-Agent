"""Component 4 — RAG Grounding Layer.

Input: the ``unmatched-exception`` records from the deterministic layer ONLY.
For each, retrieves the governing clause(s) from the GST/tax policy vector store
and produces an explanation that **cites the specific clause** it relied on
(document title + verbatim quote + source). Every grounding is logged to the
audit trail.
"""

from __future__ import annotations

from dataclasses import dataclass, field

from recon.audit import AuditLogger
from recon.ingest import CanonicalTxn, IngestResult
from recon.matching import Bucket, MatchReport

from .ground import Citation, GroundedExplanation, ground_exception
from .index import Chunk, PolicyIndex, Retrieved, load_chunks

__all__ = [
    "Chunk",
    "Citation",
    "GroundedExplanation",
    "GroundingReport",
    "PolicyIndex",
    "Retrieved",
    "ground_exception",
    "load_chunks",
    "run_grounding",
]


@dataclass
class GroundingReport:
    explanations: list[GroundedExplanation] = field(default_factory=list)

    def by_id(self, record_id: str) -> GroundedExplanation | None:
        return next((e for e in self.explanations if e.record_id == record_id), None)

    def summary(self) -> dict:
        kinds: dict[str, int] = {}
        for e in self.explanations:
            kinds[e.exception_kind] = kinds.get(e.exception_kind, 0) + 1
        cited = sum(1 for e in self.explanations if e.citations)
        return {"total": len(self.explanations), "with_citation": cited, "by_kind": kinds}


def _ungrounded(txn: CanonicalTxn, exc: Exception) -> GroundedExplanation:
    """An exception we could not cite policy for — still reported, never dropped."""
    narration = str(txn.raw.get("narration", "")).strip()
    return GroundedExplanation(
        record_id=txn.txn_id,
        exception_kind=txn.status,
        summary=(
            f"{txn.currency} {txn.amount} on {txn.value_date}"
            f"{f' ({narration})' if narration else ''} has no matching invoice or "
            "gateway record."
        ),
        action=(
            "Needs manual review: the GST policy store was unavailable "
            f"({type(exc).__name__}: {exc}), so no clause could be cited. "
            "Run scripts/build_rag_index.py once with network access to restore "
            "grounded explanations."
        ),
        citations=(),
    )


def run_grounding(
    match_report: MatchReport,
    ingest: IngestResult,
    audit: AuditLogger | None = None,
    index: PolicyIndex | None = None,
) -> GroundingReport:
    if audit is None:
        audit = AuditLogger(path=None)
    index = index or PolicyIndex()
    by_id: dict[str, CanonicalTxn] = {t.txn_id: t for t in ingest.records}

    report = GroundingReport()
    for decision in match_report.in_bucket(Bucket.EXCEPTION):
        txn = by_id.get(decision.record_id)
        if txn is None:
            continue
        try:
            explanation = ground_exception(txn, index)
        except Exception as exc:  # noqa: BLE001
            # The policy store is unavailable — most likely the embedding model
            # could not be fetched on a first, offline run. The record is still an
            # exception and still needs a human; it just arrives without the
            # citation. Losing the grounding must never lose the record.
            explanation = _ungrounded(txn, exc)
        report.explanations.append(explanation)
        primary = explanation.primary
        audit.log(
            record_id=explanation.record_id,
            stage="ground",
            decision="grounded" if primary else "no-clause-found",
            confidence=round(primary.score, 4) if primary else 0.0,
            source=f"rag:{primary.doc_title}" if primary else "rag:none",
            inputs=explanation.as_audit_inputs(),
            rationale=explanation.summary + " " + explanation.action,
        )
    return report
