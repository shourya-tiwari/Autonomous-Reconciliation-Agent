"""Per-source readers: gateway export, invoice ledger, bank statement.

Each loader knows exactly one file's dialect — its column names, its amount unit
and its date format — and produces :class:`CanonicalTxn` records plus a list of
:class:`RejectedRow` for anything it could not parse. Every input line ends up in
exactly one of the two lists; ``records + rejects`` always equals the number of
data rows read.

Source dialects
---------------
gateway_export.csv   amounts in **paise/cents** (minor units), ISO-8601 timestamps
invoice_ledger.csv   amounts in **rupees**, ``YYYY-MM-DD``, always INR-denominated
bank_statement.csv   amounts in **rupees**, ``DD-MM-YYYY``, separate debit/credit
                     columns, reference buried in free-text narration
"""

from __future__ import annotations

import csv
import re
from dataclasses import dataclass, field
from pathlib import Path

from pydantic import ValidationError

from .schema import CanonicalTxn, Direction, Source
from .validate import (
    RejectedRow,
    RowErrors,
    optional,
    parse_date_fmt,
    parse_decimal,
    parse_iso_date,
    parse_minor_units,
    parse_optional_minor_units,
    require,
)

BANK_DATE_FMT = "%d-%m-%Y"

# Razorpay ids embedded in a bank narration, e.g. "RAZORPAY SETTLEMENT setl_abc123".
_RZP_ID = re.compile(r"\b((?:setl|pay|rfnd|order)_[A-Za-z0-9]+)\b")

LoadResult = tuple[list[CanonicalTxn], list[RejectedRow]]


@dataclass(frozen=True)
class IngestResult:
    """Everything read from disk: what normalised, and what refused to."""

    records: list[CanonicalTxn] = field(default_factory=list)
    rejects: list[RejectedRow] = field(default_factory=list)

    @property
    def rows_read(self) -> int:
        return len(self.records) + len(self.rejects)

    def by_source(self, source: Source) -> list[CanonicalTxn]:
        return [r for r in self.records if r.source is source]

    def summary(self) -> dict[str, int]:
        counts = {f"{s}_records": len(self.by_source(s)) for s in Source}
        return {**counts, "records": len(self.records), "rejects": len(self.rejects)}


def _read_rows(path: Path) -> list[tuple[int, dict[str, str]]]:
    """Rows paired with their 1-based file line number (line 1 is the header)."""
    with path.open(newline="", encoding="utf-8") as fh:
        return list(enumerate(csv.DictReader(fh), start=2))


def _build(
    errors: RowErrors,
    source: Source,
    line_no: int,
    row: dict[str, str],
    **fields: object,
) -> tuple[CanonicalTxn | None, RejectedRow | None]:
    """Turn parsed fields into a record, or into a reject if anything failed."""
    if errors:
        return None, RejectedRow(
            source=source, row_number=line_no, reason=errors.reason, raw=row
        )
    try:
        return CanonicalTxn(source=source, raw=row, **fields), None  # type: ignore[arg-type]
    except ValidationError as exc:
        reason = "; ".join(
            f"{'.'.join(str(p) for p in e['loc']) or 'record'}: {e['msg']}"
            for e in exc.errors()
        )
        return None, RejectedRow(source=source, row_number=line_no, reason=reason, raw=row)


# --- gateway export --------------------------------------------------------


def load_gateway(path: Path) -> LoadResult:
    records: list[CanonicalTxn] = []
    rejects: list[RejectedRow] = []

    for line_no, row in _read_rows(path):
        err = RowErrors()
        txn_id = err.catching(require, row.get("pg_payment_id"), "pg_payment_id")
        amount = err.catching(parse_minor_units, row.get("amount"), "amount")
        value_date = err.catching(parse_iso_date, row.get("captured_at"), "captured_at")
        currency = err.catching(require, row.get("currency"), "currency")
        status = err.catching(require, row.get("status"), "status")
        # fee and tax are both withheld by the processor; downstream only cares
        # about the total, so they are summed into one canonical field.
        fee = err.catching(parse_optional_minor_units, row.get("fee"), "fee")
        tax = err.catching(parse_optional_minor_units, row.get("tax"), "tax")
        total_fee = None if fee is None and tax is None else (fee or 0) + (tax or 0)

        record, reject = _build(
            err,
            Source.GATEWAY,
            line_no,
            row,
            txn_id=txn_id,
            ref_id=optional(row.get("pg_order_id")),
            amount=amount,
            currency=currency,
            value_date=value_date,
            counterparty=optional(row.get("customer_name")),
            direction=Direction.INFLOW,  # a payment is always money coming in
            status=status,
            fee=total_fee,
        )
        (records if record else rejects).append(record or reject)  # type: ignore[arg-type]

    return records, rejects


