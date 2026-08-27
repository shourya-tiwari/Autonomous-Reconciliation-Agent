"""Tests for recon.ingest — normalisation of the three sources and the reject path.

These run against the committed corpus in data/synthetic/, so they also serve as
a regression check that the generator and the loaders agree on the file dialects.
"""

from __future__ import annotations

import csv
from datetime import date
from decimal import Decimal

import pytest

from config import settings
from recon.ingest import CanonicalTxn, Direction, Source, load_all
from recon.ingest.loaders import load_bank, load_gateway, load_invoice

MALFORMED_ID = "pay_MALFORMED0001"


@pytest.fixture(scope="module")
def ingested():
    return load_all()


def _csv_rows(name: str) -> list[dict[str, str]]:
    with (settings.SYNTHETIC_DIR / name).open(newline="", encoding="utf-8") as fh:
        return list(csv.DictReader(fh))


# --- nothing is silently lost ---------------------------------------------


def test_every_input_row_is_accounted_for(ingested):
    """records + rejects must equal the total number of data rows on disk."""
    on_disk = sum(
        len(_csv_rows(n))
        for n in ("gateway_export.csv", "invoice_ledger.csv", "bank_statement.csv")
    )
    assert ingested.rows_read == on_disk


def test_all_three_sources_present(ingested):
    for source in Source:
        assert ingested.by_source(source), f"no records loaded for {source}"


def test_txn_ids_unique_within_each_source(ingested):
    for source in Source:
        ids = [r.txn_id for r in ingested.by_source(source)]
        assert len(ids) == len(set(ids))


# --- the malformed row (failure mode 1) -----------------------------------


def test_malformed_row_is_rejected_not_dropped(ingested):
    assert MALFORMED_ID not in {r.txn_id for r in ingested.records}
    rejected = [r for r in ingested.rejects if r.raw.get("pg_payment_id") == MALFORMED_ID]
    assert len(rejected) == 1


def test_rejection_reason_names_every_bad_field(ingested):
    """Both problems on the row are reported, not just the first one found."""
    reject = next(r for r in ingested.rejects if r.raw.get("pg_payment_id") == MALFORMED_ID)
    assert "amount" in reject.reason
    assert "captured_at" in reject.reason
    assert reject.source is Source.GATEWAY
    assert reject.row_number > 1  # a real file line number, header excluded
    assert reject.raw["amount"] == "N/A"  # the original row is preserved verbatim


def test_corpus_has_exactly_one_malformed_row(ingested):
    assert len(ingested.rejects) == 1


# --- per-source dialect handling ------------------------------------------


def test_gateway_minor_units_converted_exactly():
    records, _ = load_gateway(settings.SYNTHETIC_DIR / "gateway_export.csv")
    by_id = {r.txn_id: r for r in records}
    raw = {r["pg_payment_id"]: r for r in _csv_rows("gateway_export.csv")}

    for txn_id, record in list(by_id.items())[:50]:
        expected = Decimal(raw[txn_id]["amount"]) / 100
        assert record.amount == expected.quantize(Decimal("0.01"))
        assert isinstance(record.amount, Decimal)  # never float


def test_gateway_fee_is_processor_fee_plus_its_tax():
    records, _ = load_gateway(settings.SYNTHETIC_DIR / "gateway_export.csv")
    raw = {r["pg_payment_id"]: r for r in _csv_rows("gateway_export.csv")}

    captured = next(r for r in records if r.fee is not None)
    source_row = raw[captured.txn_id]
    expected = (Decimal(source_row["fee"]) + Decimal(source_row["tax"])) / 100
    assert captured.fee == expected.quantize(Decimal("0.01"))


def test_gateway_failed_payments_flagged_as_no_money_moved():
    records, _ = load_gateway(settings.SYNTHETIC_DIR / "gateway_export.csv")
    failed = [r for r in records if r.status == "failed"]
    assert failed, "corpus should contain failed payments"
    assert all(not r.moved_money for r in failed)
    assert all(r.fee is None for r in failed)


def test_invoice_dates_parsed_from_iso_format():
    records, rejects = load_invoice(settings.SYNTHETIC_DIR / "invoice_ledger.csv")
    assert not rejects
    raw = {r["invoice_no"]: r for r in _csv_rows("invoice_ledger.csv")}

    for record in records[:50]:
        assert record.value_date == date.fromisoformat(raw[record.txn_id]["invoice_date"])
        assert record.direction is Direction.INFLOW
        assert record.currency == "INR"  # the ledger is INR-denominated throughout


