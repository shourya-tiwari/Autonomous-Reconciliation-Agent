"""Evaluation metrics — DEFINED on Day 1 (task 1.1), RUN on Day 6 (task 1.8).

The metric set is fixed up front so results can't be cherry-picked later. Every
metric is computed by comparing the pipeline's per-record output against
``data/ground_truth/matches.csv``.

Ground-truth columns (see scripts/generate_synthetic.py)
-------------------------------------------------------
* ``record_id``        — id of a record in one of the 3 source files
* ``true_match_id``    — id of the record it truly reconciles with, or "" if none
* ``expected_bucket``  — one of: auto_resolved | escalated | exception

Pipeline output (per record), produced by recon.agent.orchestrator
------------------------------------------------------------------
* ``predicted_match_id`` — "" if the pipeline made no match
* ``predicted_bucket``   — auto_resolved | escalated | exception | failed
* ``stage``              — which stage decided (exact | fuzzy | llm | rag | reject)

Metrics
-------
match_precision   : of the matches the pipeline asserted, fraction that are correct
                    (predicted_match_id == true_match_id, over records where
                    predicted_match_id != "")
match_recall      : of the records that truly have a match, fraction the pipeline
                    matched correctly
match_f1          : harmonic mean of the two
auto_resolved_pct : records ending in auto_resolved / total
escalated_pct     : records ending in escalated / total
exception_pct     : records ending in exception / total
failed_pct        : records ending in failed / total
bucket_accuracy   : predicted_bucket == expected_bucket / total
throughput_rps    : total records / wall-clock seconds for the full run
llm_call_count    : how many records reached the LLM stage (cost signal)
rag_call_count    : how many records reached the RAG stage

TODO(Day 6): implement compute() against real run output + ground truth, and
emit outputs/reports/metrics.json + summary.md.
"""

from __future__ import annotations

from dataclasses import dataclass


@dataclass
class ReconMetrics:
    match_precision: float
    match_recall: float
    match_f1: float
    auto_resolved_pct: float
    escalated_pct: float
    exception_pct: float
    failed_pct: float
    bucket_accuracy: float
    throughput_rps: float
    llm_call_count: int
    rag_call_count: int
    n_records: int


def compute(predictions, ground_truth, wall_clock_seconds: float) -> ReconMetrics:
    """Compare pipeline predictions to ground truth. TODO(Day 6)."""
    raise NotImplementedError("implemented in task 1.8")