# --- invoice ledger --------------------------------------------------------


def load_invoice(path: Path) -> LoadResult:
    records: list[CanonicalTxn] = []
    rejects: list[RejectedRow] = []

    for line_no, row in _read_rows(path):
        err = RowErrors()
        txn_id = err.catching(require, row.get("invoice_no"), "invoice_no")
        amount = err.catching(parse_decimal, row.get("gross_amount"), "gross_amount")
        value_date = err.catching(parse_date_fmt, row.get("invoice_date"), "invoice_date", "%Y-%m-%d")
        currency = err.catching(require, row.get("currency"), "currency")

        record, reject = _build(
            err,
            Source.INVOICE,
            line_no,
            row,
            txn_id=txn_id,
            ref_id=optional(row.get("pg_reference")),
            amount=amount,
            currency=currency,
            value_date=value_date,
            counterparty=optional(row.get("party_name")),
            direction=Direction.INFLOW,  # a sales invoice is a receivable
            status="booked",
        )
        (records if record else rejects).append(record or reject)  # type: ignore[arg-type]

    return records, rejects


# --- bank statement --------------------------------------------------------


def _bank_status(narration: str) -> str:
    upper = narration.upper()
    if "SETTLEMENT" in upper:
        return "settlement"
    if "REFUND" in upper:
        return "refund"
    return "charge"


def _bank_counterparty(narration: str) -> str | None:
    """First words of the narration, before any embedded reference id."""
    return _RZP_ID.sub("", narration).strip() or None


def load_bank(path: Path) -> LoadResult:
    records: list[CanonicalTxn] = []
    rejects: list[RejectedRow] = []

    for line_no, row in _read_rows(path):
        err = RowErrors()
        txn_id = err.catching(require, row.get("bank_txn_ref"), "bank_txn_ref")
        value_date = err.catching(parse_date_fmt, row.get("value_date"), "value_date", BANK_DATE_FMT)
        narration = (row.get("narration") or "").strip()

        debit = (row.get("debit") or "").strip()
        credit = (row.get("credit") or "").strip()
        amount = None
        direction = None
        if bool(debit) == bool(credit):
            err.add("debit/credit: exactly one of the two must be set")
        elif debit:
            amount = err.catching(parse_decimal, debit, "debit")
            direction = Direction.OUTFLOW
        else:
            amount = err.catching(parse_decimal, credit, "credit")
            direction = Direction.INFLOW

        found = _RZP_ID.search(narration)

        record, reject = _build(
            err,
            Source.BANK,
            line_no,
            row,
            txn_id=txn_id,
            ref_id=found.group(1) if found else None,
            amount=amount,
            currency="INR",  # the account is INR-denominated
            value_date=value_date,
            counterparty=_bank_counterparty(narration),
            direction=direction,
            status=_bank_status(narration),
        )
        (records if record else rejects).append(record or reject)  # type: ignore[arg-type]

    return records, rejects


# --- orchestration ---------------------------------------------------------

LOADERS = {
    "gateway_export.csv": load_gateway,
    "invoice_ledger.csv": load_invoice,
    "bank_statement.csv": load_bank,
}


def load_all(data_dir: Path | None = None) -> IngestResult:
    """Load all three sources into one normalised list plus the reject list."""
    from config import settings

    directory = Path(data_dir) if data_dir else settings.SYNTHETIC_DIR
    records: list[CanonicalTxn] = []
    rejects: list[RejectedRow] = []

    for filename, loader in LOADERS.items():
        path = directory / filename
        if not path.exists():
            raise FileNotFoundError(
                f"{path} not found — run scripts/generate_synthetic.py first."
            )
        loaded, bad = loader(path)
        records.extend(loaded)
        rejects.extend(bad)

    return IngestResult(records=records, rejects=rejects)
