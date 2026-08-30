"""Tests for scripts/show_audit.py — the walkthrough must quote, never paraphrase.

The walkthrough is presentation, but it is presentation of evidence: a judge
reads it instead of the 842-line trail. So the bar is that every value it prints
came out of the trail unchanged, and that it degrades honestly when a run has no
entry of some kind rather than inventing a scene for it.
"""

from __future__ import annotations

import importlib.util
import json
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parent.parent


def _load_module():
    """scripts/ is not a package, so load the script by path."""
    spec = importlib.util.spec_from_file_location("show_audit", ROOT / "scripts" / "show_audit.py")
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


show_audit = _load_module()


def _entry(seq, record_id, stage, decision, source, **kwargs):
    return {
        "run_id": "TESTRUN",
        "seq": seq,
        "ts": "2026-08-27T22:30:10.000000+00:00",
        "record_id": record_id,
        "stage": stage,
        "decision": decision,
        "confidence": kwargs.pop("confidence", 1.0),
        "source": source,
        "matched_to": kwargs.pop("matched_to", []),
        "inputs": kwargs.pop("inputs", {}),
        "rationale": kwargs.pop("rationale", "because"),
    }


@pytest.fixture
def trail():
    """A miniature run covering every decision kind the walkthrough looks for."""
    return [
        _entry(1, "pay_BAD", "ingest", "rejected", "validate:gateway",
               inputs={"row_number": 9, "raw": {"amount": "N/A"}},
               rationale="amount: 'N/A' is not a whole number of minor units"),
        _entry(2, "pay_DEAD", "match", "ignored", "deterministic:filter",
               inputs={"status": "failed"}),
        _entry(3, "INV-1", "match", "matched", "deterministic:exact", matched_to=["pay_1"]),
        _entry(4, "INV-2", "match", "matched", "deterministic:fuzzy", confidence=0.92,
               matched_to=["pay_2"],
               inputs={"candidates": [{"txn_id": "pay_2", "score": {"total": 0.92, "date": 0.6}}]}),
        _entry(5, "UTR-1", "match", "matched", "deterministic:settlement-group",
               matched_to=["pay_1", "pay_2"], inputs={"members": 2, "net": 5474.71}),
        _entry(6, "INV-7", "match", "escalated-to-llm", "deterministic:fuzzy", confidence=0.55),
        _entry(7, "UTR-9", "match", "escalated-to-rag", "deterministic:no-candidate"),
        _entry(8, "INV-7", "reason", "escalated", "cache:recon-reason-v1", confidence=0.95,
               inputs={"llm_raw": {"decision": "match", "confidence": 0.95}}),
        _entry(9, "INV-96", "reason", "escalated", "fallback:recon-reason-v1", confidence=0.0),
        _entry(10, "pay_SLOW", "agent", "retry", "retry:attempt-1", confidence=0.0,
               inputs={"error": "timed out", "backoff_seconds": 0.25}),
        _entry(11, "UTR-9", "ground", "grounded", "rag:Section 34 CGST Act - Credit and debit notes",
               confidence=0.76, inputs={"exception_kind": "refund", "citations": [
                   {"doc": "Section 34 CGST Act - Credit and debit notes", "score": 0.761}]}),
        _entry(12, "UTR-8", "ground", "grounded",
               "rag:Section 16 CGST Act - Eligibility and conditions for taking input tax credit",
               confidence=0.80, inputs={"exception_kind": "charge"}),
        _entry(13, "__run__", "agent", "completed", "orchestrator",
               inputs={"total": 680, "elapsed_seconds": 7.19}),
    ]


# --- selection --------------------------------------------------------


def test_every_scene_is_built_when_the_run_has_every_decision_kind(trail):
    scenes = show_audit.build_scenes(trail)
    assert len(scenes) == 11
    assert all(scene.entries for scene in scenes), "a scene with no evidence must be dropped"


