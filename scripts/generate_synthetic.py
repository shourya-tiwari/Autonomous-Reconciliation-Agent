"""Day 1 (task 1.1) — build the evaluation corpus: 3 reconciliation sources + ground truth.

Pipeline of this script
-----------------------
1. load the 8 real payments from data/snapshot/ (schema anchor)
2. generate synthetic Razorpay-shaped payments up to --target, with distributions
   calibrated from the real pull (see CALIBRATION below)
3. synthesize orders, refunds and T+2 settlement batches
4. derive three source files with independent schemas:
     data/synthetic/gateway_export.csv   (Razorpay's view, payment-level, paise)
     data/synthetic/invoice_ledger.csv   (finance's view, invoice-level, rupees)
     data/synthetic/bank_statement.csv   (bank account view, settlement-level, rupees)
5. inject the discrepancies from data/synthetic/mismatch_catalogue.md
6. write data/ground_truth/matches.csv  and  data/synthetic/injection_manifest.json

Deterministic: fixed --seed, sorted iteration, so ground truth always lines up.

Usage
-----
    python scripts/generate_synthetic.py                 # 300 records, seed 42
    python scripts/generate_synthetic.py --target 300 --seed 42
    python scripts/generate_synthetic.py --dry-run       # print the plan, write nothing
"""

from __future__ import annotations

import argparse
import csv
import json
import random
import string
import sys
from datetime import UTC, date, datetime, timedelta
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path[:0] = [str(ROOT), str(ROOT / "src")]

for _stream in (sys.stdout, sys.stderr):  # Windows consoles default to cp1252
    try:
        _stream.reconfigure(encoding="utf-8", errors="replace")
    except (AttributeError, ValueError):
        pass

from config import settings

# --- CALIBRATION (from data/snapshot/, 8 real payments) ---------------------
# observed: fee ~= 2.6% of amount; GST on fee ~= 18% (services standard rate).
# The BANK statement deliberately uses a slightly different fee model (catalogue #2).
PG_FEE_RATE = 0.026
GST_RATE = 0.18
STATUS_WEIGHTS = {"captured": 0.72, "failed": 0.13, "refunded": 0.15}
METHOD_WEIGHTS = {"card": 0.40, "upi": 0.30, "netbanking": 0.20, "wallet": 0.10}
CURRENCY_WEIGHTS = {"INR": 0.85, "USD": 0.10, "EUR": 0.05}
FX_TO_INR = {"USD": 83.2, "EUR": 90.5}          # "true" rate used to build the invoice
BANK_FX_SLIP = {"USD": 82.6, "EUR": 91.4}       # rate the bank actually applied (catalogue #9)
WINDOW_DAYS = 90
SETTLE_LAG_DAYS = 2                              # Razorpay settles T+2

# Fraction of eligible clean records that receive each injected case; the last
# two are absolute counts. Echoed into injection_manifest.json every run.
INJECTION_PLAN = {
    "amount_rounding": 0.15,
    "timing_offset": 0.25,
    "name_drift": 0.20,
    "missing_pg_ref": 0.06,
    "partial_capture": 0.05,
    "duplicate_ref": 0.03,
    "fx_rounding": 1.00,      # every non-INR row (the FX slip is unavoidable)
    "malformed_row": 1,
    "api_timeout": 1,
}

_ALNUM = string.ascii_letters + string.digits
FIRST_NAMES = ["Aarav", "Diya", "Vihaan", "Ananya", "Kabir", "Ishani", "Reyansh", "Myra",
               "Arjun", "Sara", "Vivaan", "Aadhya", "Rohan", "Kiara", "Dev", "Anika"]
LAST_NAMES = ["Sharma", "Patel", "Reddy", "Nair", "Iyer", "Singh", "Gupta", "Mehta",
              "Rao", "Bose", "Kapoor", "Chauhan", "Das", "Jain"]
SUFFIXES = ["Pvt Ltd", "Technologies", "Enterprises", "LLP", "Industries", "Solutions"]
STATE_CODES = ["27", "29", "07", "33", "36", "19", "24", "06"]


