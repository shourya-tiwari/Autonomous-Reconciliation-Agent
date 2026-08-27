"""Component 2 — Deterministic Matching Layer.

Exact match (reference id / amount / date) then tolerant fuzzy match.
Emits three buckets: matched | unmatched-ambiguous | unmatched-exception.
This layer is the accuracy baseline; everything from here on is audit-logged.
"""
