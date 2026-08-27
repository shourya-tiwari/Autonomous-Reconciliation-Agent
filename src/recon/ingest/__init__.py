"""Component 1 — Ingest.

Read 3 transaction sources (bank statement, invoice ledger, payment gateway
export), normalize them onto one canonical schema, and route malformed rows to
the failure path instead of dropping them silently.
"""

from .loaders import IngestResult, load_all, load_bank, load_gateway, load_invoice
from .schema import CanonicalTxn, Direction, Source
from .validate import RejectedRow

__all__ = [
    "CanonicalTxn",
    "Direction",
    "IngestResult",
    "RejectedRow",
    "Source",
    "load_all",
    "load_bank",
    "load_gateway",
    "load_invoice",
]
