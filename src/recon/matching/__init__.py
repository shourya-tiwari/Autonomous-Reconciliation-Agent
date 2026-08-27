"""Component 2 — Deterministic Matching Layer.

Exact match (reference id / amount / date) then tolerant fuzzy match.
Emits four buckets: matched | unmatched-ambiguous | unmatched-exception | ignored.
This layer is the accuracy baseline; everything from here on is audit-logged.
"""

from .engine import reconcile
from .fuzzy import candidates_for, score_pair
from .types import Bucket, Candidate, MatchDecision, MatchReport, ScoreBreakdown

__all__ = [
    "Bucket",
    "Candidate",
    "MatchDecision",
    "MatchReport",
    "ScoreBreakdown",
    "candidates_for",
    "reconcile",
    "score_pair",
]
