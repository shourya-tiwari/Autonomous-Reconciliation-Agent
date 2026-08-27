"""Component 1 — Ingest.

Read 3 transaction sources (bank statement, invoice ledger, gateway export),
normalize them onto one canonical schema, and route malformed rows to the
failure path instead of dropping them silently.
"""
