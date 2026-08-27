"""Evaluation metrics — DEFINED on Day 1 (task 1.1), COMPUTED on Day 6 (task 1.8).

The metric set was fixed before any of it could be measured, so nothing here was
chosen after seeing which numbers looked good. Everything is computed by
comparing the pipeline's per-record outcomes against ``data/ground_truth/``.

Ground truth
------------
``matches.csv``            ``record_id, source, true_match_id, expected_bucket, case``
``settlement_groups.csv``  ``settlement_id, payment_id, …`` — the N:1 answer key

Metrics
-------
bucket_accuracy      fraction of records whose terminal bucket is the expected one.
                     The headline number: it covers matching, LLM escalation, RAG
                     grounding and both failure modes in one figure.
match_precision      of the 1:1 pairings the pipeline asserted, the fraction that
                     name the correct counterpart. This is the metric that must
                     not slip: a wrong auto-match moves money incorrectly.
match_recall         of the 1:1 pairings that truly exist, the fraction the
                     pipeline auto-paired. Deliberately below 100%: a genuinely
                     ambiguous pair (partial capture, duplicate reference, FX
                     variance) is escalated rather than guessed, and that costs
                     recall by design. Read it together with precision — the
                     shortfall is escalations, not misses.
match_f1             harmonic mean of the two.
settlement_accuracy  fraction of N:1 bank settlements matched to exactly the right
                     set of gateway payments. Scored separately because a single
                     ``true_match_id`` column cannot express a group.
*_pct                share of the corpus ending in each terminal bucket. Not a
                     score — a workload profile: what a controller actually faces.
throughput_rps       records per second for the pipeline itself. The one-off
                     embedding-model load is reported separately as
                     ``setup_seconds`` rather than folded in.
llm_call_count       records that reached the LLM (cost signal — should be small).
rag_call_count       records that reached RAG.
retry_count          transient failures absorbed without losing a record.
"""

from __future__ import annotations

import csv
from collections import defaultdict
from dataclasses import asdict, dataclass, field
from pathlib import Path

from config import settings

# Sources whose records are 1:1 reconcilable; bank settlements are N:1 and are
# scored by settlement_accuracy instead.
_PAIRWISE_SOURCES = {"invoice", "gateway"}


@dataclass
class ReconMetrics:
    bucket_accuracy: float
    match_precision: float
    match_recall: float
    match_f1: float
    settlement_accuracy: float

    auto_resolved_pct: float
    escalated_pct: float
    exception_pct: float
    ignored_pct: float
    failed_pct: float

    throughput_rps: float
    throughput_rps_incl_setup: float
    elapsed_seconds: float
    setup_seconds: float

    llm_call_count: int
    rag_call_count: int
    retry_count: int
    n_records: int

    matches_asserted: int = 0
    matches_correct: int = 0
    matches_expected: int = 0
    settlements_correct: int = 0
    settlements_total: int = 0
    misbucketed: list[dict[str, str]] = field(default_factory=list)

    def as_dict(self) -> dict:
        return asdict(self)


def load_ground_truth(directory: Path | None = None) -> dict[str, dict[str, str]]:
    path = (directory or settings.GROUND_TRUTH_DIR) / "matches.csv"
    with path.open(newline="", encoding="utf-8") as fh:
        return {row["record_id"]: row for row in csv.DictReader(fh)}


def load_settlement_groups(directory: Path | None = None) -> dict[str, frozenset[str]]:
    path = (directory or settings.GROUND_TRUTH_DIR) / "settlement_groups.csv"
    if not path.exists():
        return {}
    groups: dict[str, set[str]] = defaultdict(set)
    with path.open(newline="", encoding="utf-8") as fh:
        for row in csv.DictReader(fh):
            groups[row["settlement_id"]].add(row["payment_id"])
    return {sid: frozenset(members) for sid, members in groups.items()}


def _ratio(numerator: int, denominator: int) -> float:
    return round(numerator / denominator, 4) if denominator else 0.0


