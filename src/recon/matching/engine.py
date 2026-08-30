"""The deterministic matching layer — orchestrates exact + fuzzy + settlement.

``reconcile`` takes the ingest output and produces a :class:`MatchReport` that
buckets every money-moving record. It logs one audit entry per decision, so the
trail on its own explains why each record ended where it did.

Order of operations
-------------------
1. **Filter** — failed gateway payments (no money moved) → ``ignored``.
2. **Exact** — invoice ↔ gateway on ref + amount + currency + day.
3. **Fuzzy** — the leftovers, greedy mutual-best assignment with a margin gate.
   A pair with a real amount/currency disagreement can only reach
   ``unmatched-ambiguous``, never ``matched`` — that is what routes partial
   captures, FX slips and duplicate references to the LLM.
4. **Settlement (N:1)** — each bank settlement credit against the batch of
   gateway payments that should have settled on that day (subset-sum, so a batch
   with one held-back payment still reconciles).
5. **Bank exceptions** — refund debits and bank charges have no counterpart and
   go straight to RAG.

This is the accuracy baseline: whatever it can't resolve confidently is handed
on, never guessed.
"""

from __future__ import annotations

from collections import defaultdict
from datetime import timedelta
from decimal import Decimal
from itertools import combinations

from config import settings
from recon.audit import AuditLogger
from recon.ingest import CanonicalTxn, IngestResult, Source

from .exact import exact_pairs
from .fuzzy import candidates_for, net_of_fees, score_pair
from .types import ALL_STAGES, Bucket, Candidate, MatchDecision, MatchReport, MatchStages

SETTLE_LAG = timedelta(days=2)
_SUBSET_MAX = 14  # groups larger than this only get the all-members check


def reconcile(
    ingest: IngestResult,
    audit: AuditLogger | None = None,
    stages: MatchStages = ALL_STAGES,
) -> MatchReport:
    """Bucket every money-moving record. ``stages`` is for the ablation only —
    the default runs the whole layer, which is what the pipeline always uses."""
    # note: `audit or ...` would be wrong — an empty AuditLogger is falsy (__len__)
    if audit is None:
        audit = AuditLogger(path=None)
    report = MatchReport()

    gateway = ingest.by_source(Source.GATEWAY)
    invoice = ingest.by_source(Source.INVOICE)
    bank = ingest.by_source(Source.BANK)

    live_gateway = _filter_dead(gateway, report, audit)
    _match_invoice_gateway(invoice, live_gateway, report, audit, stages)
    _match_settlements(
        [b for b in bank if b.status == "settlement"], live_gateway, report, audit, stages
    )
    _bank_exceptions([b for b in bank if b.status != "settlement"], report, audit)
    return report


# --- step 1: filter -------------------------------------------------------


def _filter_dead(
    gateway: list[CanonicalTxn], report: MatchReport, audit: AuditLogger
) -> list[CanonicalTxn]:
    live: list[CanonicalTxn] = []
    for txn in gateway:
        if txn.moved_money:
            live.append(txn)
            continue
        decision = MatchDecision(
            record_id=txn.txn_id,
            source=str(txn.source),
            bucket=Bucket.IGNORED,
            method="filter",
            confidence=1.0,
            rationale=f"status={txn.status!r}: no money moved, excluded from matching",
        )
        _record(decision, report, audit, inputs={"status": txn.status})
    return live


# --- steps 2 & 3: invoice <-> gateway ----------------------------------


def _match_invoice_gateway(
    invoices: list[CanonicalTxn],
    gateways: list[CanonicalTxn],
    report: MatchReport,
    audit: AuditLogger,
    stages: MatchStages = ALL_STAGES,
) -> None:
    if stages.exact:
        pairs, inv_left, gw_left = exact_pairs(invoices, gateways)
    else:
        pairs, inv_left, gw_left = [], list(invoices), list(gateways)
    for inv, gw in pairs:
        for record, other in ((inv, gw), (gw, inv)):
            decision = MatchDecision(
                record_id=record.txn_id,
                source=str(record.source),
                bucket=Bucket.MATCHED,
                method="exact",
                confidence=1.0,
                matched_to=(other.txn_id,),
                rationale="exact agreement on reference, amount, currency and day",
            )
            _record(decision, report, audit, inputs={"counterpart": other.txn_id})

    if stages.fuzzy:
        _fuzzy_assign(inv_left, gw_left, report, audit)
    else:
        _unresolved(inv_left + gw_left, report, audit, "fuzzy matching disabled")


