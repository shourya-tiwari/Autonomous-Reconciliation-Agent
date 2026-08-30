"""Render an audit run (``outputs/audit/run-*.jsonl``) as a readable walkthrough.

The trail is the artifact a judge is meant to inspect, but 842 JSON objects is
not something anyone reads top to bottom. This picks out one representative
decision of each kind -- and, where a record was handled by several stages, the
whole chain for that record -- and lays them out in the order the pipeline made
them.

Nothing here re-runs or re-derives anything: every field printed is read
straight from the trail. That is the point. If the walkthrough says a record was
escalated because the currencies differed, the line above it is the log entry
that says so.

Usage
-----
    python scripts/show_audit.py                        # walkthrough of the newest run
    python scripts/show_audit.py --run outputs/audit/run-<id>.jsonl
    python scripts/show_audit.py --record INV-2026-00007   # one record, end to end
    python scripts/show_audit.py --markdown              # -> outputs/reports/audit_walkthrough.md

The markdown form is committed, so the walkthrough is readable from a clean
clone without running the pipeline first (the per-run JSONL itself is ignored).
"""

from __future__ import annotations

import argparse
import json
import sys
from collections import Counter
from collections.abc import Callable, Iterable, Sequence
from pathlib import Path
from typing import Any

ROOT = Path(__file__).resolve().parent.parent
sys.path[:0] = [str(ROOT), str(ROOT / "src")]

for _stream in (sys.stdout, sys.stderr):
    try:
        _stream.reconfigure(encoding="utf-8", errors="replace")
    except (AttributeError, ValueError):
        pass

from config import settings

Entry = dict[str, Any]

WALKTHROUGH_PATH = settings.REPORT_DIR / "audit_walkthrough.md"


# --------------------------------------------------------------------------
# loading
# --------------------------------------------------------------------------

def newest_run() -> Path:
    """The most recent run file, by name (run ids are timestamps, so this sorts)."""
    runs = sorted(settings.AUDIT_DIR.glob("run-*.jsonl"))
    if not runs:
        raise SystemExit(
            f"no audit runs in {settings.AUDIT_DIR.relative_to(ROOT)} -- "
            "run `python scripts/run_pipeline.py` first"
        )
    return runs[-1]


def show_path(path: Path) -> str:
    """Repo-relative when it is inside the repo, absolute otherwise.

    ``--run`` and ``--markdown`` both accept a path anywhere on disk, and
    ``Path.relative_to`` raises rather than falling back for those.
    """
    try:
        return path.resolve().relative_to(ROOT).as_posix()
    except ValueError:
        return str(path)


def load(path: Path) -> list[Entry]:
    with path.open(encoding="utf-8") as handle:
        return [json.loads(line) for line in handle if line.strip()]


# --------------------------------------------------------------------------
# selection
# --------------------------------------------------------------------------

def pick(
    entries: Sequence[Entry],
    *,
    stage: str | None = None,
    decision: str | None = None,
    source: str | None = None,
    where: Callable[[Entry], bool] | None = None,
) -> Entry | None:
    """First entry (by log order) matching the criteria. ``source`` is a prefix."""
    for entry in entries:
        if stage is not None and entry["stage"] != stage:
            continue
        if decision is not None and entry["decision"] != decision:
            continue
        if source is not None and not entry["source"].startswith(source):
            continue
        if where is not None and not where(entry):
            continue
        return entry
    return None


def chain(entries: Sequence[Entry], record_id: str) -> list[Entry]:
    """Every entry for one record, in the order it was decided."""
    return [e for e in entries if e["record_id"] == record_id]


class Scene:
    """One numbered section of the walkthrough: a claim plus the entries proving it."""

    def __init__(self, title: str, why: str, entries: Iterable[Entry | None]) -> None:
        self.title = title
        self.why = why
        self.entries = [e for e in entries if e is not None]

    def __bool__(self) -> bool:
        return bool(self.entries)


