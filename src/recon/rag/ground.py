"""Grounded explanations for unmatched exception records.

An exception is a bank line with no counterpart in any other source — a refund
debit (no sales invoice) or a bank charge (in no ledger). The pipeline can't
*match* it, so it must *explain* it, and the explanation has to be defensible:
every claim is a verbatim quote from a retrieved GST clause, with the source
named. No free-form LLM prose here — retrieval + a fixed template.

Each exception kind maps to a retrieval query aimed at the clause that governs
it. The output is a short explanation: what the line is, the governing rule
quoted, and the concrete action a controller should take.
"""

from __future__ import annotations

from dataclasses import dataclass, field

from recon.ingest import CanonicalTxn

from .index import PolicyIndex, Retrieved

# exception kind -> (retrieval query, one-line interpretation of what to do).
# The query targets the governing clause; wording is chosen to retrieve the
# right section rather than to read naturally.
_REFUND_QUERY = (
    "supplier issues a credit note to the recipient because goods were returned"
    " or the supply was deficient or the taxable value or tax charged in the invoice"
    " exceeds what is payable; particulars of a credit note; adjustment of output tax"
    " liability in the return"
)
_REFUND_ACTION = (
    "Treat as a customer refund against an earlier sale. A GST credit note must be"
    " issued to the recipient referencing the original invoice, and the output-tax"
    " reduction declared in the return for that month (not later than the following"
    " September / annual return)."
)
_CHARGE_QUERY = (
    "eligibility and conditions for taking input tax credit on inputs and input"
    " services; input tax credit on services supplied by a banking company or"
    " financial institution; a valid tax invoice as a condition for input tax credit"
)
_CHARGE_ACTION = (
    "Treat as a bank charge / financial-service fee: an expense. Its GST component is"
    " input tax credit only if the section-16 conditions are met and the bank has"
    " issued a tax invoice; a banking company itself may instead take the 50% option"
    " under section 17(4) / rule 38."
)
_DEFAULT_QUERY = (
    "particulars a tax invoice must contain; conditions for input tax credit on a"
    " transaction with no matching invoice"
)

_PLAYBOOK: dict[str, tuple[str, str]] = {
    "refund": (_REFUND_QUERY, _REFUND_ACTION),
    "charge": (_CHARGE_QUERY, _CHARGE_ACTION),
}


@dataclass(frozen=True)
class Citation:
    doc_title: str
    source: str
    quote: str
    score: float


@dataclass(frozen=True)
class GroundedExplanation:
    record_id: str
    exception_kind: str
    summary: str
    action: str
    citations: tuple[Citation, ...] = field(default_factory=tuple)

    @property
    def primary(self) -> Citation | None:
        return self.citations[0] if self.citations else None

    def as_text(self) -> str:
        lines = [self.summary, "", f"Action: {self.action}", "", "Grounding:"]
        for c in self.citations:
            lines.append(f'  • {c.doc_title} — "{c.quote}"')
        return "\n".join(lines)

    def as_audit_inputs(self) -> dict:
        return {
            "exception_kind": self.exception_kind,
            "citations": [
                {"doc": c.doc_title, "source": c.source, "score": round(c.score, 3)}
                for c in self.citations
            ],
        }


def _kind(txn: CanonicalTxn) -> str:
    if txn.status in _PLAYBOOK:
        return txn.status
    return "charge"  # any other uncounterparted debit is handled like a charge


def ground_exception(
    txn: CanonicalTxn, index: PolicyIndex, *, k: int = 3
) -> GroundedExplanation:
    kind = _kind(txn)
    query, action = _PLAYBOOK.get(kind, (_DEFAULT_QUERY, "Refer to a tax advisor."))
    hits: list[Retrieved] = index.query(query, k=k)

    narration = str(txn.raw.get("narration", "")).strip()
    summary = (
        f"{txn.currency} {txn.amount} debit on {txn.value_date}"
        f'{f" ({narration})" if narration else ""} has no matching invoice or gateway record.'
    )
    citations = tuple(
        Citation(
            doc_title=h.chunk.doc_title,
            source=h.chunk.source,
            quote=h.quote(),
            score=h.score,
        )
        for h in hits
    )
    return GroundedExplanation(
        record_id=txn.txn_id,
        exception_kind=kind,
        summary=summary,
        action=action,
        citations=citations,
    )
