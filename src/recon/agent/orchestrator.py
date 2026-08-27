"""Top-level orchestration loop: ingest -> match -> reason -> ground -> log.

TODO (Day 5): wire the components together; per-record state machine; hand
unresolved items to the escalation path; emit the audit trail as it goes.
"""
