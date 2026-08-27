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
        explanation = ground_exception(txn, index)
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
