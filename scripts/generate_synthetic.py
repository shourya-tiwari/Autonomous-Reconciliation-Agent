"""Day 1 (task 1.1) — derive the 3 reconciliation sources + ground truth.

Input : data/snapshot/payments.json  (curated Razorpay test-mode payments)
        data/snapshot/refunds.json    (optional)
Output: data/synthetic/bank_statement.csv
        data/synthetic/invoice_ledger.csv
        data/synthetic/gateway_export.csv
        data/synthetic/injection_manifest.json
        data/ground_truth/matches.csv

Each snapshot payment becomes one row in each of the three files, with
independent schemas. Deliberate discrepancies from data/synthetic/
mismatch_catalogue.md are then injected per INJECTION_PLAN. The generation is
deterministic (fixed seed) so ground truth always lines up with the outputs.

Usage
-----
    python scripts/generate_synthetic.py
    python scripts/generate_synthetic.py --seed 7 --dry-run

NOTE: the field names read from snapshot payments follow the documented Razorpay
Payments entity. If the curated snapshot differs, adjust `_load_payments`.
"""

from __future__ import annotations

import argparse
import json
import random
import sys
from datetime import UTC, datetime
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path[:0] = [str(ROOT), str(ROOT / "src")]

for _stream in (sys.stdout, sys.stderr):  # Windows consoles default to cp1252
    try:
        _stream.reconfigure(encoding="utf-8", errors="replace")
    except (AttributeError, ValueError):
        pass

from config import settings

# Fraction of eligible payments that receive each injected case (see catalogue).
INJECTION_PLAN = {
    "amount_rounding": 0.15,
    "fee_deducted": 0.20,
    "split_settlement": 0.08,   # groups of 2-4 payments
    "partial_capture": 0.05,
    "duplicate_ref": 0.04,
    "timing_offset": 0.25,
    "name_drift": 0.20,
    "missing_field": 0.05,
    "fx_rounding": 0.06,        # only applies to non-INR payments
    "unmatched_refund": 0.06,
    "unmatched_bank_fee": 0.03,
    "malformed_row": 1,         # absolute count, not a fraction
    "api_timeout": 1,           # absolute count
}

RAZORPAY_FEE_RATE = 0.02       # 2% — Razorpay standard pricing, for the fee-deduction case
GST_ON_FEE_RATE = 0.18


def _load_payments() -> list[dict]:
    path = settings.SNAPSHOT_DIR / "payments.json"
    if not path.exists():
        sys.exit(
            f"{path.relative_to(ROOT)} not found.\n"
            "Run scripts/pull_razorpay_sandbox.py with your test-mode keys, then "
            "curate a subset into data/snapshot/payments.json."
        )
    payments = json.loads(path.read_text(encoding="utf-8"))
    # normalize the few fields we depend on; keep the rest untouched
    norm = []
    for p in payments:
        norm.append(
            {
                "id": p["id"],
                "order_id": p.get("order_id") or "",
                "amount_paise": int(p["amount"]),
                "currency": p.get("currency", "INR"),
                "created_at": int(p["created_at"]),
                "fee_paise": int(p.get("fee") or 0),
                "tax_paise": int(p.get("tax") or 0),
                "email": p.get("email") or "",
                "method": p.get("method") or "",
            }
        )
    return norm


# --- schema derivations -----------------------------------------------------

def _fmt_bank_date(dt: datetime) -> str:
    return dt.strftime("%d-%m-%Y")


def _fmt_ledger_date(dt: datetime) -> str:
    return dt.strftime("%Y-%m-%d")


def _company_name(seed_email: str, rng: random.Random) -> str:
    base = (seed_email.split("@")[0] or "acme").replace(".", " ").title() or "Acme"
    return f"{base} {rng.choice(['Pvt Ltd', 'Technologies', 'Enterprises', 'LLP'])}"


def _drift_name(name: str, rng: random.Random) -> str:
    variants = [name.upper(), name.replace(" Pvt Ltd", "").upper(), name.replace(" ", "  ")]
    return rng.choice(variants)


# TODO(task 1.1 cont.): full row construction for each source + the per-case
# injectors below. Stubbed until data/snapshot/payments.json exists so the
# transforms can be checked against real field values.

def build_rows(payments: list[dict], rng: random.Random):
    raise NotImplementedError(
        "row construction pending a real snapshot — see TODO in this file"
    )


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--seed", type=int, default=42)
    parser.add_argument("--dry-run", action="store_true", help="report plan, write nothing")
    args = parser.parse_args()

    rng = random.Random(args.seed)
    payments = _load_payments()
    print(f"loaded {len(payments)} snapshot payments")

    manifest = {
        "generated_at": datetime.now(UTC).isoformat(),
        "seed": args.seed,
        "n_payments": len(payments),
        "injection_plan": INJECTION_PLAN,
    }

    if args.dry_run:
        print(json.dumps(manifest, indent=2))
        return

    build_rows(payments, rng)  # NotImplementedError until snapshot lands
    # ... write the 3 CSVs, matches.csv, injection_manifest.json


if __name__ == "__main__":
    main()
