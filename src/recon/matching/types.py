"""Types produced by the deterministic matching layer.

The layer partitions every money-moving record into one of four buckets:

``matched``               a confident 1:1 (or N:1 settlement) pairing — done
``unmatched-ambiguous``   candidates exist but none is a clear winner — send to the LLM
``unmatched-exception``   no counterpart at all — send to RAG for a grounded explanation
``ignored``               no money moved (a failed payment) — filtered before matching

A :class:`MatchDecision` records the outcome for one record, plus the scored
candidates it was chosen from, so the audit trail and the LLM stage both have
the full picture without recomputing anything.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from enum import StrEnum

from recon.ingest import CanonicalTxn


class Bucket(StrEnum):
    MATCHED = "matched"
    AMBIGUOUS = "unmatched-ambiguous"
    EXCEPTION = "unmatched-exception"
    IGNORED = "ignored"


@dataclass(frozen=True)
class ScoreBreakdown:
    """Why a pair scored the way it did — each component is 0..1 or None (n/a)."""

    total: float
    amount: float
    date: float
    name: float | None
    ref: float
    same_currency: bool

    def as_dict(self) -> dict[str, float | bool | None]:
        return {
            "total": round(self.total, 4),
            "amount": round(self.amount, 4),
            "date": round(self.date, 4),
            "name": None if self.name is None else round(self.name, 4),
            "ref": round(self.ref, 4),
            "same_currency": self.same_currency,
        }


@dataclass(frozen=True)
class Candidate:
    """A possible counterpart for a record, with its score."""

    txn_id: str
    source: str
    score: ScoreBreakdown

    def as_dict(self) -> dict:
        return {"txn_id": self.txn_id, "source": self.source, "score": self.score.as_dict()}


@dataclass(frozen=True)
class MatchDecision:
    record_id: str
    source: str
    bucket: Bucket
    method: str                       # "exact" | "fuzzy" | "settlement-group" | "filter" | "no-candidate"
    confidence: float                 # 0..1
    matched_to: tuple[str, ...] = ()
    candidates: tuple[Candidate, ...] = ()
    rationale: str = ""

    def as_audit_inputs(self) -> dict:
        return {
            "source": self.source,
            "candidates": [c.as_dict() for c in self.candidates],
        }


@dataclass
class MatchReport:
    """The full output of the matching layer for one run."""

    decisions: list[MatchDecision] = field(default_factory=list)

    def in_bucket(self, bucket: Bucket) -> list[MatchDecision]:
        return [d for d in self.decisions if d.bucket is bucket]

    def by_id(self, record_id: str) -> MatchDecision | None:
        return next((d for d in self.decisions if d.record_id == record_id), None)

    def summary(self) -> dict[str, int]:
        out = {b.value: 0 for b in Bucket}
        for d in self.decisions:
            out[d.bucket.value] += 1
        out["total"] = len(self.decisions)
        return out


# convenience alias for callers that pass the pools around
Pool = list[CanonicalTxn]
