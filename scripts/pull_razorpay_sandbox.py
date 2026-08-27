"""Day 1 (task 1.1) — pull real transactions from the Razorpay test-mode API.

Writes raw, unmodified API responses to ``data/raw/`` plus a ``manifest.json``
recording exactly what was pulled (timestamp, key mode, date range, per-resource
counts). A curated subset is then copied by hand into ``data/snapshot/`` and
committed — that snapshot is what the pipeline runs on from a clean clone.

Usage
-----
    # keys in .env (RAZORPAY_KEY_ID / RAZORPAY_KEY_SECRET), must be rzp_test_*
    python scripts/pull_razorpay_sandbox.py
    python scripts/pull_razorpay_sandbox.py --days 90 --max 500
    python scripts/pull_razorpay_sandbox.py --resources payments,refunds

Notes
-----
* Test-mode accounts start empty. Create some test payments/refunds first
  (Dashboard in test mode, or the API) so there is data to pull.
* This script never writes to ``data/snapshot/`` — curation is a manual step so
  we control exactly what lands in the committed dataset.
"""

from __future__ import annotations

import argparse
import json
import sys
import time
from datetime import datetime, timedelta, timezone
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path[:0] = [str(ROOT), str(ROOT / "src")]

for _stream in (sys.stdout, sys.stderr):  # Windows consoles default to cp1252
    try:
        _stream.reconfigure(encoding="utf-8", errors="replace")
    except (AttributeError, ValueError):
        pass

from config import settings  # noqa: E402

PAGE_SIZE = 100  # Razorpay hard max per request

# resource name -> attribute path on the razorpay client (``client.<attr>.all``)
RESOURCES = {
    "payments": "payment",
    "orders": "order",
    "refunds": "refund",
    "settlements": "settlement",
}


def _load_env() -> tuple[str, str]:
    try:
        from dotenv import load_dotenv
    except ImportError:  # pragma: no cover - dotenv is in requirements
        load_dotenv = None
    if load_dotenv:
        load_dotenv(ROOT / ".env")

    import os

    key_id = os.environ.get("RAZORPAY_KEY_ID", "").strip()
    key_secret = os.environ.get("RAZORPAY_KEY_SECRET", "").strip()
    if not key_id or not key_secret:
        sys.exit(
            "RAZORPAY_KEY_ID / RAZORPAY_KEY_SECRET not set. Copy .env.example to "
            ".env and fill in your test-mode keys."
        )
    if not key_id.startswith("rzp_test_"):
        sys.exit(
            f"Refusing to run: key id {key_id!r} is not a test-mode key "
            "(expected rzp_test_...). This project only uses sandbox data."
        )
    return key_id, key_secret


def _fetch_all(entity, window: dict, max_records: int) -> list[dict]:
    """Page through ``entity.all()`` until exhausted or ``max_records`` hit."""
    out: list[dict] = []
    skip = 0
    while len(out) < max_records:
        options = {"count": min(PAGE_SIZE, max_records - len(out)), "skip": skip, **window}
        resp = entity.all(options)
        items = resp.get("items", [])
        out.extend(items)
        if len(items) < options["count"]:
            break
        skip += len(items)
        time.sleep(0.2)  # be gentle with the API
    return out


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--days", type=int, default=365, help="how far back to pull (default 365)")
    parser.add_argument("--max", type=int, default=1000, help="max records per resource (default 1000)")
    parser.add_argument(
        "--resources",
        default=",".join(RESOURCES),
        help=f"comma-separated subset of {list(RESOURCES)}",
    )
    args = parser.parse_args()

    key_id, key_secret = _load_env()

    try:
        import razorpay
    except ImportError:
        sys.exit("razorpay SDK not installed — run: pip install -r requirements.txt")

    client = razorpay.Client(auth=(key_id, key_secret))

    now = datetime.now(timezone.utc)
    window = {
        "from": int((now - timedelta(days=args.days)).timestamp()),
        "to": int(now.timestamp()),
    }

    wanted = [r.strip() for r in args.resources.split(",") if r.strip()]
    unknown = [r for r in wanted if r not in RESOURCES]
    if unknown:
        sys.exit(f"unknown resource(s): {unknown}. choose from {list(RESOURCES)}")

    settings.RAW_DIR.mkdir(parents=True, exist_ok=True)
    stamp = now.strftime("%Y%m%dT%H%M%SZ")
    counts: dict[str, int] = {}

    for name in wanted:
        entity = getattr(client, RESOURCES[name])
        print(f"pulling {name} ...", flush=True)
        try:
            records = _fetch_all(entity, window, args.max)
        except Exception as exc:  # noqa: BLE001 - surface any API error, keep going
            print(f"  ! {name} failed: {exc}", file=sys.stderr)
            counts[name] = -1
            continue
        path = settings.RAW_DIR / f"{name}_{stamp}.json"
        path.write_text(json.dumps(records, indent=2), encoding="utf-8")
        counts[name] = len(records)
        print(f"  {len(records)} records -> {path.relative_to(ROOT)}")

    manifest = {
        "pulled_at": now.isoformat(),
        "key_mode": "test",
        "key_id": key_id,
        "window": {
            "from": datetime.fromtimestamp(window["from"], timezone.utc).isoformat(),
            "to": datetime.fromtimestamp(window["to"], timezone.utc).isoformat(),
            "days": args.days,
        },
        "max_per_resource": args.max,
        "counts": counts,
    }
    manifest_path = settings.RAW_DIR / f"manifest_{stamp}.json"
    manifest_path.write_text(json.dumps(manifest, indent=2), encoding="utf-8")
    print(f"\nmanifest -> {manifest_path.relative_to(ROOT)}")

    if all(c <= 0 for c in counts.values()):
        print(
            "\nNo records pulled. Create test-mode payments/refunds first, then "
            "re-run. See the Dashboard in test mode or the Razorpay API docs.",
            file=sys.stderr,
        )


if __name__ == "__main__":
    main()
