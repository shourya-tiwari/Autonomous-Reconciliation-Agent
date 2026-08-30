"""End-to-end entrypoint — ingest → match → reason → ground → log → metrics.

This is the command a judge runs from a clean clone. It needs **no API key and no
network**: the corpus is committed, LLM judgments are replayed from
``data/llm_cache/`` (a cache miss escalates safely rather than failing), and
embeddings are computed locally.

Usage
-----
    python scripts/run_pipeline.py                 # run, print the summary
    python scripts/run_pipeline.py --report        # + score against ground truth
    python scripts/run_pipeline.py --data data/synthetic --report
    python scripts/run_pipeline.py --live-llm      # call Gemini for cache misses

Outputs
-------
    outputs/audit/run-<id>.jsonl   every decision, one JSON object per line
    outputs/reports/metrics.json   the scored metrics      (with --report)
    outputs/reports/summary.md     a readable write-up     (with --report)
"""

from __future__ import annotations

import argparse
import json
import sys
import time
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path[:0] = [str(ROOT), str(ROOT / "src")]

for _stream in (sys.stdout, sys.stderr):
    try:
        _stream.reconfigure(encoding="utf-8", errors="replace")
    except (AttributeError, ValueError):
        pass

from config import settings
from recon.agent import FinalBucket, run_pipeline
from recon.audit import AuditLogger
from recon.eval.metrics import compute, render_summary
from recon.rag import PolicyIndex

_BUCKET_BLURB = {
    FinalBucket.AUTO_RESOLVED: "reconciled with no human involvement",
    FinalBucket.ESCALATED: "real ambiguity -> human, with the LLM's finding attached",
    FinalBucket.EXCEPTION: "no counterpart -> explained with a cited GST clause",
    FinalBucket.IGNORED: "failed payments -- no money moved",
    FinalBucket.FAILED: "unparseable row, rejected with a reason",
}


def _warm_index() -> tuple[PolicyIndex, float]:
    """Build/load the policy index up front and report what it cost.

    The warmed index is *returned and reused*, not discarded: if the pipeline
    builds its own, the embedding-model load lands inside the timed section and
    gets counted twice — once as setup, once again as pipeline work — which
    understates throughput by roughly 4x.
    """
    started = time.perf_counter()
    index = PolicyIndex()
    index.query("input tax credit", k=1)
    return index, time.perf_counter() - started


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--data", default=None, help="source directory (default data/synthetic)")
    parser.add_argument("--report", action="store_true", help="score against data/ground_truth/")
    parser.add_argument("--live-llm", action="store_true", help="call Gemini on cache misses")
    parser.add_argument("--no-inject", action="store_true", help="disable the injected API timeout")
    parser.add_argument("--quiet", action="store_true", help="summary only")
    args = parser.parse_args()

    print("Autonomous Reconciliation Agent")
    print("=" * 62)

    print("loading policy index ...", flush=True)
    index, model_load = _warm_index()
    print(f"  ready in {model_load:.1f}s\n")

    audit = AuditLogger.for_run()
    result = run_pipeline(
        Path(args.data) if args.data else None,
        audit=audit,
        index=index,
        inject_timeout=not args.no_inject,
        live_llm=args.live_llm,
    )
    audit.close()

    summary = result.summary()
    total = summary["total"]
    print(f"processed {total} records in {summary['elapsed_seconds']}s\n")
    print(f"{'outcome':<15} {'count':>6} {'share':>7}   meaning")
    print("-" * 96)
    for bucket in FinalBucket:
        count = summary[bucket.value]
        print(f"{bucket.value:<15} {count:>6} {count / total:>6.1%}   {_BUCKET_BLURB[bucket]}")
    print("-" * 96)

    print("\nfailure modes")
    for outcome in result.in_bucket(FinalBucket.FAILED):
        print(f"  malformed row  {outcome.record_id}: {outcome.rationale}")
    if result.retries:
        for record_id, attempt, error in result.retries:
            print(f"  api timeout    {record_id}: attempt {attempt} failed ({error}) -> retried, absorbed")
    else:
        print("  api timeout    none triggered")

    print(f"\naudit trail    {len(audit)} entries -> {audit.path.relative_to(ROOT)}")

    if not args.report:
        print("\n(pass --report to score against data/ground_truth/)")
        return

    metrics = compute(result, setup_seconds=model_load)
    settings.REPORT_DIR.mkdir(parents=True, exist_ok=True)
    metrics_path = settings.REPORT_DIR / "metrics.json"
    summary_path = settings.REPORT_DIR / "summary.md"
    metrics_path.write_text(json.dumps(metrics.as_dict(), indent=2), encoding="utf-8")
    summary_path.write_text(render_summary(metrics, result), encoding="utf-8")

    print("\nevaluation vs data/ground_truth/")
    print("-" * 62)
    print(f"  bucket accuracy      {metrics.bucket_accuracy:>7.1%}   "
          f"({total - len(metrics.misbucketed)}/{total} records)")
    print(f"  match precision      {metrics.match_precision:>7.1%}   "
          f"({metrics.matches_correct}/{metrics.matches_asserted} asserted pairings)")
    print(f"  match recall         {metrics.match_recall:>7.1%}   "
          f"({metrics.matches_correct}/{metrics.matches_expected} true pairings auto-paired; "
          "the rest are escalated, not missed)")
    print(f"  match F1             {metrics.match_f1:>7.1%}")
    print(f"  settlement accuracy  {metrics.settlement_accuracy:>7.1%}   "
          f"({metrics.settlements_correct}/{metrics.settlements_total} N:1 batches)")
    print(f"  throughput           {metrics.throughput_rps:>7.1f}   records/s "
          f"(after a one-off {metrics.setup_seconds}s model load)")
    print(f"  llm calls            {metrics.llm_call_count:>7}   "
          f"({metrics.llm_call_count / total:.0%} of the corpus)")
    print(f"  retries absorbed     {metrics.retry_count:>7}")
    if metrics.misbucketed and not args.quiet:
        print(f"\n  {len(metrics.misbucketed)} misbucketed:")
        for row in metrics.misbucketed[:10]:
            print(f"    {row['record_id']:<24} {row['case']:<18} "
                  f"expected {row['expected']:<14} got {row['got']}")
    print(f"\nwrote {metrics_path.relative_to(ROOT)} and {summary_path.relative_to(ROOT)}")


if __name__ == "__main__":
    main()