# --- id + party helpers ----------------------------------------------------

def _rid(prefix: str, rng: random.Random) -> str:
    return f"{prefix}_{''.join(rng.choices(_ALNUM, k=14))}"


def _gstin(rng: random.Random) -> str:
    pan = (
        "".join(rng.choices(string.ascii_uppercase, k=5))
        + "".join(rng.choices(string.digits, k=4))
        + rng.choice(string.ascii_uppercase)
    )
    return f"{rng.choice(STATE_CODES)}{pan}1Z{rng.choice(string.digits)}"


def _company(rng: random.Random) -> str:
    return f"{rng.choice(FIRST_NAMES)} {rng.choice(LAST_NAMES)} {rng.choice(SUFFIXES)}"


def _weighted(weights: dict[str, float], rng: random.Random) -> str:
    keys = sorted(weights)
    return rng.choices(keys, weights=[weights[k] for k in keys])[0]


def _amount_minor(currency: str, rng: random.Random) -> int:
    """A plausible transaction amount in the currency's minor unit (paise / cents)."""
    if currency == "INR":
        bucket = rng.choices(
            ["s", "m", "l", "xl"], weights=[0.50, 0.35, 0.13, 0.02]
        )[0]
        lo, hi = {
            "s": (50_000, 500_000),
            "m": (500_000, 5_000_000),
            "l": (5_000_000, 50_000_000),
            "xl": (50_000_000, 200_000_000),
        }[bucket]
        return rng.randrange(lo, hi, 100)
    return rng.randrange(1_000, 500_000, 100)  # $10 – $5000


# --- step 2/3: base transaction generation --------------------------------

def _load_real_payments() -> list[dict]:
    path = settings.SNAPSHOT_DIR / "payments.json"
    if not path.exists():
        sys.exit(
            f"{path.relative_to(ROOT)} not found.\n"
            "Run scripts/pull_razorpay_sandbox.py, then curate data/snapshot/."
        )
    return json.loads(path.read_text(encoding="utf-8"))


def _synth_payment(rng: random.Random, now: int) -> dict:
    currency = _weighted(CURRENCY_WEIGHTS, rng)
    status = _weighted(STATUS_WEIGHTS, rng)
    amount = _amount_minor(currency, rng)
    created = now - rng.randrange(0, WINDOW_DAYS * 86_400)
    settled = status in ("captured", "refunded")
    fee = round(amount * PG_FEE_RATE) if settled else None
    tax = round(fee * GST_RATE) if fee is not None else None
    return {
        "id": _rid("pay", rng),
        "entity": "payment",
        "amount": amount,
        "currency": currency,
        "status": status,
        "order_id": _rid("order", rng),
        "international": currency != "INR",
        "method": _weighted(METHOD_WEIGHTS, rng),
        "amount_refunded": amount if status == "refunded" else 0,
        "captured": status in ("captured", "refunded"),
        "description": "",
        "email": f"{rng.choice(FIRST_NAMES).lower()}@example.com",
        "contact": f"+9198{rng.randrange(10**7, 10**8)}",
        "fee": fee,
        "tax": tax,
        "created_at": created,
        "_synthetic": True,
    }


def build_base_transactions(rng: random.Random, target: int) -> dict:
    now = int(datetime.now(UTC).timestamp())
    real = _load_real_payments()
    for p in real:
        p["_synthetic"] = False
    payments = list(real) + [_synth_payment(rng, now) for _ in range(max(0, target - len(real)))]
    payments.sort(key=lambda p: p["created_at"])

    orders, refunds = [], []
    for p in payments:
        orders.append(
            {
                "id": p["order_id"] or _rid("order", rng),
                "entity": "order",
                "amount": p["amount"],
                "currency": p["currency"],
                "status": "paid" if p["captured"] else "attempted",
                "created_at": p["created_at"] - rng.randrange(60, 3600),
            }
        )
        if p["status"] == "refunded":
            refunds.append(
                {
                    "id": _rid("rfnd", rng),
                    "entity": "refund",
                    "payment_id": p["id"],
                    "amount": p["amount"],
                    "currency": p["currency"],
                    "status": "processed",
                    "created_at": p["created_at"] + rng.randrange(86_400, 5 * 86_400),
                }
            )

    return {"payments": payments, "orders": orders, "refunds": refunds}


