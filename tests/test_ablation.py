"""Tests for the layer ablation (task 3.3).

An ablation is only worth quoting if the variants differ in exactly the way they
claim to. The risks are: a disabled layer silently dropping records (which would
flatter every downstream number), the stage toggles leaking into the default
pipeline, and the report overstating what the table shows.
"""

from __future__ import annotations

from collections import Counter

import pytest

from recon.agent import FinalBucket, run_pipeline
from recon.eval.ablation import (
    VARIANTS,
    AblationReport,
    AblationRow,
    Variant,
    render_markdown,
    run_ablation,
)
from recon.eval.metrics import ReconMetrics
from recon.ingest import load_all
from recon.matching import ALL_STAGES, Bucket, MatchStages, reconcile

# The deterministic-only variants; the shipped one needs the embedding model.
_OFFLINE_VARIANTS = tuple(v for v in VARIANTS if not v.use_rag)


@pytest.fixture(scope="module")
def ingested():
    return load_all()


# --- the toggles must not change the shipped pipeline ------------------


def test_default_reconcile_is_unchanged_by_the_stage_parameter(ingested):
    """The ablation must not have altered the pipeline it measures."""
    explicit = reconcile(ingested, None, ALL_STAGES)
    implicit = reconcile(ingested)
    assert explicit.summary() == implicit.summary()


def test_all_stages_is_every_stage_on():
    assert (ALL_STAGES.exact, ALL_STAGES.fuzzy, ALL_STAGES.settlement) == (True, True, True)


def test_shipped_variant_is_the_full_configuration():
    shipped = VARIANTS[-1]
    assert shipped.stages == ALL_STAGES
    assert shipped.use_llm and shipped.use_rag


def test_stage_label_names_the_active_layers():
    assert MatchStages(True, False, False).label == "exact"
    assert MatchStages(True, True, False).label == "exact+fuzzy"
    assert ALL_STAGES.label == "exact+fuzzy+settlement"


# --- a disabled layer must not lose records ----------------------------


@pytest.mark.parametrize("stages", [
    MatchStages(exact=True, fuzzy=False, settlement=False),
    MatchStages(exact=True, fuzzy=True, settlement=False),
    MatchStages(exact=False, fuzzy=True, settlement=True),
    ALL_STAGES,
])
def test_every_variant_decides_every_money_moving_record_exactly_once(ingested, stages):
    report = reconcile(ingested, None, stages)
    ids = [d.record_id for d in report.decisions]
    assert len(ids) == len(set(ids)), "a record was decided twice"
    assert len(ids) == len(ingested.records)


def test_disabled_layers_escalate_rather_than_resolve_or_drop(ingested):
    """The honest reading of a missing layer: nothing resolved, nothing lost."""
    full = reconcile(ingested, None, ALL_STAGES)
    reduced = reconcile(ingested, None, MatchStages(exact=True, fuzzy=False, settlement=False))

    assert len(reduced.decisions) == len(full.decisions)
    assert len(reduced.in_bucket(Bucket.MATCHED)) < len(full.in_bucket(Bucket.MATCHED))
    assert len(reduced.in_bucket(Bucket.AMBIGUOUS)) > len(full.in_bucket(Bucket.AMBIGUOUS))
    # ignored is decided before any optional layer, so it must be identical
    assert len(reduced.in_bucket(Bucket.IGNORED)) == len(full.in_bucket(Bucket.IGNORED))


def test_disabled_layer_decisions_say_why(ingested):
    reduced = reconcile(ingested, None, MatchStages(exact=True, fuzzy=False, settlement=False))
    disabled = [d for d in reduced.decisions if d.method == "stage-disabled"]
    assert disabled, "expected records left unresolved by the disabled layers"
    assert all("disabled" in d.rationale for d in disabled)
    assert all(d.bucket is Bucket.AMBIGUOUS and not d.matched_to for d in disabled)


# --- the orchestrator toggles -----------------------------------------


def test_pipeline_without_the_llm_still_buckets_every_row():
    result = run_pipeline(use_llm=False, use_rag=False, inject_timeout=False)
    counts = Counter(o.record_id for o in result.outcomes)
    assert max(counts.values()) == 1, "a record landed in two buckets"
    assert result.reasoning is None and result.grounding is None
    assert sum(len(result.in_bucket(b)) for b in FinalBucket) == len(result.outcomes)


def test_skipping_the_llm_does_not_change_which_records_are_escalated():
    """The model explains escalations; it does not decide them on this corpus."""
    without = run_pipeline(use_llm=False, use_rag=False, inject_timeout=False)
    escalated = {o.record_id for o in without.in_bucket(FinalBucket.ESCALATED)}
    ambiguous = {d.record_id for d in without.matching.in_bucket(Bucket.AMBIGUOUS)}
    assert escalated == ambiguous


