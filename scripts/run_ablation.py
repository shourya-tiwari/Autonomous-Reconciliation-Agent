"""Task 3.3 — run the layer ablation and write the report.

Runs the pipeline four times over the same corpus, removing one layer at a time,
and scores every variant with the same function used for the headline metrics.
Answers "what does each layer actually buy?" with a measurement instead of an
assertion.

Offline and deterministic, like the main entrypoint: no API key, no network
beyond the one-off embedding-model load the final variant needs.

Usage
-----
    python scripts/run_ablation.py
    python scripts/run_ablation.py --data data/synthetic

Outputs
-------
    outputs/reports/ablation.json   the numbers
    outputs/reports/ablation.md     the write-up (committed)
"""

from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path[:0] = [str(ROOT), str(ROOT / "src")]

for _stream in (sys.stdout, sys.stderr):
    try:
        _stream.reconfigure(encoding="utf-8", errors="replace")
    except (AttributeError, ValueError):
        pass

from config import settings
from recon.eval.ablation import render_markdown, run_ablation
from recon.rag import PolicyIndex


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--data", default=None, help="source directory (default data/synthetic)")
    args = parser.parse_args()

    print("Ablation — what each layer buys")
    print("=" * 78)

    # Warm the index once and share it, so the final variant isn't charged for a
    # model load the others never pay.
    print("loading policy index ...", flush=True)
    index = PolicyIndex()
    index.query("input tax credit", k=1)
    print("  ready\n")

    report = run_ablation(Path(args.data) if args.data else None, index=index)

    header = f"{'variant':<24} {'auto':>6} {'escal':>6} {'bucket':>8} {'prec':>7} {'recall':>7} {'llm':>5}"
    print(header)
    print("-" * len(header))
    for row in report.rows:
        m = row.metrics
        print(
            f"{row.variant.label:<24} "
            f"{row.counts['auto_resolved']:>6} "
            f"{row.counts['escalated']:>6} "
            f"{m.bucket_accuracy:>7.1%} "
            f"{m.match_precision:>7.1%} "
            f"{m.match_recall:>7.1%} "
            f"{m.llm_call_count:>5}"
        )
    print("-" * len(header))

    last = report.rows[-1]
    ratio = report.money_moving / max(last.metrics.llm_call_count, 1)
    print(
        f"\nrouting: {last.metrics.llm_call_count} records reach the LLM; a no-routing "
        f"design would send {report.money_moving} ({ratio:.1f}x)"
    )

    settings.REPORT_DIR.mkdir(parents=True, exist_ok=True)
    json_path = settings.REPORT_DIR / "ablation.json"
    md_path = settings.REPORT_DIR / "ablation.md"
    json_path.write_text(json.dumps(report.as_dict(), indent=2), encoding="utf-8")
    md_path.write_text(render_markdown(report), encoding="utf-8")
    print(f"\nwrote {json_path.relative_to(ROOT).as_posix()} and {md_path.relative_to(ROOT).as_posix()}")


if __name__ == "__main__":
    main()