# --- step 4: derive the three source files (clean) ------------------------

def _iso(ts: int) -> str:
    return datetime.fromtimestamp(ts, UTC).isoformat()


def derive_sources(txns: dict, rng: random.Random):
    """Return (gateway_rows, invoice_rows, parties) — all still clean.

    The bank statement is derived later (``derive_bank``) from the *injected*
    gateway rows, so a settlement always reflects what was really captured.
    """
    payments = txns["payments"]
    parties = {p["id"]: (_company(rng), _gstin(rng)) for p in sorted(payments, key=lambda p: p["id"])}

    gateway_rows, invoice_rows = [], []
    inv_seq = 0
    for p in payments:
        gateway_rows.append(
            {
                "pg_payment_id": p["id"],
                "pg_order_id": p["order_id"],
                "captured_at": _iso(p["created_at"]),
                "amount": p["amount"],           # minor units, native currency
                "currency": p["currency"],
                "method": p["method"],
                "status": p["status"],
                "customer_name": parties[p["id"]][0],   # clean; invoice side gets name-drifted
                "fee": p["fee"] if p["fee"] is not None else "",
                "tax": p["tax"] if p["tax"] is not None else "",
                "_settles": p["currency"] == "INR" and p["captured"],
            }
        )
        if not p["captured"]:
            continue  # failed payments never reach an invoice
        inv_seq += 1
        name, gstin = parties[p["id"]]
        # The ledger is kept in the company's functional currency (INR). A foreign
        # payment is booked at the "true" rate; the bank later settles it at a
        # slightly different one, which is the FX discrepancy (catalogue #7).
        if p["currency"] == "INR":
            gross = round(p["amount"] / 100, 2)
        else:
            gross = round(p["amount"] / 100 * FX_TO_INR[p["currency"]], 2)
        tax_component = round(gross - gross / (1 + GST_RATE), 2)
        invoice_rows.append(
            {
                "invoice_no": f"INV-2026-{inv_seq:05d}",
                "invoice_date": datetime.fromtimestamp(p["created_at"], UTC).strftime("%Y-%m-%d"),
                "party_name": name,
                "party_gstin": gstin,
                "gross_amount": gross,
                "tax_amount": tax_component,
                "net_amount": round(gross - tax_component, 2),
                "currency": "INR",              # always: the ledger is INR-denominated
                "pg_reference": p["order_id"],
                "_pay_id": p["id"],             # dropped before writing; used for ground truth
                "_src_currency": p["currency"],  # what the gateway actually charged in
            }
        )

    return gateway_rows, invoice_rows, parties


def _paise(gw_row: dict) -> int | None:
    try:
        return int(gw_row["amount"])
    except (TypeError, ValueError):
        return None  # the malformed row


