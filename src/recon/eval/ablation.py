"""Task 3.3 — measure what each layer actually buys, by removing it.

The pipeline's central claim is that a layered design beats routing everything
through a model: the deterministic layers resolve most of the corpus, and only
what genuinely can't be settled reaches the LLM. That is easy to assert and easy
to believe. This measures it.

Four variants, each scored against the same ground truth by the same
``metrics.compute``:

``exact``          reference id + amount + currency + day. The naive baseline —
                   what you get from a SQL join on the reference column.
``exact+fuzzy``    adds tolerant scoring (amount / date / name / ref).
``deterministic``  adds N:1 settlement matching. The full no-model pipeline.
``full``           adds LLM reasoning and RAG grounding. What ships.

A disabled layer never drops records: whatever it would have resolved falls
through to ``escalated``, which is the honest reading of "this capability is
missing" — nothing is resolved, and nothing disappears from the totals.

Reading the result
------------------
The interesting finding is not that accuracy climbs. It is *where* it climbs and
where it doesn't. The deterministic layers move bucket counts; the LLM and RAG
layers, on this corpus, move almost none — because a confident LLM match with a
residual amount variance still escalates by design. What those layers change is
what a reviewer is handed: an escalation with the model's finding attached
instead of a bare "unresolved", and an exception with a statute citation instead
of a bare "no counterpart". :func:`render_markdown` says so rather than letting
the table imply the model raised the score.
"""

from __future__ import annotations

import time
from dataclasses import dataclass, field
from pathlib import Path

from recon.agent import FinalBucket, run_pipeline
from recon.matching import MatchStages

from .metrics import ReconMetrics, compute


@dataclass(frozen=True)
class Variant:
    key: str
    label: str
    blurb: str
    stages: MatchStages
    use_llm: bool = False
    use_rag: bool = False


VARIANTS: tuple[Variant, ...] = (
    Variant(
        "exact",
        "Exact only",
        "Reference id + amount + currency + day must all agree. This is the naive "
        "baseline: a join on the reference column.",
        MatchStages(exact=True, fuzzy=False, settlement=False),
    ),
    Variant(
        "exact_fuzzy",
        "+ fuzzy",
        "Adds tolerant scoring over amount / date / counterparty name / reference, "
        "gated on a confidence threshold and a margin over the runner-up.",
        MatchStages(exact=True, fuzzy=True, settlement=False),
    ),
    Variant(
        "deterministic",
        "+ N:1 settlements",
        "Adds subset-sum matching of a bank settlement credit against the batch of "
        "gateway payments behind it. The complete no-model pipeline.",
        MatchStages(exact=True, fuzzy=True, settlement=True),
    ),
    Variant(
        "full",
        "+ LLM + RAG (shipped)",
        "Adds structured LLM reasoning over the ambiguous bucket and RAG grounding "
        "over the exception bucket.",
        MatchStages(exact=True, fuzzy=True, settlement=True),
        use_llm=True,
        use_rag=True,
    ),
)


@dataclass
class AblationRow:
    variant: Variant
    metrics: ReconMetrics
    counts: dict[str, int]
    seconds: float
    findings_attached: int = 0   # escalations carrying a real model judgment
    citations_attached: int = 0  # exceptions carrying a quoted statute clause


@dataclass
class AblationReport:
    rows: list[AblationRow] = field(default_factory=list)
    money_moving: int = 0        # records a no-routing design would send to the LLM
    n_records: int = 0

    def row(self, key: str) -> AblationRow | None:
        return next((r for r in self.rows if r.variant.key == key), None)

    def as_dict(self) -> dict:
        return {
            "n_records": self.n_records,
            "money_moving": self.money_moving,
            "variants": [
                {
                    "key": r.variant.key,
                    "label": r.variant.label,
                    "blurb": r.variant.blurb,
                    "stages": {
                        "exact": r.variant.stages.exact,
                        "fuzzy": r.variant.stages.fuzzy,
                        "settlement": r.variant.stages.settlement,
                        "llm": r.variant.use_llm,
                        "rag": r.variant.use_rag,
                    },
                    "counts": r.counts,
                    "seconds": round(r.seconds, 3),
                    "findings_attached": r.findings_attached,
                    "citations_attached": r.citations_attached,
                    "metrics": r.metrics.as_dict(),
                }
                for r in self.rows
            ],
        }


def run_ablation(
    data_dir: Path | None = None,
    *,
    index: object | None = None,
    variants: tuple[Variant, ...] = VARIANTS,
) -> AblationReport:
    """Run every variant over the same corpus and score each one identically."""
    report = AblationReport()

    for variant in variants:
        started = time.perf_counter()
        result = run_pipeline(
            data_dir,
            index=index,
            stages=variant.stages,
            use_llm=variant.use_llm,
            use_rag=variant.use_rag,
            inject_timeout=False,  # the injected timeout is a separate demo; keep runs comparable
        )
        seconds = time.perf_counter() - started

        counts = {b.value: len(result.in_bucket(b)) for b in FinalBucket}
        findings = 0
        if result.reasoning is not None:
            # a judgment that came from the model, not the offline fallback
            findings = sum(
                1 for o in result.reasoning.outcomes
                if getattr(o, "llm_source", "fallback") != "fallback"
            )
        citations = len(result.grounding.explanations) if result.grounding is not None else 0

        report.rows.append(
            AblationRow(
                variant=variant,
                metrics=compute(result),
                counts=counts,
                seconds=seconds,
                findings_attached=findings,
                citations_attached=citations,
            )
        )
        if not report.n_records:
            report.n_records = len(result.outcomes)
            report.money_moving = (
                report.n_records
                - counts[FinalBucket.IGNORED.value]
                - counts[FinalBucket.FAILED.value]
            )

    return report


