"""Tests for recon.rag — retrieval quality and grounded, cited explanations.

Runs against the committed ChromaDB index in data/rag_index/. Building the index
needs the embedding model (downloaded once); querying an already-built index does
too, so these tests are skipped if sentence-transformers can't load the model
offline.
"""

from __future__ import annotations

from datetime import date
from decimal import Decimal

import pytest

from recon.audit import AuditLogger
from recon.ingest import CanonicalTxn, Direction, Source, load_all
from recon.matching import Bucket, reconcile
from recon.rag import PolicyIndex, load_chunks, run_grounding
from recon.rag.ground import _PLAYBOOK, ground_exception


@pytest.fixture(scope="module")
def index() -> PolicyIndex:
    idx = PolicyIndex()
    if not idx.is_built():
        pytest.skip("RAG index not built — run scripts/build_rag_index.py")
    try:
        idx.query("input tax credit", k=1)  # forces the embedder to load
    except Exception as exc:  # noqa: BLE001 - offline model load failure
        pytest.skip(f"embedding model unavailable offline: {exc}")
    return idx


def _bank_txn(txn_id: str, status: str, narration: str) -> CanonicalTxn:
    return CanonicalTxn(
        txn_id=txn_id,
        source=Source.BANK,
        ref_id=None,
        amount=Decimal("2500.00"),
        currency="INR",
        value_date=date(2026, 6, 1),
        counterparty=None,
        direction=Direction.OUTFLOW,
        status=status,
        raw={"narration": narration},
    )


# --- corpus & chunking ------------------------------------------------


def test_corpus_has_the_expected_documents():
    slugs = {c.doc_slug for c in load_chunks()}
    assert {"cgst-act-s34", "cgst-act-s16", "cgst-act-s17", "cgst-rules-r38"} <= slugs
    assert len(slugs) >= 8  # "5-10 real policy documents"


def test_chunks_are_reasonably_sized_and_attributed():
    chunks = load_chunks()
    assert len(chunks) > 30
    for c in chunks:
        assert c.text.strip()
        assert len(c.text) <= 1200  # MAX_CHARS + overlap slack
        assert c.doc_title and c.source.startswith("http")


# --- retrieval quality (the DoD gate) --------------------------------


def test_refund_query_retrieves_the_credit_note_section(index):
    slugs = [h.chunk.doc_slug for h in index.query(_PLAYBOOK["refund"][0], k=3)]
    assert "cgst-act-s34" in slugs


def test_charge_query_retrieves_an_itc_clause(index):
    slugs = [h.chunk.doc_slug for h in index.query(_PLAYBOOK["charge"][0], k=3)]
    assert {"cgst-act-s16", "cgst-act-s17", "cgst-rules-r38"} & set(slugs)


def test_scores_are_similarities_in_range_and_ordered(index):
    hits = index.query(_PLAYBOOK["refund"][0], k=4)
    assert all(0.0 <= h.score <= 1.0 for h in hits)
    assert [h.score for h in hits] == sorted((h.score for h in hits), reverse=True)


# --- grounded explanations ------------------------------------------


def test_refund_exception_is_grounded_in_section_34(index):
    exp = ground_exception(_bank_txn("b1", "refund", "RAZORPAY REFUND pay_x"), index)
    assert exp.exception_kind == "refund"
    assert exp.primary is not None
    assert "credit and debit notes" in exp.primary.doc_title.lower()
    assert exp.primary.quote  # a real verbatim snippet
    assert "credit note" in exp.action.lower()


def test_bank_charge_exception_is_grounded_in_an_itc_clause(index):
    exp = ground_exception(_bank_txn("b2", "charge", "ACCT MAINTENANCE CHARGE"), index)
    assert exp.exception_kind == "charge"
    assert exp.primary is not None
    assert "input tax credit" in exp.primary.doc_title.lower()


def test_every_citation_quote_is_verbatim_from_a_real_chunk(index):
    chunk_texts = {c.text.split() and " ".join(c.text.split()) for c in load_chunks()}
    exp = ground_exception(_bank_txn("b3", "refund", "RAZORPAY REFUND pay_y"), index)
    for cite in exp.citations:
        stripped = cite.quote.rstrip("…").strip()
        assert any(stripped[:60] in t for t in chunk_texts), f"quote not found verbatim: {cite.quote[:60]}"


def test_explanation_text_names_amount_and_source(index):
    exp = ground_exception(_bank_txn("b4", "refund", "RAZORPAY REFUND pay_z"), index)
    text = exp.as_text()
    assert "INR 2500.00" in text
    assert "Grounding:" in text
    assert exp.primary.doc_title in text


# --- batch runner over the corpus -----------------------------------


@pytest.fixture(scope="module")
def grounding(index):
    ingest = load_all()
    match_report = reconcile(ingest, AuditLogger(path=None))
    audit = AuditLogger(path=None)
    report = run_grounding(match_report, ingest, audit, index)
    return match_report, report, audit


def test_grounds_exactly_the_exception_bucket(grounding):
    match_report, report, _ = grounding
    assert len(report.explanations) == len(match_report.in_bucket(Bucket.EXCEPTION))


def test_every_exception_gets_at_least_one_citation(grounding):
    _, report, _ = grounding
    assert report.summary()["with_citation"] == report.summary()["total"]
    assert report.summary()["total"] > 40


def test_an_unavailable_policy_store_degrades_instead_of_losing_records():
    """A first, offline run cannot fetch the embedding model. The exceptions must
    still be reported — without a citation — rather than killing the run."""
    from recon.matching import reconcile
    from recon.rag import GroundingReport, run_grounding

    class Broken(PolicyIndex):
        def query(self, text, k=4):
            raise RuntimeError("cannot download embedding model")

    ingest = load_all()
    match_report = reconcile(ingest, AuditLogger(path=None))
    audit = AuditLogger(path=None)
    report: GroundingReport = run_grounding(match_report, ingest, audit, Broken())

    assert len(report.explanations) == len(match_report.in_bucket(Bucket.EXCEPTION))
    assert report.summary()["with_citation"] == 0
    for explanation in report.explanations:
        assert "manual review" in explanation.action
        assert "build_rag_index" in explanation.action  # tells the reader how to fix it
    entries = audit.by_stage("ground")
    assert len(entries) == len(report.explanations)
    assert all(e["decision"] == "no-clause-found" for e in entries)


def test_every_grounding_is_audited_with_the_cited_doc(grounding):
    _, report, audit = grounding
    entries = audit.by_stage("ground")
    assert len(entries) == len(report.explanations)
    for e in entries:
        assert e["decision"] == "grounded"
        assert e["source"].startswith("rag:")
        assert e["inputs"]["citations"]
        assert e["confidence"] > 0
