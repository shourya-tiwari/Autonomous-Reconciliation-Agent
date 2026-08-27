"""Component 6 — Audit Trail.

Every decision (deterministic, LLM, RAG) is logged with input, decision,
confidence/source, and timestamp. This is a first-class output — judges
inspect it directly.
"""

from .logger import AuditLogger

__all__ = ["AuditLogger"]