def _margin(cands: list[Candidate]) -> float:
    if len(cands) < 2:
        return 1.0
    return cands[0].score.total - cands[1].score.total


def _fuzzy_assign(
    invoices: list[CanonicalTxn],
    gateways: list[CanonicalTxn],
    report: MatchReport,
    audit: AuditLogger,
) -> None:
    inv_cands = {inv.txn_id: candidates_for(inv, gateways) for inv in invoices}
    gw_cands = {gw.txn_id: candidates_for(gw, invoices) for gw in gateways}

    triples = sorted(
        (
            (cand.score.total, inv_id, cand.txn_id)
            for inv_id, cands in inv_cands.items()
            for cand in cands
        ),
        reverse=True,
    )

    assigned: dict[str, str] = {}  # txn_id -> counterpart txn_id, both directions
    for score, inv_id, gw_id in triples:
        if score < settings.MATCH_CONFIDENT:
            break
        if inv_id in assigned or gw_id in assigned:
            continue
        if _margin(inv_cands[inv_id]) < settings.MATCH_MARGIN:
            continue
        if _margin(gw_cands[gw_id]) < settings.MATCH_MARGIN:
            continue
        if inv_cands[inv_id][0].txn_id != gw_id or gw_cands[gw_id][0].txn_id != inv_id:
            continue  # not a mutual first choice
        assigned[inv_id] = gw_id
        assigned[gw_id] = inv_id

    for record in invoices + gateways:
        cands = (inv_cands if record.source is Source.INVOICE else gw_cands)[record.txn_id]
        if record.txn_id in assigned:
            best = cands[0]
            decision = MatchDecision(
                record_id=record.txn_id,
                source=str(record.source),
                bucket=Bucket.MATCHED,
                method="fuzzy",
                confidence=best.score.total,
                matched_to=(assigned[record.txn_id],),
                candidates=tuple(cands),
                rationale=_fuzzy_reason(best),
            )
        elif cands:
            decision = MatchDecision(
                record_id=record.txn_id,
                source=str(record.source),
                bucket=Bucket.AMBIGUOUS,
                method="fuzzy",
                confidence=cands[0].score.total,
                candidates=tuple(cands),
                rationale=_ambiguous_reason(cands),
            )
        else:
            decision = MatchDecision(
                record_id=record.txn_id,
                source=str(record.source),
                bucket=Bucket.EXCEPTION,
                method="no-candidate",
                confidence=0.0,
                rationale="no counterpart scored above the candidate floor",
            )
        _record(decision, report, audit, inputs=decision.as_audit_inputs())


def _fuzzy_reason(best: Candidate) -> str:
    s = best.score
    bits = [f"amount={s.amount:.2f}", f"date={s.date:.2f}", f"ref={s.ref:.0f}"]
    if s.name is not None:
        bits.append(f"name={s.name:.2f}")
    return f"fuzzy match to {best.txn_id} (" + ", ".join(bits) + ")"


def _ambiguous_reason(cands: list[Candidate]) -> str:
    top = cands[0].score
    if not top.same_currency:
        return f"best candidate {cands[0].txn_id} is a different currency — needs review"
    if top.amount < 0.5:
        return f"best candidate {cands[0].txn_id} matches on reference/date but not amount"
    if len(cands) > 1 and _margin(cands) < settings.MATCH_MARGIN:
        return f"{len(cands)} near-equal candidates ({', '.join(c.txn_id for c in cands[:3])})"
    return f"best candidate {cands[0].txn_id} scored {top.total:.2f}, below the confident threshold"


# --- step 4: settlements (N:1) ---------------------------------------