def derive_bank(gateway_rows: list[dict], refunds: list[dict], rng: random.Random):
    """Build the bank statement from the *post-injection* gateway rows.

    Settlements batch the INR payments that actually settled (``_settles`` and not
    held back by a gateway-side anomaly), T+2, net of processor fees — so the
    credit always equals the sum of its members. Returns
    ``(bank_rows, settlement_groups)``.
    """
    batches: dict[str, list[dict]] = {}
    for g in gateway_rows:
        if not g.get("_settles") or g.get("_held"):
            continue
        paise = _paise(g)
        if paise is None:
            continue
        settle_day = date.fromisoformat(g["captured_at"][:10]) + timedelta(days=SETTLE_LAG_DAYS)
        batches.setdefault(settle_day.isoformat(), []).append(g)

    settlements = []
    for day in sorted(batches):
        group = batches[day]
        gross = sum(_paise(g) for g in group)
        fees = sum(int(g["fee"] or 0) + int(g["tax"] or 0) for g in group)
        settlements.append(
            {
                "id": _rid("setl", rng),
                "amount_paise": gross - fees,
                "fees_paise": fees,
                "settle_ts": int(datetime.fromisoformat(day).replace(tzinfo=UTC).timestamp()),
                "payment_ids": sorted(g["pg_payment_id"] for g in group),
            }
        )

    events: list[tuple[int, str, dict]] = [(s["settle_ts"], "settlement", s) for s in settlements]
    for r in refunds:
        events.append((r["created_at"], "refund", r))
    now = int(datetime.now(UTC).timestamp())
    for _ in range(5):  # non-Razorpay bank charges with no counterpart anywhere
        ts = now - rng.randrange(0, WINDOW_DAYS * 86_400)
        events.append((ts, "charge", {"amount": rng.choice([59000, 17700, 82600, 23600])}))

    rows: list[dict] = []
    balance = 5_000_000.0
    for ts, kind, obj in sorted(events, key=lambda e: e[0]):
        dt = datetime.fromtimestamp(ts, UTC)
        row = {
            "txn_date": dt.strftime("%d-%m-%Y"),
            "value_date": dt.strftime("%d-%m-%Y"),
            "ref_no": f"UTR{rng.randrange(10**11, 10**12)}",
            "debit": "",
            "credit": "",
        }
        if kind == "settlement":
            row["narration"] = f"RAZORPAY SETTLEMENT {obj['id']}"
            row["credit"] = round(obj["amount_paise"] / 100, 2)
            row["_match"] = obj["id"]      # N:1; members in settlement_groups.csv
            row["_bucket"] = "auto_resolved"
            row["_case"] = "clean"
        elif kind == "refund":
            row["narration"] = f"RAZORPAY REFUND {obj['payment_id']}"
            row["debit"] = round(obj["amount"] / 100, 2)
            # Deliberately no true_match_id: a refund debit has no invoice
            # counterpart, so it is explained (RAG / credit-note rule), not matched.
            row["_match"] = ""
            row["_bucket"] = "exception"
            row["_case"] = "unmatched_refund"
        else:  # charge
            row["narration"] = rng.choice(
                ["ACCT MAINTENANCE CHARGE", "NEFT OUTWARD CHARGE", "GST ON BANK CHARGES", "CHQ RETURN CHARGE"]
            )
            row["debit"] = round(obj["amount"] / 100, 2)
            row["_match"] = ""
            row["_bucket"] = "exception"      # no counterpart -> RAG (expense / ITC treatment)
            row["_case"] = "unmatched_bank_fee"

        balance += (row["credit"] or 0) - (row["debit"] or 0)
        row["balance"] = round(balance, 2)
        row["bank_txn_ref"] = row["ref_no"]
        rows.append(row)

    groups = [
        {
            "settlement_id": s["id"],
            "payment_id": pid,
            "settlement_amount": round(s["amount_paise"] / 100, 2),
            "fees_deducted": round(s["fees_paise"] / 100, 2),
        }
        for s in settlements
        for pid in s["payment_ids"]
    ]
    return rows, groups


# --- step 5: inject discrepancies ----------------------------------------