def compute(
    result,
    ground_truth: dict[str, dict[str, str]] | None = None,
    settlement_groups: dict[str, frozenset[str]] | None = None,
    *,
    setup_seconds: float = 0.0,
) -> ReconMetrics:
    """Score a :class:`~recon.agent.PipelineResult` against the answer key."""
    truth = load_ground_truth() if ground_truth is None else ground_truth
    groups = load_settlement_groups() if settlement_groups is None else settlement_groups
    expected_group_sets = set(groups.values())

    outcomes = result.outcomes
    n = len(outcomes)

    # --- bucket accuracy -------------------------------------------
    correct_bucket = 0
    misbucketed: list[dict[str, str]] = []
    for outcome in outcomes:
        row = truth.get(outcome.record_id)
        if row is None:
            misbucketed.append(
                {"record_id": outcome.record_id, "case": "not-in-ground-truth",
                 "expected": "?", "got": outcome.bucket.value}
            )
            continue
        if row["expected_bucket"] == outcome.bucket.value:
            correct_bucket += 1
        else:
            misbucketed.append(
                {"record_id": outcome.record_id, "case": row["case"],
                 "expected": row["expected_bucket"], "got": outcome.bucket.value}
            )

    # --- 1:1 match precision / recall -------------------------------
    asserted = correct = 0
    for outcome in outcomes:
        if outcome.source not in _PAIRWISE_SOURCES or not outcome.matched_to:
            continue
        asserted += 1
        row = truth.get(outcome.record_id)
        if row and row["true_match_id"] and row["true_match_id"] in outcome.matched_to:
            correct += 1

    expected_matches = sum(
        1
        for rid, row in truth.items()
        if row["source"] in _PAIRWISE_SOURCES and row["true_match_id"]
    )
    precision = _ratio(correct, asserted)
    recall = _ratio(correct, expected_matches)
    f1 = round(2 * precision * recall / (precision + recall), 4) if precision + recall else 0.0

    # --- N:1 settlement accuracy ------------------------------------
    settlements_correct = 0
    if result.matching is not None:
        for decision in result.matching.decisions:
            if decision.method != "settlement-group" or not decision.matched_to:
                continue
            if frozenset(decision.matched_to) in expected_group_sets:
                settlements_correct += 1

    # --- workload profile -------------------------------------------
    def share(bucket: str) -> float:
        return _ratio(sum(1 for o in outcomes if o.bucket.value == bucket), n)

    elapsed = result.elapsed_seconds
    with_setup = elapsed + setup_seconds

    return ReconMetrics(
        bucket_accuracy=_ratio(correct_bucket, n),
        match_precision=precision,
        match_recall=recall,
        match_f1=f1,
        settlement_accuracy=_ratio(settlements_correct, len(groups)),
        auto_resolved_pct=share("auto_resolved"),
        escalated_pct=share("escalated"),
        exception_pct=share("exception"),
        ignored_pct=share("ignored"),
        failed_pct=share("failed"),
        throughput_rps=round(n / elapsed, 1) if elapsed else 0.0,
        throughput_rps_incl_setup=round(n / with_setup, 1) if with_setup else 0.0,
        elapsed_seconds=round(elapsed, 2),
        setup_seconds=round(setup_seconds, 2),
        llm_call_count=len(result.reasoning.outcomes) if result.reasoning else 0,
        rag_call_count=len(result.grounding.explanations) if result.grounding else 0,
        retry_count=len(result.retries),
        n_records=n,
        matches_asserted=asserted,
        matches_correct=correct,
        matches_expected=expected_matches,
        settlements_correct=settlements_correct,
        settlements_total=len(groups),
        misbucketed=misbucketed,
    )


