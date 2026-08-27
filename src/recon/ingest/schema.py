"""Canonical transaction schema shared across all sources.

This is the single contract every downstream component depends on.
TODO (Day 1-2): define the canonical record (id, source, ref_id, amount,
currency, value_date, counterparty, raw payload) and the source->canonical
field maps for bank statement / invoice ledger / gateway export.
"""