def test_bank_debit_and_credit_map_to_direction():
    records, rejects = load_bank(settings.SYNTHETIC_DIR / "bank_statement.csv")
    assert not rejects
    raw = {r["bank_txn_ref"]: r for r in _csv_rows("bank_statement.csv")}

    for record in records:
        source_row = raw[record.txn_id]
        if source_row["credit"]:
            assert record.direction is Direction.INFLOW
            assert record.amount == Decimal(source_row["credit"]).quantize(Decimal("0.01"))
        else:
            assert record.direction is Direction.OUTFLOW
            assert record.amount == Decimal(source_row["debit"]).quantize(Decimal("0.01"))


def test_bank_dates_use_day_first_format():
    """DD-MM-YYYY must not be silently read as MM-DD-YYYY."""
    records, _ = load_bank(settings.SYNTHETIC_DIR / "bank_statement.csv")
    raw = {r["bank_txn_ref"]: r for r in _csv_rows("bank_statement.csv")}

    for record in records:
        day, month, year = raw[record.txn_id]["value_date"].split("-")
        assert record.value_date == date(int(year), int(month), int(day))


def test_bank_reference_extracted_from_narration():
    records, _ = load_bank(settings.SYNTHETIC_DIR / "bank_statement.csv")

    settlements = [r for r in records if r.status == "settlement"]
    assert settlements
    assert all(r.ref_id and r.ref_id.startswith("setl_") for r in settlements)

    charges = [r for r in records if r.status == "charge"]
    assert charges
    assert all(r.ref_id is None for r in charges)  # bank fees carry no reference


# --- the canonical contract ------------------------------------------------


def test_amounts_are_positive_magnitudes_with_direction_carrying_the_sign(ingested):
    assert all(r.amount > 0 for r in ingested.records)
    outflows = [r for r in ingested.records if r.direction is Direction.OUTFLOW]
    assert outflows
    assert all(r.signed_amount < 0 for r in outflows)


def test_raw_row_is_preserved_for_the_audit_trail(ingested):
    record = ingested.by_source(Source.GATEWAY)[0]
    assert record.raw["pg_payment_id"] == record.txn_id


def test_records_are_immutable(ingested):
    with pytest.raises(Exception):  # noqa: B017 - pydantic raises ValidationError on frozen models
        ingested.records[0].amount = Decimal(1)


def test_ingested_ids_line_up_with_ground_truth(ingested):
    """Normalised ids must be the same ids the answer key uses."""
    with (settings.GROUND_TRUTH_DIR / "matches.csv").open(newline="", encoding="utf-8") as fh:
        truth_ids = {row["record_id"] for row in csv.DictReader(fh)}

    ingested_ids = {r.txn_id for r in ingested.records}
    assert ingested_ids <= truth_ids
    # the only id in the answer key but not in the records is the rejected row
    assert truth_ids - ingested_ids == {MALFORMED_ID}


# --- validation rejects bad values ----------------------------------------


@pytest.mark.parametrize(
    ("field", "value"),
    [
        ("amount", Decimal(0)),
        ("amount", Decimal(-5)),
        ("currency", "RUPEES"),
        ("txn_id", "   "),
    ],
)
def test_invalid_field_values_are_refused(field, value):
    valid = {
        "txn_id": "pay_1",
        "source": Source.GATEWAY,
        "ref_id": "order_1",
        "amount": Decimal(100),
        "currency": "INR",
        "value_date": date(2026, 1, 1),
        "counterparty": "Acme",
        "direction": Direction.INFLOW,
        "status": "captured",
    }
    with pytest.raises(Exception):  # noqa: B017 - pydantic ValidationError
        CanonicalTxn(**{**valid, field: value})


def test_blank_reference_becomes_none():
    record = CanonicalTxn(
        txn_id="INV-1",
        source=Source.INVOICE,
        ref_id="   ",
        amount=Decimal(10),
        currency="inr",
        value_date=date(2026, 1, 1),
        counterparty="",
        direction=Direction.INFLOW,
        status="Booked",
    )
    assert record.ref_id is None
    assert record.counterparty is None
    assert record.currency == "INR"  # normalised
    assert record.status == "booked"
