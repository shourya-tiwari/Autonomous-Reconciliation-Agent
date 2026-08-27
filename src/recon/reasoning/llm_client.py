"""Gemini-backed reasoner for ambiguous matches, with an on-disk replay cache.

The pipeline must run from a clean clone with no API key and no network, so every
call goes through a content-addressed cache:

* **cache hit** — the stored response is returned; no network.
* **cache miss, replay-only (default)** — a deterministic fallback result is
  returned (``decision='unsure'``, low confidence, rationale drawn from the
  deterministic score). The record escalates, which is the safe outcome, and the
  run continues.
* **cache miss, live mode** (``RECON_LLM_REPLAY_ONLY=0`` + ``GEMINI_API_KEY``) —
  Gemini is called via ``google-genai`` with a ``response_schema``, and the
  parsed response is written to the cache.

The cache key is a SHA-256 of the canonical JSON of ``{prompt_version, model,
request}``. Bumping ``PROMPT_VERSION`` or changing the request re-keys cleanly.

``fail_once_ids`` makes ``reason`` raise :class:`ReasoningTimeout` the first time
it sees a given record id — the hook the agent layer (task 1.7) uses to
demonstrate the injected API-timeout failure mode being retried.
"""

from __future__ import annotations

import hashlib
import json
from dataclasses import asdict, dataclass, field
from datetime import UTC, datetime
from pathlib import Path

from config import settings

from .prompts import (
    PROMPT_VERSION,
    SYSTEM_INSTRUCTION,
    Decision,
    ReasoningOutput,
    build_user_prompt,
)


class ReasoningError(RuntimeError):
    """Base class for reasoning-layer failures."""


class ReasoningTimeout(ReasoningError):
    """A single LLM call timed out (real, or the injected failure mode)."""


# --- request / result shapes -------------------------------------------


@dataclass(frozen=True)
class TxnView:
    """The subset of a canonical record the model is shown. Strings only, so the
    JSON — and therefore the cache key — is stable."""

    txn_id: str
    source: str
    amount: str
    currency: str
    value_date: str
    counterparty: str | None
    ref_id: str | None
    status: str

    @classmethod
    def of(cls, txn) -> TxnView:
        return cls(
            txn_id=txn.txn_id,
            source=str(txn.source),
            amount=str(txn.amount),
            currency=txn.currency,
            value_date=txn.value_date.isoformat(),
            counterparty=txn.counterparty,
            ref_id=txn.ref_id,
            status=txn.status,
        )


@dataclass(frozen=True)
class CandidateView:
    txn: TxnView
    score: dict


@dataclass(frozen=True)
class ReasoningRequest:
    record: TxnView
    candidates: tuple[CandidateView, ...]
    deterministic_note: str

    def payload(self) -> dict:
        return {
            "record": asdict(self.record),
            "candidates": [
                {**asdict(c.txn), "match_score": c.score} for c in self.candidates
            ],
            "why_escalated": self.deterministic_note,
        }

    def cache_key(self, *, prompt_version: str, model: str) -> str:
        blob = json.dumps(
            {"prompt_version": prompt_version, "model": model, "request": self.payload()},
            sort_keys=True,
            ensure_ascii=False,
        )
        return hashlib.sha256(blob.encode("utf-8")).hexdigest()


@dataclass(frozen=True)
class ReasoningResult:
    decision: Decision
    matched_candidate_id: str
    confidence: float
    rationale: str
    source: str                 # "cache" | "gemini" | "fallback"
    model: str
    prompt_version: str
    cache_key: str = ""
    raw: dict = field(default_factory=dict)

    @property
    def is_confident_match(self) -> bool:
        return (
            self.decision is Decision.MATCH
            and self.confidence >= settings.LLM_CONFIDENCE_MIN
            and bool(self.matched_candidate_id)
        )


# --- the reasoner ------------------------------------------------------


