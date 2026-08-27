"""Field parsing and the malformed-row failure path.

Nothing here silently coerces. Every parser raises ``ValueError`` with a message
naming the offending field, and ``RowErrors`` accumulates them so a bad row is
reported with *all* of its problems at once rather than just the first — that
detail is what makes the rejected row legible in the audit trail.

A row that fails validation becomes a :class:`RejectedRow` and travels on with
the run. It is never dropped: the pipeline must be able to account for every
line of every input file.
"""

from __future__ import annotations

from collections.abc import Callable
from datetime import date, datetime
from decimal import Decimal, InvalidOperation
from typing import Any, TypeVar

from pydantic import BaseModel, ConfigDict

from .schema import Source

T = TypeVar("T")


class RejectedRow(BaseModel):
    """An input row that could not be normalised, kept for the audit trail."""

    model_config = ConfigDict(frozen=True)

    source: Source
    row_number: int  # 1-based line number in the source file, header included
    reason: str
    raw: dict[str, Any]

    def __str__(self) -> str:
        return f"{self.source} line {self.row_number}: {self.reason}"


class RowErrors:
    """Collects field-level parse failures for a single row."""

    def __init__(self) -> None:
        self.messages: list[str] = []

    def catching(self, parser: Callable[..., T], *args: Any, **kwargs: Any) -> T | None:
        """Run ``parser``; on failure record the message and return ``None``."""
        try:
            return parser(*args, **kwargs)
        except ValueError as exc:
            self.messages.append(str(exc))
            return None

    def add(self, message: str) -> None:
        self.messages.append(message)

    def __bool__(self) -> bool:
        return bool(self.messages)

    @property
    def reason(self) -> str:
        return "; ".join(self.messages)


# --- field parsers ---------------------------------------------------------


def require(value: str | None, field: str) -> str:
    """A non-empty string."""
    text = (value or "").strip()
    if not text:
        raise ValueError(f"{field}: missing")
    return text


def optional(value: str | None) -> str | None:
    text = (value or "").strip()
    return text or None


def parse_decimal(value: str | None, field: str) -> Decimal:
    """A money amount already in major units."""
    text = require(value, field)
    try:
        return Decimal(text)
    except (InvalidOperation, ArithmeticError) as exc:
        raise ValueError(f"{field}: {text!r} is not a number") from exc


def parse_minor_units(value: str | None, field: str, *, per_major: int = 100) -> Decimal:
    """An integer amount in minor units (paise, cents) converted to major units.

    Kept exact: integer division by a Decimal, never float arithmetic.
    """
    text = require(value, field)
    try:
        minor = int(text)
    except ValueError as exc:
        raise ValueError(f"{field}: {text!r} is not a whole number of minor units") from exc
    return Decimal(minor) / Decimal(per_major)


def parse_optional_minor_units(
    value: str | None, field: str, *, per_major: int = 100
) -> Decimal | None:
    if not (value or "").strip():
        return None
    return parse_minor_units(value, field, per_major=per_major)


def parse_date_fmt(value: str | None, field: str, fmt: str) -> date:
    """A date in an explicit ``strptime`` format (e.g. the bank's DD-MM-YYYY)."""
    text = require(value, field)
    try:
        # DTZ007 is suppressed deliberately: these source dates carry no
        # timezone, and only the calendar day survives into the canonical record.
        return datetime.strptime(text, fmt).date()  # noqa: DTZ007
    except ValueError as exc:
        raise ValueError(f"{field}: {text!r} is not a date in format {fmt}") from exc


def parse_iso_date(value: str | None, field: str) -> date:
    """The date part of an ISO-8601 date or timestamp."""
    text = require(value, field)
    try:
        return datetime.fromisoformat(text).date()
    except ValueError as exc:
        raise ValueError(f"{field}: {text!r} is not an ISO-8601 date/timestamp") from exc