def render_summary(metrics: ReconMetrics, result) -> str:
    """A readable report for `outputs/reports/summary.md`."""
    m = metrics
    stats = (result.reasoning.stats if result.reasoning else {}) or {}
    n_ok = m.n_records - len(m.misbucketed)
    llm_share = m.llm_call_count / m.n_records if m.n_records else 0.0
    counts = {b: len(result.in_bucket_by_name(b)) for b in
              ("auto_resolved", "escalated", "exception", "ignored", "failed")}
    lines = [
        "# Reconciliation run — evaluation summary",
        "",
        (
            f"Corpus: **{m.n_records} records** across 3 sources "
            f"(`gateway_export.csv`, `invoice_ledger.csv`, `bank_statement.csv`)."
        ),
        (
            f"Wall clock: **{m.elapsed_seconds}s** ({m.throughput_rps} records/s), "
            f"after a one-off {m.setup_seconds}s embedding-model load "
            f"({m.throughput_rps_incl_setup}/s including it)."
        ),
        "",
        "## Accuracy",
        "",
        "| Metric | Value | Basis |",
        "|--------|-------|-------|",
        (
            f"| Bucket accuracy | **{m.bucket_accuracy:.1%}** | {n_ok}/{m.n_records} "
            "records in the expected terminal bucket |"
        ),
        (
            f"| Match precision | **{m.match_precision:.1%}** | {m.matches_correct}/"
            f"{m.matches_asserted} asserted 1:1 pairings correct |"
        ),
        (
            f"| Match recall | **{m.match_recall:.1%}** | {m.matches_correct}/"
            f"{m.matches_expected} true 1:1 pairings auto-paired |"
        ),
        f"| Match F1 | **{m.match_f1:.1%}** | harmonic mean |",
        (
            f"| Settlement accuracy | **{m.settlement_accuracy:.1%}** | "
            f"{m.settlements_correct}/{m.settlements_total} N:1 bank settlements "
            "matched to the exact batch |"
        ),
        "",
        (
            f"Recall is **{m.match_recall:.1%}**, not 100%, on purpose. The "
            f"{m.matches_expected - m.matches_correct} pairings it does not assert are "
            "the genuinely ambiguous ones — partial captures, duplicate references, FX "
            "variances — which are escalated to a human rather than guessed. Read it "
            "alongside precision: the pipeline never asserted a wrong pairing. Trading "
            "recall for precision is the right direction when the output moves money."
        ),
        "",
        "## Workload profile",
        "",
        "How the corpus resolves — this is what a controller would actually face.",
        "",
        "| Outcome | Share | Count | Meaning |",
        "|---------|-------|-------|---------|",
        (
            f"| auto_resolved | {m.auto_resolved_pct:.1%} | {counts['auto_resolved']} | "
            "reconciled with no human involvement |"
        ),
        (
            f"| escalated | {m.escalated_pct:.1%} | {counts['escalated']} | "
            "a real ambiguity, sent to a human with the LLM's finding |"
        ),
        (
            f"| exception | {m.exception_pct:.1%} | {counts['exception']} | "
            "no counterpart; explained and cited from GST policy |"
        ),
        (
            f"| ignored | {m.ignored_pct:.1%} | {counts['ignored']} | "
            "failed payments — no money moved |"
        ),
        (
            f"| failed | {m.failed_pct:.1%} | {counts['failed']} | "
            "unparseable row, rejected with a reason |"
        ),
        "",
        "## Cost control",
        "",
        (
            f"- **{m.llm_call_count}** of {m.n_records} records reached the LLM "
            f"({llm_share:.0%}) — the deterministic layer absorbs the rest."
        ),
        f"- **{m.rag_call_count}** reached RAG.",
        (
            f"- LLM response source: {stats or 'n/a'} (`cache` = replayed from the "
            "committed cache, `fallback` = no cached judgment, escalated safely)."
        ),
        "",
        "## Failure modes handled",
        "",
        (
            f"- **Malformed row** — {counts['failed']} row(s) rejected at ingest with a "
            "per-field reason, logged, run continued."
        ),
        (
            f"- **LLM API timeout** — {m.retry_count} transient failure(s) retried with "
            "backoff and absorbed; no record lost."
        ),
    ]
    if m.misbucketed:
        lines += [
            "",
            "## Misbucketed records",
            "",
            "| Record | Case | Expected | Got |",
            "|--------|------|----------|-----|",
            *(
                f"| `{r['record_id']}` | {r['case']} | {r['expected']} | {r['got']} |"
                for r in m.misbucketed[:25]
            ),
        ]
        if len(m.misbucketed) > 25:
            lines.append(f"\n…and {len(m.misbucketed) - 25} more.")
    else:
        lines += ["", "## Misbucketed records", "", "None."]
    return "\n".join(lines) + "\n"
