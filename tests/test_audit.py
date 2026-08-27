"""Tests for recon.audit — the trail is complete, parseable, and self-explaining.

The audit trail is the artifact judges inspect, so the bar is: after a matching
run, every decision is on disk as one JSON line carrying enough context to
explain that decision without re-running anything.
"""

from __future__ import annotations

import json

import pytest

from recon.audit import AuditLogger
from recon.ingest import load_all
from recon.matching import Bucket, reconcile


@pytest.fixture(scope="module")
def ingested():
    return load_all()


# --- the logger in isolation ------------------------------------------


def test_memory_only_logger_writes_no_file(tmp_path):
    log = AuditLogger(path=None)
    log.log(record_id="x", stage="match", decision="matched")
    assert len(log) == 1
    assert not list(tmp_path.iterdir())


def test_entries_get_a_monotonic_sequence():
    log = AuditLogger(path=None)
    for i in range(5):
        log.log(record_id=f"r{i}", stage="match", decision="matched")
    assert [e["seq"] for e in log] == [1, 2, 3, 4, 5]


def test_non_serialisable_inputs_are_coerced():
    from decimal import Decimal

    log = AuditLogger(path=None)
    entry = log.log(
        record_id="x",
        stage="match",
        decision="matched",
        inputs={"amount": Decimal("10.50"), "when": None, "tags": {"a", "b"}},
    )
    # must round-trip through json without raising
    json.dumps(entry)
    assert entry["inputs"]["amount"] == "10.50"


def test_file_is_written_as_jsonl_and_flushed(tmp_path):
    path = tmp_path / "run.jsonl"
    log = AuditLogger(path=path)
    log.log(record_id="a", stage="match", decision="matched", confidence=1.0)
    log.log(record_id="b", stage="match", decision="escalated-to-llm", confidence=0.5)
    # flushed immediately — readable before close()
    lines = path.read_text(encoding="utf-8").splitlines()
    assert len(lines) == 2
    assert json.loads(lines[0])["record_id"] == "a"
    log.close()


def test_read_round_trips(tmp_path):
    path = tmp_path / "run.jsonl"
    with AuditLogger(path=path) as log:
        log.log(record_id="a", stage="match", decision="matched")
        log.log(record_id="b", stage="reason", decision="no_match")
    back = AuditLogger.read(path)
    assert [e["record_id"] for e in back] == ["a", "b"]
    assert [e["stage"] for e in back] == ["match", "reason"]


def test_context_manager_closes_the_file(tmp_path):
    path = tmp_path / "run.jsonl"
    with AuditLogger(path=path) as log:
        log.log(record_id="a", stage="match", decision="matched")
    assert log._fh is None  # asserting the file handle was closed


# --- the trail from a real matching run ------------------------------


@pytest.fixture(scope="module")
def trail(tmp_path_factory, ingested):
    path = tmp_path_factory.mktemp("audit") / "run.jsonl"
    audit = AuditLogger(path=path)
    report = reconcile(ingested, audit)
    audit.close()
    return report, audit, AuditLogger.read(path)


def test_every_decision_produced_one_audit_entry(trail):
    report, audit, on_disk = trail
    assert len(on_disk) == len(report.decisions)
    assert len(audit) == len(report.decisions)


def test_entries_cover_every_matched_record(trail):
    report, _, on_disk = trail
    assert {e["record_id"] for e in on_disk} == {d.record_id for d in report.decisions}


def test_every_entry_has_the_required_shape(trail):
    _, _, on_disk = trail
    for e in on_disk:
        assert e["stage"] == "match"
        assert e["run_id"]
        assert e["ts"]
        assert e["record_id"]
        assert e["decision"] in {
            "matched",
            "escalated-to-llm",
            "escalated-to-rag",
            "ignored",
        }
        assert isinstance(e["matched_to"], list)
        assert e["rationale"]  # never blank — a reader must be able to see *why*
        assert e["source"].startswith("deterministic:")


def test_matched_entries_name_their_counterpart(trail):
    report, _, _ = trail
    for d in report.in_bucket(Bucket.MATCHED):
        assert d.matched_to, f"{d.record_id} matched but names no counterpart"


def test_ambiguous_entries_carry_the_candidates_for_the_llm(trail):
    report, _, on_disk = trail
    by_id = {e["record_id"]: e for e in on_disk}
    for d in report.in_bucket(Bucket.AMBIGUOUS):
        entry = by_id[d.record_id]
        cands = entry["inputs"].get("candidates", [])
        assert cands, f"{d.record_id} is ambiguous but logged no candidates"
        assert all("score" in c for c in cands)


def test_run_id_is_consistent_across_the_trail(trail):
    _, audit, on_disk = trail
    assert {e["run_id"] for e in on_disk} == {audit.run_id}


def test_decision_counts_summary(trail):
    _, audit, _ = trail
    counts = audit.decision_counts()
    assert sum(counts.values()) == len(audit)
    assert counts["match/matched"] > 400
