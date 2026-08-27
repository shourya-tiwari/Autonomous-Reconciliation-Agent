"""Component 5 — Agent Orchestration.

Drives ingest → match → reason → ground → log, retries transient failures,
escalates anything unresolved, and demonstrates two deliberately injected
failure modes handled gracefully rather than merely claimed.
"""

from .orchestrator import FinalBucket, PipelineResult, RecordOutcome, run_pipeline
from .retry import RetryingReasoner, RetryPolicy, call_with_retry

__all__ = [
    "FinalBucket",
    "PipelineResult",
    "RecordOutcome",
    "RetryPolicy",
    "RetryingReasoner",
    "call_with_retry",
    "run_pipeline",
]