def test_skipping_rag_still_reports_the_exceptions_uncited():
    result = run_pipeline(use_llm=False, use_rag=False, inject_timeout=False)
    exceptions = result.in_bucket(FinalBucket.EXCEPTION)
    assert exceptions
    assert all("uncited" in o.rationale for o in exceptions)


# --- the ablation itself ----------------------------------------------


@pytest.fixture(scope="module")
def offline_report():
    return run_ablation(variants=_OFFLINE_VARIANTS)


def test_adding_layers_never_lowers_auto_resolution(offline_report):
    resolved = [r.counts["auto_resolved"] for r in offline_report.rows]
    assert resolved == sorted(resolved), f"not monotonic: {resolved}"
    assert resolved[0] < resolved[-1], "the layers should buy something"


def test_precision_holds_at_every_variant(offline_report):
    """The layers must buy recall, never trade precision for it."""
    for row in offline_report.rows:
        assert row.metrics.match_precision == 1.0, row.variant.key


def test_recall_improves_with_the_layers(offline_report):
    recalls = [r.metrics.match_recall for r in offline_report.rows]
    assert recalls == sorted(recalls)
    assert recalls[-1] > recalls[0]


def test_every_variant_scores_the_same_number_of_records(offline_report):
    totals = {sum(r.counts.values()) for r in offline_report.rows}
    assert len(totals) == 1, f"variants scored different corpora: {totals}"
    assert totals.pop() == offline_report.n_records


def test_money_moving_excludes_ignored_and_failed(offline_report):
    row = offline_report.rows[0]
    assert offline_report.money_moving == (
        offline_report.n_records - row.counts["ignored"] - row.counts["failed"]
    )
    assert 0 < offline_report.money_moving < offline_report.n_records


def test_settlement_layer_is_what_fixes_the_settlements(offline_report):
    without = offline_report.row("exact_fuzzy")
    with_it = offline_report.row("deterministic")
    assert without.metrics.settlements_correct == 0
    assert with_it.metrics.settlements_correct == with_it.metrics.settlements_total > 0


# --- the write-up ------------------------------------------------------


def _fake_report() -> AblationReport:
    def row(key, label, auto, escalated, recall, llm=0, findings=0, citations=0):
        metrics = ReconMetrics(
            bucket_accuracy=0.9, match_precision=1.0, match_recall=recall, match_f1=0.8,
            settlement_accuracy=1.0, auto_resolved_pct=0.5, escalated_pct=0.2,
            exception_pct=0.1, ignored_pct=0.1, failed_pct=0.0, throughput_rps=1.0,
            throughput_rps_incl_setup=1.0, elapsed_seconds=1.0, setup_seconds=0.0,
            llm_call_count=llm, rag_call_count=citations, retry_count=0, n_records=100,
            settlements_correct=8, settlements_total=8,
        )
        return AblationRow(
            variant=Variant(key, label, "blurb.", ALL_STAGES),
            metrics=metrics,
            counts={"auto_resolved": auto, "escalated": escalated, "exception": 10,
                    "ignored": 5, "failed": 1},
            seconds=0.1,
            findings_attached=findings,
            citations_attached=citations,
        )

    return AblationReport(
        rows=[row("exact", "Exact only", 20, 64, 0.3),
              row("deterministic", "+ settlements", 50, 34, 0.7),
              row("full", "+ LLM + RAG", 50, 34, 0.7, llm=34, findings=6, citations=10)],
        money_moving=94,
        n_records=100,
    )


def test_markdown_reports_every_variant_and_stays_valid():
    md = render_markdown(_fake_report())
    assert md.count("\n## ") == 3
    for label in ("Exact only", "+ settlements", "+ LLM + RAG"):
        assert label in md
    assert md.endswith("\n")


def test_markdown_states_the_routing_ratio_as_a_ratio_not_a_cost():
    md = render_markdown(_fake_report())
    assert "2.8×" in md, "94 money-moving / 34 routed"
    assert "not a latency or rupee figure" in md


def test_markdown_refuses_to_credit_the_model_for_bucket_gains():
    """The LLM row is identical to the row above it; the prose must say so."""
    md = render_markdown(_fake_report())
    assert "barely move the table" in md
    assert "review cost" in md


def test_markdown_reports_findings_and_citations_honestly():
    md = render_markdown(_fake_report())
    assert "6 of the 34 escalations" in md
    assert "10 of the 10 exceptions" in md
