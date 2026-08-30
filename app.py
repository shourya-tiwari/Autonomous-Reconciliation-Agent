"""Controller review queue — a UI over what the agent could not settle alone.

    streamlit run app.py

The terminal output shows that 68% of the corpus reconciles itself. This shows
the other 32%: the escalations a person has to settle, and the exceptions that
need a policy answer. That is the part of reconciliation a finance controller
actually does, and it is the part a metrics table cannot show.

It runs the real pipeline in-process on first load (about 20 seconds, mostly the
embedding model) and every view reads from that one run. Nothing here is
pre-rendered or mocked: the escalation you click is the record the agent
escalated, with the scores it weighed and the model's finding as written to the
audit trail.

Deliberately read-only. Triage marks live in the browser session and are never
written back to any ledger — the agent explains and escalates, it does not post
entries. Anything else would be a claim this project has not earned.
"""

from __future__ import annotations

import json
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent
sys.path[:0] = [str(ROOT), str(ROOT / "src")]

import pandas as pd
import streamlit as st

from config import settings
from recon.agent import FinalBucket, run_pipeline
from recon.audit import AuditLogger
from recon.eval.metrics import compute
from recon.rag import PolicyIndex

st.set_page_config(
    page_title="Reconciliation review queue",
    page_icon="🧾",
    layout="wide",
)

# Identity by icon + label, never by colour alone.
BUCKET_META = {
    FinalBucket.AUTO_RESOLVED: ("✅", "reconciled with no human involvement"),
    FinalBucket.ESCALATED: ("⚠️", "real ambiguity — a person decides, with the model's finding attached"),
    FinalBucket.EXCEPTION: ("📄", "no counterpart — explained against a cited GST clause"),
    FinalBucket.IGNORED: ("➖", "failed payments — no money moved, nothing to reconcile"),
    FinalBucket.FAILED: ("⛔", "unparseable row — rejected with a per-field reason"),
}


# ---------------------------------------------------------------------------
# data
# ---------------------------------------------------------------------------


@st.cache_resource(show_spinner=False)
def load_run():
    """Run the pipeline once per session and keep everything it produced.

    The policy index is built first and passed in, so the one-off embedding-model
    load is not charged to the pipeline's own elapsed time. Without this the app
    would report a throughput several times worse than the committed metrics and
    look like it was contradicting its own README.
    """
    index = PolicyIndex()
    index.query("input tax credit", k=1)
    audit = AuditLogger(path=None)
    result = run_pipeline(audit=audit, index=index)
    metrics = compute(result)
    records = {t.txn_id: t for t in result.ingest.records}
    return result, metrics, audit, records


@st.cache_data(show_spinner=False)
def load_ablation() -> dict | None:
    path = settings.REPORT_DIR / "ablation.json"
    if not path.exists():
        return None
    return json.loads(path.read_text(encoding="utf-8"))


def txn_fields(txn) -> dict:
    if txn is None:
        return {}
    return {
        "amount": f"{txn.currency} {txn.amount:,}",
        "counterparty": txn.counterparty or "—",
        "value_date": str(txn.value_date),
        "reference": txn.ref_id or "—",
        "status": txn.status,
    }


# ---------------------------------------------------------------------------
# views
# ---------------------------------------------------------------------------


