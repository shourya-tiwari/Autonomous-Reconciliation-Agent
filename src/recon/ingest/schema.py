"""Canonical transaction schema — the contract every downstream stage depends on.

Ingest maps all three source files onto ``CanonicalTxn``. Matching, reasoning,
RAG and audit read only these fields; they never touch raw CSV columns.

Normalisation rules
-------------------
* ``amount`` is a **positive magnitude in major units** (rupees, dollars) — the
  gateway export's paise are divided exactly once, here. Sign is never encoded
  in the number.
* ``direction`` carries the sign instead (money in vs money out), so a debit and
  a credit of the same size compare equal on ``amount``.
* ``value_date`` is a plain ``date``. The three sources use three different date
  formats and only the calendar day is meaningful for matching.
* ``ref_id`` is the cross-source join key (the Razorpay order/settlement id),
  or ``None`` when the source row carries no usable reference.
* ``raw`` keeps the untouched source row so the audit trail can show precisely
  what a decision was made from.

``status`` deliberately keeps each source's own vocabulary rather than forcing a
shared enum — a gateway "refunded" and a bank "refund" are different facts. Use
``moved_money`` for the one cross-source question matching actually asks.
"""

from __future__ import annotations

from datetime import date
from decimal import ROUND_HALF_UP, Decimal
from enum import StrEnum
from typing import Any

from pydantic import BaseModel, ConfigDict, Field, field_validator

MONEY = Decimal("0.01")


class Source(StrEnum):
    """Which file a record came from."""

    GATEWAY = "gateway"
    INVOICE = "invoice"
    BANK = "bank"


class Direction(StrEnum):
    """Which way the money went, from the company's point of view."""

    INFLOW = "inflow"
    OUTFLOW = "outflow"


class CanonicalTxn(BaseModel):
    """One transaction record, normalised across sources."""

    model_config = ConfigDict(frozen=True, extra="forbid")

    txn_id: str
    source: Source
    ref_id: str | None
    amount: Decimal
    currency: str
    value_date: date
    counterparty: str | None
    direction: Direction
    status: str
    # What the payment processor withheld (its fee + GST on that fee), in major
    # units. Only the gateway export reports this; it is what makes a bank
    # settlement credit smaller than the payments it covers.
    fee: Decimal | None = None
    raw: dict[str, Any] = Field(default_factory=dict, repr=False)

    @field_validator("txn_id", mode="after")
    @classmethod
    def _txn_id_present(cls, v: str) -> str:
        v = v.strip()
        if not v:
            raise ValueError("txn_id: missing")
        return v

    @field_validator("ref_id", "counterparty", mode="after")
    @classmethod
    def _blank_to_none(cls, v: str | None) -> str | None:
        if v is None:
            return None
        v = v.strip()
        return v or None

    @field_validator("currency", mode="after")
    @classmethod
    def _iso_currency(cls, v: str) -> str:
        v = v.strip().upper()
        if len(v) != 3 or not v.isalpha():
            raise ValueError(f"currency: {v!r} is not an ISO-4217 code")
        return v

    @field_validator("status", mode="after")
    @classmethod
    def _normalise_status(cls, v: str) -> str:
        return v.strip().lower()

    @field_validator("amount", mode="after")
    @classmethod
    def _positive_money(cls, v: Decimal) -> Decimal:
        if v <= 0:
            raise ValueError(f"amount: must be positive, got {v}")
        return v.quantize(MONEY, rounding=ROUND_HALF_UP)

    @field_validator("fee", mode="after")
    @classmethod
    def _fee_money(cls, v: Decimal | None) -> Decimal | None:
        if v is None:
            return None
        if v < 0:
            raise ValueError(f"fee: must not be negative, got {v}")
        return v.quantize(MONEY, rounding=ROUND_HALF_UP)

    @property
    def moved_money(self) -> bool:
        """False for records where no money actually changed hands.

        Failed gateway payments are real rows in a real export, but they have no
        counterpart anywhere else and must be filtered before matching rather
        than counted as unmatched exceptions.
        """
        return self.status != "failed"

    @property
    def signed_amount(self) -> Decimal:
        """Amount with direction applied — for balance/summation checks."""
        return self.amount if self.direction is Direction.INFLOW else -self.amount

    def __str__(self) -> str:  # keeps audit-trail lines readable
        return (
            f"{self.source}:{self.txn_id} {self.direction} "
            f"{self.currency} {self.amount} on {self.value_date}"
        )