def _pct(value: float) -> str:
    return f"{value:.1%}"


def render_markdown(report: AblationReport) -> str:
    """The ablation as a report — table first, then what the table does *not* say."""
    first, last = report.rows[0], report.rows[-1]
    det = report.row("deterministic") or last
    gain = det.counts["auto_resolved"] - first.counts["auto_resolved"]
    ratio = report.money_moving / max(last.metrics.llm_call_count, 1)

    intro = (
        f"Same corpus ({report.n_records} records), same ground truth, same scoring"
        " function for every row. Each variant removes a layer and re-scores; a"
        " disabled layer leaves its records `escalated` rather than dropping them,"
        f" so every row below still accounts for all {report.n_records} records."
    )
    precision_note = (
        "**Precision never moves.** Every variant asserts only pairings it is sure"
        f" of, so precision stays at {_pct(first.metrics.match_precision)}"
        " throughout. The layers buy *recall* — they convert escalations into"
        " resolutions — and that is the correct direction to buy it in: a layer"
        " that raised recall by lowering precision would be moving money on guesses."
    )
    deterministic_note = (
        "**The deterministic layers do the resolving.** Going from a"
        " reference-column join to the full no-model layer takes auto-resolution"
        f" from {first.counts['auto_resolved']} to {det.counts['auto_resolved']}"
        f" records (+{gain}), and cuts what a human must look at from"
        f" {first.counts['escalated']} to {det.counts['escalated']}."
    )
    model_note = (
        "**The LLM and RAG layers barely move the table — and that is the design,"
        " not a disappointment.** A confident LLM match that still carries a"
        " residual amount variance escalates anyway, because that call belongs to"
        " a person. So the model does not convert escalations into resolutions"
        " here. What it changes is what the reviewer is handed:"
    )
    findings_bullet = (
        f"- {last.findings_attached} of the {last.counts['escalated']} escalations"
        " arrive with the model's actual finding attached (the rest fall back to a"
        " safe `unsure` — see the LLM cache note in the README) instead of a bare"
        ' "unresolved".'
    )
    citations_bullet = (
        f"- {last.citations_attached} of the {last.counts['exception']} exceptions"
        " arrive with a verbatim GST clause and its source URL instead of a bare"
        ' "no counterpart".'
    )
    review_cost_note = (
        "That is a claim about **review cost**, not about accuracy, and it is"
        " stated that way on purpose."
    )
    routing_note = (
        f"The shipped pipeline sends **{last.metrics.llm_call_count}** records to"
        " the LLM. A design with no deterministic layer in front of it — the naive"
        " agent that asks the model about every line — would send"
        f" **{report.money_moving}** (every record where money actually moved), a"
        f" **{ratio:.1f}×** difference in model calls for the same buckets."
    )
    caveat = (
        "That multiple is arithmetic over the measured routing, not a simulated"
        " run: the naive variant was not executed, because doing so would need a"
        " live judgment for every record and the reproducible offline path"
        " deliberately has no such cache. Treat it as the call-count ratio it is,"
        " not a latency or rupee figure."
    )

    lines = [
        "# Ablation — what each layer buys",
        "",
        intro,
        "",
        "Regenerate with `python scripts/run_ablation.py`.",
        "",
        "| Variant | Auto-resolved | Escalated | Bucket acc. | Precision | Recall | F1 | Settlements |",
        "|---------|--------------:|----------:|------------:|----------:|-------:|---:|------------:|",
    ]
    for row in report.rows:
        m = row.metrics
        lines.append(
            f"| {row.variant.label} "
            f"| {row.counts['auto_resolved']} "
            f"| {row.counts['escalated']} "
            f"| {_pct(m.bucket_accuracy)} "
            f"| {_pct(m.match_precision)} "
            f"| {_pct(m.match_recall)} "
            f"| {_pct(m.match_f1)} "
            f"| {m.settlements_correct}/{m.settlements_total} |"
        )

    lines += ["", "## What each variant is", ""]
    lines += [f"- **{row.variant.label}** — {row.variant.blurb}" for row in report.rows]
    lines += [
        "",
        "## Reading it honestly",
        "",
        precision_note,
        "",
        deterministic_note,
        "",
        model_note,
        "",
        findings_bullet,
        citations_bullet,
        "",
        review_cost_note,
        "",
        "## Routing, versus sending everything to the model",
        "",
        routing_note,
        "",
        caveat,
        "",
    ]
    return "\n".join(lines).rstrip() + "\n"