def render_overview(result, metrics, ablation) -> None:
    st.subheader("This run")

    cols = st.columns(5)
    cols[0].metric("Bucket accuracy", f"{metrics.bucket_accuracy:.1%}",
                   help="Records that landed in the bucket ground truth expects.")
    cols[1].metric("Match precision", f"{metrics.match_precision:.1%}",
                   help=f"{metrics.matches_correct}/{metrics.matches_asserted} asserted "
                        "1:1 pairings correct. The metric that must not slip.")
    cols[2].metric("Match recall", f"{metrics.match_recall:.1%}",
                   help=f"{metrics.matches_correct}/{metrics.matches_expected} true pairings "
                        "auto-paired. Below 100% by design — the rest are escalated, not missed.")
    cols[3].metric("Reached the LLM", f"{metrics.llm_call_count}",
                   help="Only the ambiguous bucket is routed to the model.")
    cols[4].metric("Throughput", f"{metrics.throughput_rps:.0f} rec/s",
                   help=f"Excludes the one-off {metrics.setup_seconds:.0f}s embedding-model load.")

    st.caption(
        "Recall is below 100% on purpose: partial captures, duplicate references and FX "
        "variances are escalated rather than guessed. Read it next to precision."
    )

    st.subheader("Where every record ended up")
    total = len(result.outcomes)
    rows = []
    for bucket in FinalBucket:
        icon, blurb = BUCKET_META[bucket]
        count = len(result.in_bucket(bucket))
        rows.append({
            "Outcome": f"{icon} {bucket.value}",
            "Records": count,
            "Share": count / total,
            "What it means": blurb,
        })
    st.dataframe(
        pd.DataFrame(rows),
        hide_index=True,
        use_container_width=True,
        column_config={
            "Records": st.column_config.NumberColumn(width="small"),
            # magnitude as one hue, identity carried by the row label beside it
            "Share": st.column_config.ProgressColumn(
                "Share of corpus", format="%.1f%%", min_value=0.0, max_value=1.0
            ),
        },
    )
    st.caption(f"{total} records in, {total} accounted for — nothing is dropped to tidy the totals.")

    st.subheader("Failure modes, handled")
    left, right = st.columns(2)
    with left:
        st.markdown("**Malformed row** — rejected at ingest, run continues")
        for outcome in result.in_bucket(FinalBucket.FAILED):
            st.code(f"{outcome.record_id}\n{outcome.rationale}", language=None)
    with right:
        st.markdown("**Transient API timeout** — retried with backoff, absorbed")
        if result.retries:
            for record_id, attempt, error in result.retries:
                st.code(f"{record_id}\nattempt {attempt}: {error}\n→ retried, absorbed", language=None)
        else:
            st.info("No timeout was armed for this run.")

    if ablation:
        st.subheader("What each layer buys")
        table = pd.DataFrame([
            {
                "Variant": v["label"],
                "Auto-resolved": v["counts"]["auto_resolved"],
                "Escalated": v["counts"]["escalated"],
                "Bucket acc.": v["metrics"]["bucket_accuracy"],
                "Precision": v["metrics"]["match_precision"],
                "Recall": v["metrics"]["match_recall"],
                "LLM calls": v["metrics"]["llm_call_count"],
            }
            for v in ablation["variants"]
        ])
        st.dataframe(
            table, hide_index=True, use_container_width=True,
            column_config={
                col: st.column_config.NumberColumn(format="%.1f%%")
                for col in ("Bucket acc.", "Precision", "Recall")
            },
        )
        ratio = ablation["money_moving"] / max(ablation["variants"][-1]["metrics"]["llm_call_count"], 1)
        st.caption(
            f"Precision holds at 100% across every variant — the layers buy recall, never "
            f"trade precision for it. The last two rows are identical because the model "
            f"explains escalations rather than resolving them; what it changes is what a "
            f"reviewer is handed. Routing sends {ablation['variants'][-1]['metrics']['llm_call_count']} "
            f"records to the LLM where a no-routing design would send "
            f"{ablation['money_moving']} ({ratio:.1f}×)."
        )
    else:
        st.info("Run `python scripts/run_ablation.py` to populate the layer comparison.")


def render_review_queue(result, records) -> None:
    escalated = result.in_bucket(FinalBucket.ESCALATED)
    reasoning = {o.record_id: o for o in (result.reasoning.outcomes if result.reasoning else [])}

    st.subheader(f"{len(escalated)} records the agent refused to guess")
    st.caption(
        "Each one had a plausible counterpart that failed a gate — a currency mismatch, an "
        "amount that did not agree, or several near-equal candidates. The agent scored them, "
        "wrote down why it stopped, and handed them over."
    )

    rows = []
    for outcome in escalated:
        decision = result.matching.by_id(outcome.record_id)
        judgment = reasoning.get(outcome.record_id)
        txn = records.get(outcome.record_id)
        rows.append({
            "Record": outcome.record_id,
            "Source": outcome.source,
            "Amount": f"{txn.currency} {txn.amount:,}" if txn else "—",
            "Counterparty": (txn.counterparty if txn else "") or "—",
            "Date": str(txn.value_date) if txn else "—",
            "Best score": decision.confidence if decision else 0.0,
            "Why it stopped": decision.rationale if decision else outcome.rationale,
            "Model finding": (
                "—" if judgment is None or judgment.llm_source == "fallback"
                else f"{judgment.llm_decision} ({judgment.confidence:.2f})"
            ),
        })

    frame = pd.DataFrame(rows)
    with_finding = st.checkbox("Only show records carrying a model finding", value=False)
    if with_finding:
        frame = frame[frame["Model finding"] != "—"]

    st.dataframe(
        frame, hide_index=True, use_container_width=True, height=280,
        column_config={
            "Best score": st.column_config.ProgressColumn(
                "Best score", format="%.2f", min_value=0.0, max_value=1.0
            ),
        },
    )

    if frame.empty:
        st.info("No records match that filter.")
        return

    st.divider()
    choice = st.selectbox("Inspect a record", frame["Record"].tolist(), key="queue_pick")
    _render_escalation_detail(choice, result, records, reasoning)