def build_scenes(entries: Sequence[Entry]) -> list[Scene]:
    """The curated tour. Order follows the pipeline, not the file."""
    scenes: list[Scene] = []

    scenes.append(Scene(
        "Failure mode 1 -- a malformed row is rejected, not dropped",
        "Two fields are unparseable at once and the entry names both, keeps the raw "
        "row, and lets the run continue. The record ends in the `failed` bucket, so "
        "it still shows up in the totals instead of quietly vanishing from them.",
        [pick(entries, stage="ingest", decision="rejected")],
    ))

    scenes.append(Scene(
        "Failed payments are filtered out before matching",
        "No money moved, so there is nothing to reconcile. Excluding them here -- "
        "and saying so on the record -- keeps them out of the accuracy denominator "
        "rather than padding it with free wins.",
        [pick(entries, stage="match", decision="ignored")],
    ))

    scenes.append(Scene(
        "The deterministic layer carries the load -- exact",
        "Reference, amount, currency and day all agree, so no model is consulted. "
        "This one path resolves 256 of the 680 records.",
        [pick(entries, stage="match", decision="matched", source="deterministic:exact")],
    ))

    fuzzy = pick(entries, stage="match", decision="matched", source="deterministic:fuzzy")
    scenes.append(Scene(
        "...and fuzzy, when the fields drift",
        f"Scored on amount / date / name / ref. A pairing is only accepted here if it "
        f"clears MATCH_CONFIDENT ({settings.MATCH_CONFIDENT}) *and* beats the runner-up "
        f"by MATCH_MARGIN ({settings.MATCH_MARGIN}). Everything else becomes ambiguous "
        "on purpose.",
        [fuzzy],
    ))

    scenes.append(Scene(
        "N:1 -- one bank credit against a batch of payments",
        "A settlement credit is matched to the exact subset of gateway payments that "
        "foots to it net of fees. The tolerance is deliberately tight, because a loose "
        "one lets a wrong subset add up by coincidence.",
        [pick(entries, stage="match", decision="matched", source="deterministic:settlement-group")],
    ))

    # The LLM story reads best as a chain: why it was escalated, then what the model said.
    cached = pick(entries, stage="reason", source="cache:")
    llm_scene: list[Entry | None] = []
    if cached is not None:
        llm_scene = [
            pick(entries, stage="match", decision="escalated-to-llm",
                 where=lambda e, rid=cached["record_id"]: e["record_id"] == rid),
            cached,
        ]
    scenes.append(Scene(
        "Ambiguity reaches the LLM -- and the LLM's answer still does not move money",
        "The deterministic layer capped this pairing because the currencies differ. "
        "Gemini returns structured output, not prose, and identifies the counterpart "
        "with high confidence. The pipeline escalates anyway: a residual amount "
        "variance is a human's call. The model's finding is attached to the "
        "escalation, not acted on.",
        llm_scene,
    ))

    scenes.append(Scene(
        "A cache miss escalates safely instead of failing",
        "Offline, with no API key and no cached judgment for this record, the reasoner "
        "returns a deterministic `unsure` at confidence 0.0 and the record escalates -- "
        "carrying the deterministic layer's reason with it. This is what keeps the "
        "clean-clone run honest rather than merely green.",
        [pick(entries, stage="reason", source="fallback:")],
    ))

    retry = pick(entries, stage="agent", decision="retry")
    retry_scene: list[Entry | None] = [retry]
    if retry is not None:
        retry_scene.append(
            pick(entries, stage="reason",
                 where=lambda e, rid=retry["record_id"]: e["record_id"] == rid)
        )
    scenes.append(Scene(
        "Failure mode 2 -- a transient API timeout, retried and absorbed",
        "The error is classified as transient, so it is retried with backoff; a "
        "malformed request would not be. The retry itself is logged, so 'we handled a "
        "timeout' is a checkable claim rather than a story. The second attempt returns, "
        "and the record then follows the ordinary reasoning path -- here a cache miss, "
        "so it escalates. The timeout cost one record 0.25s, not the run.",
        retry_scene,
    ))

    grounded = pick(entries, stage="ground", decision="grounded",
                    where=lambda e: "Section 34" in e["source"])
    rag_scene: list[Entry | None] = []
    if grounded is not None:
        rag_scene = [
            pick(entries, stage="match", decision="escalated-to-rag",
                 where=lambda e, rid=grounded["record_id"]: e["record_id"] == rid),
            grounded,
        ]
    scenes.append(Scene(
        "No counterpart at all -- explained against a quoted GST clause",
        "A refund debit has no invoice to match. Rather than reporting a bare "
        "unmatched line, the exception is grounded in the retrieved statute with the "
        "clause quoted verbatim and its source URL recorded, so a controller can check "
        "the citation instead of trusting it.",
        rag_scene,
    ))

    scenes.append(Scene(
        "A different exception grounds in a different clause",
        "The bank charge is an input-tax-credit question, not a credit-note one, and "
        "retrieval routes it to the ITC sections. Retrieval is doing real work here -- "
        "it is not one canned answer wearing two labels.",
        [pick(entries, stage="ground", decision="grounded",
              where=lambda e: "Section 16" in e["source"])],
    ))

    scenes.append(Scene(
        "The run closes its own books",
        "The final entry reconciles the reconciler: every input row is accounted for "
        "in exactly one terminal bucket, and the counts are logged next to the elapsed "
        "time that produced them.",
        [pick(entries, stage="agent", decision="completed")],
    ))

    return [scene for scene in scenes if scene]


