"""Day 3 (task 1.5) — populate data/llm_cache/ with real Gemini responses.

Runs the reasoning layer in **live** mode over every ``unmatched-ambiguous``
record the deterministic matcher produced, and writes each Gemini response to the
content-addressed cache. Idempotent: a record whose request is already cached is
skipped, so re-runs only fill gaps.

The resulting cache is committed. From then on the whole pipeline — including
evaluation — runs offline with no API key.

Usage
-----
    # GEMINI_API_KEY in .env
    python scripts/populate_llm_cache.py
    python scripts/populate_llm_cache.py --limit 5      # try a handful first
"""

from __future__ import annotations

import argparse
import re
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

from dotenv import load_dotenv

load_dotenv(ROOT / ".env")

from config import settings
from recon.audit import AuditLogger
from recon.ingest import load_all
from recon.matching import Bucket, reconcile
from recon.reasoning import GeminiReasoner
from recon.reasoning.llm_client import CandidateView, ReasoningRequest, TxnView

_RETRY_SECONDS = re.compile(r"retry(?:\s+in|\s*delay['\": ]+)\s*([0-9.]+)\s*s", re.IGNORECASE)


def _retry_after(exc: Exception, default: float) -> float:
    m = _RETRY_SECONDS.search(str(exc))
    return float(m.group(1)) + 1.0 if m else default


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--limit", type=int, default=0, help="only process the first N (0 = all)")
    parser.add_argument("--pace", type=float, default=13.0, help="seconds between calls (free tier ~5/min)")
    args = parser.parse_args()

    if not settings.GEMINI_API_KEY:
        sys.exit("GEMINI_API_KEY not set. Add it to .env, then re-run.")

    ingest = load_all()
    match_report = reconcile(ingest, AuditLogger(path=None))
    index = {t.txn_id: t for t in ingest.records}
    ambiguous = match_report.in_bucket(Bucket.AMBIGUOUS)
    if args.limit:
        ambiguous = ambiguous[: args.limit]

    reasoner = GeminiReasoner(replay_only=False)
    print(f"model {reasoner.model}  |  {len(ambiguous)} ambiguous records\n")

    done = cached = failed = 0
    for i, decision in enumerate(ambiguous, start=1):
        record = index.get(decision.record_id)
        cands = [
            CandidateView(TxnView.of(index[c.txn_id]), c.score.as_dict())
            for c in decision.candidates
            if c.txn_id in index
        ]
        if record is None or not cands:
            continue
        request = ReasoningRequest(TxnView.of(record), tuple(cands), decision.rationale)
        key = request.cache_key(prompt_version=reasoner.prompt_version, model=reasoner.model)
        if (settings.LLM_CACHE_DIR / f"{key}.json").exists():
            cached += 1
            continue

        for attempt in range(4):
            try:
                result = reasoner.reason(request)
                done += 1
                print(
                    f"  [{i}/{len(ambiguous)}] {decision.record_id}: "
                    f"{result.decision} conf={result.confidence:.2f}"
                )
                break
            except Exception as exc:  # noqa: BLE001 - report and move on
                if "429" in str(exc) and attempt < 3:
                    wait = _retry_after(exc, 30.0)
                    print(f"  [{i}/{len(ambiguous)}] rate-limited, sleeping {wait:.0f}s")
                    time.sleep(wait)
                    continue
                failed += 1
                print(f"  [{i}/{len(ambiguous)}] {decision.record_id}: FAILED {str(exc)[:120]}")
                break
        time.sleep(args.pace)

    print(f"\nwrote {done}, already cached {cached}, failed {failed}")
    print(f"cache dir: {settings.LLM_CACHE_DIR.relative_to(ROOT)}")


if __name__ == "__main__":
    main()