def inject(gateway_rows, invoice_rows, rng: random.Random) -> dict:
    """Mutate the gateway/invoice row lists in place. Runs before the bank
    statement is derived, so gateway-side anomalies flow through to settlements.
    Returns the injection log + the api-timeout target record id."""
    log: dict[str, list] = {k: [] for k in INJECTION_PLAN}
    # foreign-currency invoices already carry the FX discrepancy; keep them out
    # of the pool so a row never gets two overlapping cases.
    clean_invoices = [r for r in invoice_rows if r["_src_currency"] == "INR"]
    rng.shuffle(clean_invoices)

    cursor = 0

    def slice_n(frac):
        """Take the next `frac` share of the shuffled clean-invoice pool (no overlap)."""
        nonlocal cursor
        n = int(len(clean_invoices) * frac)
        chunk = clean_invoices[cursor:cursor + n]
        cursor += n
        return chunk

    for inv in slice_n(INJECTION_PLAN["amount_rounding"]):
        inv["gross_amount"] = float(round(inv["gross_amount"]))  # bank rounds to nearest rupee
        inv["_case"] = "amount_rounding"
        log["amount_rounding"].append(inv["invoice_no"])

    for inv in slice_n(INJECTION_PLAN["timing_offset"]):
        d = date.fromisoformat(inv["invoice_date"]) + timedelta(days=rng.randint(1, 3))
        inv["invoice_date"] = d.isoformat()
        inv["_case"] = "timing_offset"
        log["timing_offset"].append(inv["invoice_no"])

    for inv in slice_n(INJECTION_PLAN["name_drift"]):
        inv["party_name"] = inv["party_name"].upper().replace(" PVT LTD", "")
        inv["_case"] = "name_drift"
        log["name_drift"].append(inv["invoice_no"])

    for inv in slice_n(INJECTION_PLAN["missing_pg_ref"]):
        inv["pg_reference"] = ""
        inv["_case"] = "missing_pg_ref"
        log["missing_pg_ref"].append(inv["invoice_no"])

    for inv in slice_n(INJECTION_PLAN["partial_capture"]):
        gw = next(g for g in gateway_rows if g["pg_payment_id"] == inv["_pay_id"])
        gw["amount"] = int(gw["amount"] * rng.uniform(0.4, 0.8))  # captured less than invoiced
        gw["_held"] = True  # a partial capture is held back from settlement pending review
        inv["_case"] = "partial_capture"
        inv["_bucket"] = "escalated"
        log["partial_capture"].append(inv["invoice_no"])

    for inv in slice_n(INJECTION_PLAN["duplicate_ref"]):
        gw = next(g for g in gateway_rows if g["pg_payment_id"] == inv["_pay_id"])
        gw["_held"] = True  # the disputed pair is held back from settlement too
        dupe = dict(gw)
        dupe["pg_payment_id"] = _rid("pay", rng)
        gateway_rows.append(dupe)  # two gateway rows, same order id
        inv["_case"] = "duplicate_ref"
        inv["_bucket"] = "escalated"
        log["duplicate_ref"].append(inv["invoice_no"])

    # FX slip: every foreign-currency invoice was booked at FX_TO_INR; the bank
    # settled it at BANK_FX_SLIP, so invoice INR != gateway amount x settled rate.
    for inv in invoice_rows:
        if inv["_src_currency"] != "INR":
            inv["_case"] = "fx_rounding"
            inv["_bucket"] = "escalated"
            log["fx_rounding"].append(inv["invoice_no"])

    # failure mode 1 — malformed gateway row
    gateway_rows.append(
        {
            "pg_payment_id": "pay_MALFORMED0001",
            "pg_order_id": "order_MALFORMED01",
            "captured_at": "2026-13-45T99:99:99",
            "amount": "N/A",
            "currency": "INR",
            "method": "card",
            "status": "captured",
            "customer_name": "",
            "fee": "",
            "tax": "",
            "_settles": False,
        }
    )
    log["malformed_row"].append("pay_MALFORMED0001")

    # failure mode 2 — one ambiguous record the llm_client will time out on (once)
    timeout_target = ""
    ambiguous = log["partial_capture"] or log["duplicate_ref"]
    if ambiguous:
        tgt_inv = _invoice_by_no(invoice_rows, ambiguous[0])
        timeout_target = tgt_inv["_pay_id"] if tgt_inv else ""
        log["api_timeout"].append(timeout_target)

    return {"log": log, "api_timeout_record_id": timeout_target}


def _invoice_by_no(invoice_rows, invoice_no):
    for r in invoice_rows:
        if r["invoice_no"] == invoice_no:
            return r
    return None


# --- step 6: ground truth + writing ------------------------------------