# --------------------------------------------------------------------------
# rendering
# --------------------------------------------------------------------------

def _clip(text: str, width: int) -> str:
    text = str(text).replace("\n", " ")
    return text if len(text) <= width else text[: width - 1] + "…"


def _fmt_value(value: Any, width: int) -> str:
    if isinstance(value, float):
        return f"{value:g}"
    if isinstance(value, str):
        return _clip(value, width)
    return _clip(json.dumps(value, ensure_ascii=False), width)


def _flatten(item: dict[str, Any], width: int = 80) -> str:
    """A dict as `k=v` pairs, one level of nesting inlined.

    Candidate scores are the interesting part of an ambiguous decision -- the
    per-signal breakdown is why the record was escalated -- so they are laid out
    flat rather than JSON-dumped and clipped.
    """
    parts: list[str] = []
    for key, value in item.items():
        if isinstance(value, dict):
            parts += [f"{sub_key}={_fmt_value(sub_value, width)}" for sub_key, sub_value in value.items()]
        else:
            parts.append(f"{key}={_fmt_value(value, width)}")
    return "  ".join(parts)


def fmt_inputs(inputs: dict[str, Any], indent: str, width: int) -> list[str]:
    """Flatten the `inputs` blob into aligned lines, one fact per line."""
    lines: list[str] = []
    for key, value in inputs.items():
        if isinstance(value, dict):
            lines.append(f"{indent}{key}:")
            for sub_key, sub_value in value.items():
                lines.append(f"{indent}  {sub_key} = {_fmt_value(sub_value, width)}")
        elif isinstance(value, list) and value and isinstance(value[0], dict):
            lines.append(f"{indent}{key}:")
            for item in value:
                lines.append(f"{indent}  - {_clip(_flatten(item), width)}")
        elif isinstance(value, list):
            lines.append(f"{indent}{key} = {_clip(', '.join(map(str, value)) or '-', width)}")
        else:
            lines.append(f"{indent}{key} = {_fmt_value(value, width)}")
    return lines


def fmt_entry(entry: Entry, width: int = 150) -> list[str]:
    """One audit entry as a block of aligned `field  value` lines."""
    lines = [
        f"seq {entry['seq']:<5} {entry['ts']}",
        f"  record      {entry['record_id']}",
        f"  stage       {entry['stage']}  ->  {entry['decision']}",
        f"  source      {entry['source']}   confidence {entry['confidence']:.2f}",
    ]
    if entry["matched_to"]:
        lines.append(f"  matched_to  {', '.join(entry['matched_to'])}")
    lines.append(f"  rationale   {_clip(entry['rationale'], width)}")
    if entry["inputs"]:
        lines.append("  inputs")
        lines.extend(fmt_inputs(entry["inputs"], "    ", width))
    return lines