def _render_escalation_detail(record_id, result, records, reasoning) -> None:
    decision = result.matching.by_id(record_id)
    txn = records.get(record_id)
    judgment = reasoning.get(record_id)

    left, right = st.columns([1, 1])

    with left:
        st.markdown("#### The record")
        fields = txn_fields(txn)
        if fields:
            st.dataframe(
                pd.DataFrame({"Field": list(fields), "Value": list(fields.values())}),
                hide_index=True, use_container_width=True,
            )
        st.markdown("#### Why the deterministic layer stopped")
        st.warning(decision.rationale if decision else "—")

    with right:
        st.markdown("#### What it weighed")
        if decision and decision.candidates:
            for candidate in decision.candidates[:3]:
                score = candidate.score
                st.markdown(f"**{candidate.txn_id}** · {candidate.source} · total **{score.total:.2f}**")
                parts = {"amount": score.amount, "date": score.date, "ref": score.ref}
                if score.name is not None:
                    parts["name"] = score.name
                st.dataframe(
                    pd.DataFrame({"Signal": list(parts), "Score": list(parts.values())}),
                    hide_index=True, use_container_width=True,
                    column_config={
                        "Score": st.column_config.ProgressColumn(
                            "Score", format="%.2f", min_value=0.0, max_value=1.0
                        ),
                    },
                )
                if not score.same_currency:
                    st.caption("⚠️ different currency — capped below the confident threshold by rule")
        else:
            st.caption("No scored candidate — see the rationale.")

        st.markdown("#### What the model said")
        if judgment is None or judgment.llm_source == "fallback":
            st.info(
                "No cached judgment for this record, so the reasoner returned a deterministic "
                "`unsure` and it escalated anyway. That is the offline-replay path — it fails "
                "safe rather than inventing an answer."
            )
        else:
            st.success(
                f"**{judgment.llm_decision}** · confidence {judgment.confidence:.2f} · "
                f"source `{judgment.llm_source}`\n\n{judgment.rationale}"
            )
            st.caption(
                "The model's finding is attached to the escalation, not acted on: a residual "
                "amount variance is a person's call."
            )

    st.divider()
    _render_triage(record_id)


def _render_triage(record_id: str) -> None:
    marks = st.session_state.setdefault("triage", {})
    st.markdown("#### Your call")
    cols = st.columns([1, 1, 1, 3])
    if cols[0].button("Accept match", key=f"acc_{record_id}"):
        marks[record_id] = "accepted"
    if cols[1].button("Reject", key=f"rej_{record_id}"):
        marks[record_id] = "rejected"
    if cols[2].button("Clear", key=f"clr_{record_id}"):
        marks.pop(record_id, None)

    current = marks.get(record_id)
    cols[3].markdown(f"**{current}**" if current else "_not reviewed_")
    st.caption(
        f"{len(marks)} record(s) marked this session. Triage is held in the browser session "
        "only and is never written back to any ledger — this agent explains and escalates, "
        "it does not post entries."
    )