def _match_settlements(
    settlements: list[CanonicalTxn],
    gateways: list[CanonicalTxn],
    report: MatchReport,
    audit: AuditLogger,
    stages: MatchStages = ALL_STAGES,
) -> None:
    if not stages.settlement:
        _unresolved(settlements, report, audit, "N:1 settlement matching disabled")
        return
    by_date: dict[object, list[CanonicalTxn]] = defaultdict(list)
    for gw in gateways:
        if gw.currency == "INR":
            by_date[gw.value_date + SETTLE_LAG].append(gw)

    tol = settings.SETTLEMENT_ABS_TOLERANCE
    for credit in settlements:
        pool = by_date.get(credit.value_date, [])
        subset = _find_settling_subset(credit.amount, pool, tol)
        if subset is not None:
            net = net_of_fees(subset)
            decision = MatchDecision(
                record_id=credit.txn_id,
                source="bank",
                bucket=Bucket.MATCHED,
                method="settlement-group",
                confidence=1.0,
                matched_to=tuple(sorted(g.txn_id for g in subset)),
                rationale=(
                    f"batch of {len(subset)} payment(s) on "
                    f"{credit.value_date - SETTLE_LAG} nets to {net} (fees deducted)"
                ),
            )
            _record(decision, report, audit, inputs={"members": len(subset), "net": str(net)})
            continue

        decision = MatchDecision(
            record_id=credit.txn_id,
            source="bank",
            bucket=Bucket.AMBIGUOUS,
            method="settlement-group",
            confidence=0.0,
            candidates=tuple(
                Candidate(g.txn_id, "gateway", score_pair(credit, g)) for g in pool[:8]
            ),
            rationale=(
                f"no subset of the {len(pool)} payment(s) dated "
                f"{credit.value_date - SETTLE_LAG} nets to the credit {credit.amount}"
            ),
        )
        _record(decision, report, audit, inputs={"pool_size": len(pool)})


def _find_settling_subset(
    target: Decimal, pool: list[CanonicalTxn], abs_tol: Decimal
) -> list[CanonicalTxn] | None:
    """Smallest-exclusion subset of ``pool`` whose net-of-fees matches ``target``.

    Fast path: the whole pool. Otherwise drop 1, then 2, … members (a held-back
    payment is the usual reason a batch doesn't foot). Returns ``None`` if no
    subset matches or if more than one distinct subset does (ambiguous).
    """

    def close(subset: list[CanonicalTxn]) -> bool:
        return bool(subset) and abs(net_of_fees(subset) - target) <= abs_tol

    if close(pool):
        return pool
    if not pool or len(pool) > _SUBSET_MAX:
        return None

    for drop in range(1, len(pool)):
        hits = [
            [t for t in pool if t not in excluded]
            for excluded in combinations(pool, drop)
            if close([t for t in pool if t not in excluded])
        ]
        # dedupe by the frozenset of member ids
        unique = {frozenset(t.txn_id for t in h): h for h in hits}
        if len(unique) == 1:
            return next(iter(unique.values()))
        if len(unique) > 1:
            return None
    return None


def _unresolved(
    records: list[CanonicalTxn], report: MatchReport, audit: AuditLogger, why: str
) -> None:
    """Records a disabled layer would have handled — ambiguous, never dropped.

    Ablation-only path. Routing them to ``unmatched-ambiguous`` is the honest
    reading of "this layer is missing": nothing is resolved and nothing is
    discarded, so a person (or the LLM stage) still has to settle each one.
    """
    for txn in records:
        decision = MatchDecision(
            record_id=txn.txn_id,
            source=str(txn.source),
            bucket=Bucket.AMBIGUOUS,
            method="stage-disabled",
            confidence=0.0,
            rationale=f"{why} — left unresolved for review",
        )
        _record(decision, report, audit, inputs={"disabled": why})


# --- step 5: bank exceptions ---------------------------------------


def _bank_exceptions(rows: list[CanonicalTxn], report: MatchReport, audit: AuditLogger) -> None:
    for txn in rows:
        why = {
            "refund": "refund debit — no invoice counterpart; needs a GST credit-note explanation",
            "charge": "bank charge — no counterpart in any source; needs expense/ITC treatment",
        }.get(txn.status, "no counterpart in any source")
        decision = MatchDecision(
            record_id=txn.txn_id,
            source="bank",
            bucket=Bucket.EXCEPTION,
            method="no-candidate",
            confidence=1.0,
            rationale=why,
        )
        _record(
            decision,
            report,
            audit,
            inputs={"status": txn.status, "narration": txn.raw.get("narration", "")},
        )


# --- shared -------------------------------------------------------


_DECISION_WORD = {
    Bucket.MATCHED: "matched",
    Bucket.AMBIGUOUS: "escalated-to-llm",
    Bucket.EXCEPTION: "escalated-to-rag",
    Bucket.IGNORED: "ignored",
}


def _record(
    decision: MatchDecision, report: MatchReport, audit: AuditLogger, *, inputs: dict
) -> None:
    report.decisions.append(decision)
    audit.log(
        record_id=decision.record_id,
        stage="match",
        decision=_DECISION_WORD[decision.bucket],
        confidence=decision.confidence,
        source=f"deterministic:{decision.method}",
        matched_to=list(decision.matched_to),
        inputs=inputs,
        rationale=decision.rationale,
    )