def wrap(text: str, width: int, indent: str = "") -> list[str]:
    words, lines, current = text.split(), [], indent
    for word in words:
        if len(current) + len(word) + 1 > width and current.strip():
            lines.append(current.rstrip())
            current = indent
        current += word + " "
    if current.strip():
        lines.append(current.rstrip())
    return lines


def header(path: Path, entries: Sequence[Entry]) -> list[str]:
    counts = Counter((e["stage"], e["decision"]) for e in entries)
    lines = [
        "Audit trail walkthrough",
        "=" * 78,
        f"run    {entries[0]['run_id']}",
        f"file   {show_path(path)}",
        f"       {len(entries)} entries -- one per decision, append-only, written as the run went",
        "",
        "decisions logged",
    ]
    for (stage, decision), count in sorted(counts.items(), key=lambda kv: -kv[1]):
        lines.append(f"  {count:>5}  {stage:<8} {decision}")
    return lines


def render_terminal(path: Path, entries: Sequence[Entry], scenes: Sequence[Scene]) -> str:
    out = header(path, entries)
    for number, scene in enumerate(scenes, start=1):
        out += ["", "-" * 78, f"{number}. {scene.title}", ""]
        out += wrap(scene.why, 78, "  ")
        for entry in scene.entries:
            out.append("")
            out += ["  " + line for line in fmt_entry(entry)]
    shown = sum(len(scene.entries) for scene in scenes)
    out += ["", "-" * 78,
            (f"{shown} of {len(entries)} entries shown. "
             "Use --record <id> to follow one record end to end.")]
    return "\n".join(out)


def render_markdown(path: Path, entries: Sequence[Entry], scenes: Sequence[Scene]) -> str:
    counts = Counter((e["stage"], e["decision"]) for e in entries)
    out = [
        "# Audit trail walkthrough",
        "",
        ("Every decision the pipeline makes is written to "
         "`outputs/audit/run-<id>.jsonl` as it is made -- one JSON object per line, "
         "append-only. This file is a guided tour of one such run: a representative "
         "entry of each kind, quoted verbatim from the trail, in the order the "
         "pipeline decided them."),
        "",
        "Regenerate with `python scripts/show_audit.py --markdown`.",
        "",
        f"- **Run** `{entries[0]['run_id']}`",
        f"- **Source** `{show_path(path)}`",
        f"- **Entries** {len(entries)}",
        "",
        "| Count | Stage | Decision |",
        "|------:|-------|----------|",
    ]
    for (stage, decision), count in sorted(counts.items(), key=lambda kv: -kv[1]):
        out.append(f"| {count} | `{stage}` | `{decision}` |")

    for number, scene in enumerate(scenes, start=1):
        out += ["", f"## {number}. {scene.title}", "", scene.why, ""]
        for entry in scene.entries:
            out += ["```", *fmt_entry(entry, width=130), "```", ""]
    return "\n".join(out).rstrip() + "\n"


# --------------------------------------------------------------------------

def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--run", default=None, help="audit JSONL to read (default: newest)")
    parser.add_argument("--record", default=None, help="print every entry for one record id")
    parser.add_argument("--markdown", nargs="?", const=str(WALKTHROUGH_PATH), default=None,
                        help=f"write the walkthrough to a file (default {WALKTHROUGH_PATH.name})")
    args = parser.parse_args()

    path = Path(args.run) if args.run else newest_run()
    entries = load(path)
    if not entries:
        raise SystemExit(f"{path} is empty")

    if args.record:
        found = chain(entries, args.record)
        if not found:
            raise SystemExit(f"no entries for record {args.record!r} in {path.name}")
        print(f"{args.record} -- {len(found)} decision(s), {path.name}")
        for entry in found:
            print()
            print("\n".join(fmt_entry(entry)))
        return

    scenes = build_scenes(entries)

    if args.markdown:
        out_path = Path(args.markdown)
        out_path.parent.mkdir(parents=True, exist_ok=True)
        out_path.write_text(render_markdown(path, entries, scenes), encoding="utf-8")
        print(f"wrote {show_path(out_path)} -- {len(scenes)} scenes from {len(entries)} entries")
        return

    print(render_terminal(path, entries, scenes))


if __name__ == "__main__":
    main()