class GeminiReasoner:
    def __init__(
        self,
        *,
        model: str | None = None,
        cache_dir: Path | None = None,
        replay_only: bool | None = None,
        api_key: str | None = None,
        fail_once_ids: set[str] | None = None,
    ) -> None:
        self.model = model or settings.LLM_MODEL
        self.cache_dir = Path(cache_dir) if cache_dir is not None else settings.LLM_CACHE_DIR
        self.replay_only = settings.LLM_REPLAY_ONLY if replay_only is None else replay_only
        self.api_key = settings.GEMINI_API_KEY if api_key is None else api_key
        self.prompt_version = PROMPT_VERSION
        self._fail_once = set(fail_once_ids or ())
        self._client = None  # lazily created only when a live call is needed
        self.stats = {"cache": 0, "gemini": 0, "fallback": 0}

    # -- public ------------------------------------------------------

    def reason(self, request: ReasoningRequest) -> ReasoningResult:
        rid = request.record.txn_id
        if rid in self._fail_once:
            self._fail_once.discard(rid)
            raise ReasoningTimeout(f"reasoning call for {rid} timed out")

        key = request.cache_key(prompt_version=self.prompt_version, model=self.model)
        cached = self._read_cache(key)
        if cached is not None:
            self.stats["cache"] += 1
            return self._result_from(cached, key, source="cache")

        if self.replay_only:
            self.stats["fallback"] += 1
            return self._fallback(request, key)

        parsed = self._call_gemini(request)
        self._write_cache(key, request, parsed)
        self.stats["gemini"] += 1
        return self._result_from(parsed.model_dump(), key, source="gemini")

    # -- cache -----------------------------------------------------

    def _cache_path(self, key: str) -> Path:
        return self.cache_dir / f"{key}.json"

    def _read_cache(self, key: str) -> dict | None:
        path = self._cache_path(key)
        if not path.exists():
            return None
        data = json.loads(path.read_text(encoding="utf-8"))
        return data["output"]

    def _write_cache(self, key: str, request: ReasoningRequest, parsed: ReasoningOutput) -> None:
        self.cache_dir.mkdir(parents=True, exist_ok=True)
        record = {
            "key": key,
            "model": self.model,
            "prompt_version": self.prompt_version,
            "created_at": datetime.now(UTC).isoformat(),
            "request": request.payload(),
            "output": parsed.model_dump(),
        }
        self._cache_path(key).write_text(
            json.dumps(record, indent=2, ensure_ascii=False), encoding="utf-8"
        )

    # -- gemini --------------------------------------------------

    def _call_gemini(self, request: ReasoningRequest) -> ReasoningOutput:
        if not self.api_key:
            raise ReasoningError(
                "live mode requires GEMINI_API_KEY (set RECON_LLM_REPLAY_ONLY=1 to stay offline)"
            )
        try:
            from google import genai
            from google.genai import types
        except ImportError as exc:  # pragma: no cover
            raise ReasoningError("google-genai not installed") from exc

        if self._client is None:
            self._client = genai.Client(api_key=self.api_key)

        try:
            response = self._client.models.generate_content(
                model=self.model,
                contents=build_user_prompt(request.payload()),
                config=types.GenerateContentConfig(
                    system_instruction=SYSTEM_INSTRUCTION,
                    response_mime_type="application/json",
                    response_schema=ReasoningOutput,
                    temperature=0.0,
                ),
            )
        except Exception as exc:
            name = type(exc).__name__.lower()
            if "timeout" in name or "deadline" in name:
                raise ReasoningTimeout(str(exc)) from exc
            raise ReasoningError(f"gemini call failed: {exc}") from exc

        parsed = response.parsed
        if isinstance(parsed, ReasoningOutput):
            return parsed
        if parsed is not None:
            return ReasoningOutput.model_validate(parsed)
        return ReasoningOutput.model_validate_json(response.text)

    # -- fallback (offline, cache miss) --------------------------

    def _fallback(self, request: ReasoningRequest, key: str) -> ReasoningResult:
        note = request.deterministic_note or "no confident deterministic match"
        return ReasoningResult(
            decision=Decision.UNSURE,
            matched_candidate_id="",
            confidence=0.0,
            rationale=(
                f"No cached LLM judgment available; escalated for human review. "
                f"Deterministic layer said: {note}"
            ),
            source="fallback",
            model=self.model,
            prompt_version=self.prompt_version,
            cache_key=key,
        )

    # -- shared -------------------------------------------------

    def _result_from(self, output: dict, key: str, *, source: str) -> ReasoningResult:
        return ReasoningResult(
            decision=Decision(output["decision"]),
            matched_candidate_id=output.get("matched_candidate_id", "") or "",
            confidence=float(output["confidence"]),
            rationale=output["rationale"],
            source=source,
            model=self.model,
            prompt_version=self.prompt_version,
            cache_key=key,
            raw=output,
        )
