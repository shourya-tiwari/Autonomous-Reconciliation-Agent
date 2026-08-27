"""Retry / backoff for transient failures (e.g. LLM API timeout).

TODO (Day 5): bounded retry with backoff; exhausted retries escalate, not crash.
This is where the injected API-timeout failure mode is demonstrated handled.
"""
