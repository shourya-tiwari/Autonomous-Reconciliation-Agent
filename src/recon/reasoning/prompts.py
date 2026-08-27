"""Versioned prompt + output schema for the LLM reasoning layer.

Everything the model sees or returns is defined here and stamped with
``PROMPT_VERSION``. The audit trail records that version against every decision,
so a result can always be traced to the exact instructions that produced it.
Bump the version on any change to the wording or the schema — that also changes
the replay-cache key, which is what you want.
"""

from __future__ import annotations

import json
from enum import StrEnum

from pydantic import BaseModel, Field

PROMPT_VERSION = "recon-reason-v1"


class Decision(StrEnum):
    MATCH = "match"
    NO_MATCH = "no_match"
    UNSURE = "unsure"


class ReasoningOutput(BaseModel):
    """The structured answer the model must return — no free-form prose."""

    decision: Decision = Field(
        description="'match' if the record and one candidate are the same real transaction; "
        "'no_match' if none of the candidates is; 'unsure' if it genuinely cannot be "
        "determined from the data and needs a human."
    )
    matched_candidate_id: str = Field(
        default="",
        description="When decision is 'match', the txn_id of the single candidate it "
        "matches. Empty otherwise.",
    )
    confidence: float = Field(
        ge=0.0,
        le=1.0,
        description="0..1. How sure you are of 'decision'. Use <0.6 whenever a human "
        "should look before money is moved.",
    )
    rationale: str = Field(
        description="One or two sentences a finance controller can act on: what the "
        "discrepancy is and why you decided as you did. Reference concrete amounts/dates."
    )


SYSTEM_INSTRUCTION = """\
You are a reconciliation analyst. The deterministic matcher could not confidently \
pair a transaction record with a counterpart and has escalated it to you.

You are given one record and its scored candidate counterparts from another \
source. Decide whether the record is the same real-world transaction as exactly \
one candidate.

Principles:
- A small, explainable amount difference (rounding to whole rupees, a documented \
processor fee, a stated FX rate) is consistent with a match.
- A large or unexplained amount difference is NOT a match on its own — it may be \
a partial capture, a wrong pairing, or an error. Prefer 'unsure' and a low \
confidence; do not force a match to make the number work.
- Two candidates that both fit equally well means a duplicate or an ambiguity: \
answer 'unsure', not 'match'.
- Reserve confidence >= 0.8 for cases a human would not need to re-check.

Return only the structured fields requested."""


def build_user_prompt(payload: dict) -> str:
    """Render the record + candidates + deterministic note as the user turn."""
    return (
        "Reconciliation case:\n\n"
        + json.dumps(payload, indent=2, ensure_ascii=False)
        + "\n\nDecide whether the record matches exactly one candidate."
    )