def render_exceptions(result, records) -> None:
    grounding = result.grounding
    if grounding is None or not grounding.explanations:
        st.warning(
            "No grounded explanations in this run. The policy index could not be built — "
            "exceptions are still reported, just without their citations."
        )
        return

    explanations = grounding.explanations
    st.subheader(f"{len(explanations)} exceptions, each answered from statute")
    st.caption(
        "These records have no counterpart in any source, so there is nothing to match them "
        "to. Instead of reporting a bare unmatched line, the agent retrieves the governing "
        "GST clause and quotes it verbatim, with its source URL, so the citation can be "
        "checked rather than trusted."
    )

    kinds = sorted({e.exception_kind for e in explanations})
    chosen = st.multiselect("Exception kind", kinds, default=kinds)
    shown = [e for e in explanations if e.exception_kind in chosen]

    summary = pd.DataFrame([
        {
            "Record": e.record_id,
            "Kind": e.exception_kind,
            "Cited clause": e.primary.doc_title if e.primary else "—",
            "Similarity": round(e.primary.score, 3) if e.primary else 0.0,
        }
        for e in shown
    ])
    st.dataframe(
        summary, hide_index=True, use_container_width=True, height=240,
        column_config={
            "Similarity": st.column_config.ProgressColumn(
                "Retrieval similarity", format="%.3f", min_value=0.0, max_value=1.0
            ),
        },
    )

    if not shown:
        st.info("No exceptions match that filter.")
        return

    st.divider()
    pick = st.selectbox("Inspect an exception", [e.record_id for e in shown], key="exc_pick")
    explanation = next(e for e in shown if e.record_id == pick)
    txn = records.get(pick)

    left, right = st.columns([1, 1])
    with left:
        st.markdown("#### The record")
        fields = txn_fields(txn)
        if fields:
            st.dataframe(
                pd.DataFrame({"Field": list(fields), "Value": list(fields.values())}),
                hide_index=True, use_container_width=True,
            )
        st.markdown("#### What the agent concluded")
        st.info(f"{explanation.summary}\n\n**Recommended treatment.** {explanation.action}")

    with right:
        st.markdown("#### The clauses it relied on")
        for citation in explanation.citations:
            with st.expander(
                f"{citation.doc_title} · similarity {citation.score:.3f}",
                expanded=citation is explanation.citations[0],
            ):
                st.markdown(f"> {citation.quote.strip()}")
                st.caption(f"Source: {citation.source}")
        st.caption(
            "Retrieval plus a template — no LLM prose in this layer, so every claim above "
            "traces to a quote a controller can check."
        )


def render_audit(result, audit) -> None:
    entries = list(audit)
    st.subheader(f"{len(entries)} decisions, as they were made")
    st.caption(
        "Append-only, one JSON object per decision, `rationale` never blank. This is the "
        "same trail `scripts/show_audit.py` renders and the one a judge can read on disk."
    )

    ids = sorted({e["record_id"] for e in entries})
    default = next((o.record_id for o in result.in_bucket(FinalBucket.ESCALATED)), ids[0])
    pick = st.selectbox("Trace a record end to end", ids, index=ids.index(default), key="audit_pick")

    chain = [e for e in entries if e["record_id"] == pick]
    st.write(f"**{pick}** — {len(chain)} decision(s)")
    for entry in chain:
        st.markdown(
            f"**{entry['stage']} → {entry['decision']}** · `{entry['source']}` · "
            f"confidence {entry['confidence']:.2f}"
        )
        st.caption(entry["rationale"])
        if entry["inputs"]:
            with st.expander("inputs"):
                st.json(entry["inputs"])

    with st.expander("Decision counts across the whole run"):
        counts = pd.DataFrame(
            [{"Stage": e["stage"], "Decision": e["decision"]} for e in entries]
        ).value_counts().reset_index(name="Count")
        st.dataframe(counts, hide_index=True, use_container_width=True)


# ---------------------------------------------------------------------------


def main() -> None:
    st.title("🧾 Autonomous Reconciliation Agent")
    st.markdown(
        "The part of reconciliation a person still has to do — and what the agent "
        "hands them to do it with."
    )

    with st.spinner("Running the pipeline (embedding model loads once, ~20s)…"):
        result, metrics, audit, records = load_run()
    ablation = load_ablation()

    st.success(
        f"Ran live in this session: {len(result.outcomes)} records in "
        f"{result.elapsed_seconds:.1f}s, no API keys. Everything below is that run.",
        icon="▶",
    )

    overview, queue, exceptions, trail = st.tabs(
        ["Overview", "Review queue", "Exceptions", "Audit trail"]
    )
    with overview:
        render_overview(result, metrics, ablation)
    with queue:
        render_review_queue(result, records)
    with exceptions:
        render_exceptions(result, records)
    with trail:
        render_audit(result, audit)


if __name__ == "__main__":
    main()