def build_ground_truth(gateway_rows, invoice_rows, bank_rows) -> list[dict]:
    gt: list[dict] = []
    inv_by_pay = {r["_pay_id"]: r for r in invoice_rows}

    for g in gateway_rows:
        pid = g["pg_payment_id"]
        if pid == "pay_MALFORMED0001":
            gt.append(_gt(pid, "gateway", "", "failed", "malformed_row"))
            continue
        inv = inv_by_pay.get(pid)
        if g["status"] == "failed":
            # no money moved -> the pipeline should filter these before matching
            gt.append(_gt(pid, "gateway", "", "ignored", "failed_payment"))
        elif inv is not None:
            gt.append(_gt(pid, "gateway", inv["invoice_no"],
                          inv.get("_bucket", "auto_resolved"), inv.get("_case", "clean")))
        else:
            gt.append(_gt(pid, "gateway", "", "escalated", "duplicate_ref"))

    for inv in invoice_rows:
        gt.append(_gt(inv["invoice_no"], "invoice", inv["_pay_id"],
                      inv.get("_bucket", "auto_resolved"), inv.get("_case", "clean")))

    for b in bank_rows:
        gt.append(_gt(b["bank_txn_ref"], "bank", b["_match"], b["_bucket"], b["_case"]))
    return gt


def _gt(record_id, source, true_match_id, bucket, case) -> dict:
    return {
        "record_id": record_id,
        "source": source,
        "true_match_id": true_match_id,
        "expected_bucket": bucket,
        "case": case,
    }


def _write_csv(path: Path, rows: list[dict], drop_private=True):
    if not rows:
        return
    fields = [k for k in rows[0] if not (drop_private and k.startswith("_"))]
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("w", newline="", encoding="utf-8") as fh:
        w = csv.DictWriter(fh, fieldnames=fields, extrasaction="ignore")
        w.writeheader()
        w.writerows(rows)


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--target", type=int, default=300, help="evaluation corpus size (default 300)")
    parser.add_argument("--seed", type=int, default=42)
    parser.add_argument("--dry-run", action="store_true", help="print the plan, write nothing")
    args = parser.parse_args()

    rng = random.Random(args.seed)
    txns = build_base_transactions(rng, args.target)
    n = {k: len(v) for k, v in txns.items()}
    print(f"base transactions: {n}")

    gateway_rows, invoice_rows, _ = derive_sources(txns, rng)
    injection = inject(gateway_rows, invoice_rows, rng)
    bank_rows, settlement_groups = derive_bank(gateway_rows, txns["refunds"], rng)
    ground_truth = build_ground_truth(gateway_rows, invoice_rows, bank_rows)

    manifest = {
        "generated_at": datetime.now(UTC).isoformat(),
        "seed": args.seed,
        "target": args.target,
        "base_counts": n,
        "rows": {
            "gateway_export": len(gateway_rows),
            "invoice_ledger": len(invoice_rows),
            "bank_statement": len(bank_rows),
            "ground_truth": len(ground_truth),
            "settlement_groups": len(settlement_groups),
        },
        "injection_plan": INJECTION_PLAN,
        "injection_log": {k: len(v) for k, v in injection["log"].items()},
        "api_timeout_record_id": injection["api_timeout_record_id"],
    }

    if args.dry_run:
        print(json.dumps(manifest, indent=2))
        return

    _write_csv(settings.SYNTHETIC_DIR / "gateway_export.csv", gateway_rows)
    _write_csv(settings.SYNTHETIC_DIR / "invoice_ledger.csv", invoice_rows)
    _write_csv(settings.SYNTHETIC_DIR / "bank_statement.csv", bank_rows)
    _write_csv(settings.GROUND_TRUTH_DIR / "matches.csv", ground_truth, drop_private=False)
    _write_csv(settings.GROUND_TRUTH_DIR / "settlement_groups.csv", settlement_groups, drop_private=False)
    (settings.SYNTHETIC_DIR / "base_transactions.json").write_text(
        json.dumps(txns, indent=2), encoding="utf-8"
    )
    (settings.SYNTHETIC_DIR / "injection_manifest.json").write_text(
        json.dumps({**manifest, "injection_log": injection["log"]}, indent=2), encoding="utf-8"
    )
    print(json.dumps(manifest, indent=2))
    print("\nwrote data/synthetic/*.csv, data/ground_truth/matches.csv")


if __name__ == "__main__":
    main()