def test_scenes_are_dropped_rather_than_faked_when_evidence_is_missing(trail):
    """No retry in the run means no 'we handled a timeout' section. This is the
    whole point: the walkthrough cannot claim a failure mode the trail lacks."""
    without_retry = [e for e in trail if e["decision"] != "retry"]
    titles = [s.title for s in show_audit.build_scenes(without_retry)]
    assert not any("timeout" in t for t in titles)
    assert len(titles) == 10


def test_llm_scene_chains_the_match_and_the_reasoning_for_one_record(trail):
    scene = next(s for s in show_audit.build_scenes(trail) if "LLM" in s.title)
    assert [e["seq"] for e in scene.entries] == [6, 8]
    assert {e["record_id"] for e in scene.entries} == {"INV-7"}


def test_rag_scene_chains_the_exception_and_its_grounding(trail):
    scene = next(s for s in show_audit.build_scenes(trail) if "quoted GST clause" in s.title)
    assert [e["seq"] for e in scene.entries] == [7, 11]


def test_pick_matches_source_by_prefix(trail):
    assert show_audit.pick(trail, stage="reason", source="cache:")["seq"] == 8
    assert show_audit.pick(trail, stage="reason", source="fallback:")["seq"] == 9
    assert show_audit.pick(trail, stage="reason", source="nope:") is None


def test_chain_returns_one_record_in_decision_order(trail):
    assert [e["seq"] for e in show_audit.chain(trail, "INV-7")] == [6, 8]
    assert show_audit.chain(trail, "absent") == []


# --- rendering --------------------------------------------------------


def test_entry_renders_the_values_from_the_trail_verbatim(trail):
    text = "\n".join(show_audit.fmt_entry(trail[0]))
    assert "pay_BAD" in text
    assert "ingest  ->  rejected" in text
    assert "amount: 'N/A' is not a whole number of minor units" in text
    assert "row_number = 9" in text, "nested inputs must survive"


def test_nested_score_breakdown_is_flattened_not_json_dumped(trail):
    text = "\n".join(show_audit.fmt_entry(trail[3]))
    assert "txn_id=pay_2" in text and "total=0.92" in text and "date=0.6" in text
    assert "{" not in text, "the score breakdown should read as fields, not JSON"


def test_markdown_covers_every_scene_and_stays_valid(trail):
    md = show_audit.render_markdown(Path("run-TESTRUN.jsonl"), trail, show_audit.build_scenes(trail))
    assert md.count("\n## ") == 11
    assert md.count("```") % 2 == 0, "unbalanced code fences"
    assert "| 1 | `agent` | `retry` |" in md


def test_terminal_render_reports_how_much_of_the_trail_it_showed(trail):
    text = show_audit.render_terminal(Path("run-TESTRUN.jsonl"), trail, show_audit.build_scenes(trail))
    assert f"of {len(trail)} entries shown" in text
    assert "TESTRUN" in text


def test_long_values_are_clipped_not_wrapped(trail):
    entry = _entry(1, "r", "match", "matched", "deterministic:exact", rationale="x" * 400)
    lines = show_audit.fmt_entry(entry, width=60)
    assert all(len(line) < 120 for line in lines)
    assert "…" in "\n".join(lines)


# --- the committed artifact -------------------------------------------


def test_committed_walkthrough_is_present_and_current():
    """The JSONL trail is regenerated per run and gitignored, so the walkthrough
    is what a judge reads from a clean clone. It has to actually be there."""
    path = ROOT / "outputs" / "reports" / "audit_walkthrough.md"
    assert path.exists(), "run `python scripts/show_audit.py --markdown`"
    text = path.read_text(encoding="utf-8")
    assert text.startswith("# Audit trail walkthrough")
    assert "Failure mode 1" in text and "Failure mode 2" in text


def test_round_trip_from_a_real_jsonl_file(tmp_path, trail):
    path = tmp_path / "run-TESTRUN.jsonl"
    path.write_text("\n".join(json.dumps(e) for e in trail), encoding="utf-8")
    assert show_audit.load(path) == trail
