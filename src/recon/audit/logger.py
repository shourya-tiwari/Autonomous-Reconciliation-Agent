"""Structured audit-trail writer.

Every decision any stage makes — a deterministic match, an LLM call, a RAG
grounding, a rejected row — is one JSON object appended to
``outputs/audit/run-<run_id>.jsonl``. The trail is the artifact judges inspect,
so each line must be enough to explain *why* a record ended where it did without
re-running anything: it carries the inputs the decision was made from, the
decision, a confidence, and the source of that confidence (which rule fired,
which model, which retrieved clause).

Design
------
* **Append-only JSONL.** One object per line, flushed immediately, so a crash
  mid-run still leaves a readable partial trail.
* **In-memory mirror.** Every entry is also kept in ``entries`` for assertions
  and for the Day-7 human-readable renderer, without re-reading the file.
* **Path optional.** ``AuditLogger(path=None)`` keeps the trail in memory only —
  used by tests and by callers that don't want a file on disk.
* **Monotonic sequence.** Each entry gets a ``seq`` so the exact decision order
  is recoverable even if two entries share a timestamp.
"""

from __future__ import annotations

import json
from collections.abc import Iterator
from datetime import UTC, datetime
from pathlib import Path
from types import TracebackType
from typing import Any, Self

Stage = str  # "ingest" | "match" | "reason" | "ground" | "agent"


def _new_run_id() -> str:
    return datetime.now(UTC).strftime("%Y%m%dT%H%M%S.%fZ")


def _jsonable(value: Any) -> Any:
    """Best-effort conversion so any decision payload can be serialised."""
    if isinstance(value, (str, int, float, bool)) or value is None:
        return value
    if isinstance(value, dict):
        return {str(k): _jsonable(v) for k, v in value.items()}
    if isinstance(value, (list, tuple, set, frozenset)):
        return [_jsonable(v) for v in value]
    return str(value)


class AuditLogger:
    """Collects decision records for one pipeline run."""

    def __init__(self, run_id: str | None = None, path: Path | None = None) -> None:
        self.run_id: str = run_id or _new_run_id()
        self.path: Path | None = Path(path) if path is not None else None
        self.entries: list[dict[str, Any]] = []
        self._seq = 0
        self._fh = None
        if self.path is not None:
            self.path.parent.mkdir(parents=True, exist_ok=True)
            self._fh = self.path.open("w", encoding="utf-8")

    # -- writing ----------------------------------------------------------

    def log(
        self,
        *,
        record_id: str,
        stage: Stage,
        decision: str,
        confidence: float | None = None,
        source: str | None = None,
        matched_to: list[str] | None = None,
        inputs: dict[str, Any] | None = None,
        rationale: str | None = None,
        **extra: Any,
    ) -> dict[str, Any]:
        """Append one decision record. Returns the stored entry.

        ``source`` is where the decision's confidence comes from — a rule id
        ("exact:ref+amount+date"), a model name, or a retrieved clause id.
        ``inputs`` is whatever a reader needs to see to check the decision.
        """
        self._seq += 1
        entry: dict[str, Any] = {
            "run_id": self.run_id,
            "seq": self._seq,
            "ts": datetime.now(UTC).isoformat(),
            "record_id": record_id,
            "stage": stage,
            "decision": decision,
            "confidence": None if confidence is None else round(float(confidence), 4),
            "source": source,
            "matched_to": list(matched_to or []),
            "inputs": _jsonable(inputs or {}),
            "rationale": rationale,
        }
        if extra:
            entry.update(_jsonable(extra))

        self.entries.append(entry)
        if self._fh is not None:
            self._fh.write(json.dumps(entry, ensure_ascii=False) + "\n")
            self._fh.flush()
        return entry

    # -- reading / summarising ------------------------------------------

    def by_stage(self, stage: Stage) -> list[dict[str, Any]]:
        return [e for e in self.entries if e["stage"] == stage]

    def for_record(self, record_id: str) -> list[dict[str, Any]]:
        return [e for e in self.entries if e["record_id"] == record_id]

    def decision_counts(self) -> dict[str, int]:
        counts: dict[str, int] = {}
        for e in self.entries:
            key = f"{e['stage']}/{e['decision']}"
            counts[key] = counts.get(key, 0) + 1
        return counts

    def __len__(self) -> int:
        return len(self.entries)

    def __iter__(self) -> Iterator[dict[str, Any]]:
        return iter(self.entries)

    # -- lifecycle ------------------------------------------------------

    def close(self) -> None:
        if self._fh is not None:
            self._fh.close()
            self._fh = None

    def __enter__(self) -> Self:
        return self

    def __exit__(
        self,
        exc_type: type[BaseException] | None,
        exc: BaseException | None,
        tb: TracebackType | None,
    ) -> None:
        self.close()

    # -- constructors / static helpers -------------------------------

    @classmethod
    def for_run(cls, run_id: str | None = None, audit_dir: Path | None = None) -> Self:
        """A logger that writes to ``<audit_dir>/run-<run_id>.jsonl``.

        ``audit_dir`` defaults to ``config.settings.AUDIT_DIR``. Use this from the
        pipeline entrypoint; tests pass an explicit ``path`` or ``None`` instead.
        """
        if audit_dir is None:
            from config import settings

            audit_dir = settings.AUDIT_DIR
        run_id = run_id or _new_run_id()
        return cls(run_id=run_id, path=Path(audit_dir) / f"run-{run_id}.jsonl")

    @staticmethod
    def read(path: str | Path) -> list[dict[str, Any]]:
        """Parse a trail file back into a list of entries."""
        out: list[dict[str, Any]] = []
        with Path(path).open(encoding="utf-8") as fh:
            for line_no, line in enumerate(fh, start=1):
                line = line.strip()
                if not line:
                    continue
                try:
                    out.append(json.loads(line))
                except json.JSONDecodeError as exc:  # pragma: no cover - corruption guard
                    raise ValueError(f"{path}:{line_no}: not valid JSON: {exc}") from exc
        return out
