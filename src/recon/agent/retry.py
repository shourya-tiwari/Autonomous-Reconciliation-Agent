"""Bounded retry with backoff for transient failures.

A reconciliation run must not die because one LLM call timed out. Transient
failures are retried a few times with exponential backoff; when the attempts are
exhausted the original exception is re-raised so the *caller* decides what to do
with that one record (the reasoning stage escalates it and carries on).

Backoff is deterministic by default (no jitter) so a demo run is reproducible.

``RetryingReasoner`` is the concrete wrapper used by the orchestrator: it adds
retry around ``GeminiReasoner.reason`` and reports each retry through a callback
so the attempt shows up in the audit trail.
"""

from __future__ import annotations

import time
from collections.abc import Callable
from dataclasses import dataclass
from typing import Any, TypeVar

from recon.reasoning.llm_client import ReasoningRequest, ReasoningResult, ReasoningTimeout

T = TypeVar("T")

# What counts as worth retrying. Anything else is a real error and propagates
# immediately — retrying a malformed request just wastes time.
TRANSIENT: tuple[type[BaseException], ...] = (ReasoningTimeout, TimeoutError, ConnectionError)


@dataclass(frozen=True)
class RetryPolicy:
    attempts: int = 3          # total attempts, not retries-after-the-first
    base_delay: float = 0.25   # seconds before the first retry
    max_delay: float = 4.0
    multiplier: float = 2.0

    def delay_for(self, attempt: int) -> float:
        """Delay before the retry that follows attempt ``n`` (1-based)."""
        return min(self.max_delay, self.base_delay * (self.multiplier ** (attempt - 1)))


DEFAULT_POLICY = RetryPolicy()

# called as on_retry(attempt, exception, delay_seconds)
RetryHook = Callable[[int, BaseException, float], None]


def call_with_retry(
    fn: Callable[..., T],
    *args: Any,
    policy: RetryPolicy = DEFAULT_POLICY,
    transient: tuple[type[BaseException], ...] = TRANSIENT,
    on_retry: RetryHook | None = None,
    sleep: Callable[[float], None] = time.sleep,
    **kwargs: Any,
) -> T:
    """Call ``fn``, retrying transient failures. Re-raises the last one on give-up."""
    last: BaseException | None = None
    for attempt in range(1, policy.attempts + 1):
        try:
            return fn(*args, **kwargs)
        except transient as exc:
            last = exc
            if attempt == policy.attempts:
                break
            delay = policy.delay_for(attempt)
            if on_retry is not None:
                on_retry(attempt, exc, delay)
            if delay:
                sleep(delay)
    assert last is not None  # only reachable after at least one failure
    raise last


# called as on_record_retry(record_id, attempt, exception, delay_seconds)
RecordRetryHook = Callable[[str, int, BaseException, float], None]


class RetryingReasoner:
    """Wraps any reasoner so ``reason()`` retries transient failures.

    The retry hook is told *which record* was being reasoned about, so the
    orchestrator can write a retry line into the audit trail against that record.
    Every other attribute (``model``, ``prompt_version``, ``stats``, …) is
    delegated to the wrapped instance, making this a drop-in for ``run_reasoning``.
    """

    def __init__(
        self,
        inner: Any,
        *,
        policy: RetryPolicy = DEFAULT_POLICY,
        on_retry: RecordRetryHook | None = None,
        sleep: Callable[[float], None] = time.sleep,
    ) -> None:
        self.inner = inner
        self.policy = policy
        self.on_retry = on_retry
        self._sleep = sleep
        self.retries: list[tuple[str, int, str]] = []  # (record_id, attempt, error)

    def reason(self, request: ReasoningRequest) -> ReasoningResult:
        record_id = request.record.txn_id

        def _hook(attempt: int, exc: BaseException, delay: float) -> None:
            self.retries.append((record_id, attempt, str(exc)))
            if self.on_retry is not None:
                self.on_retry(record_id, attempt, exc, delay)

        return call_with_retry(
            self.inner.reason,
            request,
            policy=self.policy,
            on_retry=_hook,
            sleep=self._sleep,
        )

    def __getattr__(self, name: str) -> Any:
        return getattr(self.inner, name)
