"""Central configuration: paths, matching thresholds, model + RAG settings.

Values here are read from environment variables (see .env.example) with safe
defaults so the pipeline runs from a clean clone with no setup.
TODO: fill in as components land. Keep every tunable (fuzzy tolerances,
confidence cutoffs, top-k) in this file, not scattered across modules.
"""
from __future__ import annotations

import os
from decimal import Decimal
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent

DATA_DIR = ROOT / "data"
SNAPSHOT_DIR = DATA_DIR / "snapshot"        # committed, frozen sandbox pull — clean-clone runs use this
RAW_DIR = DATA_DIR / "raw"                  # live Razorpay pulls (gitignored)
POLICY_DIR = DATA_DIR / "policy"            # GST/tax docs for the RAG corpus
GROUND_TRUTH_DIR = DATA_DIR / "ground_truth"
SYNTHETIC_DIR = DATA_DIR / "synthetic"

OUTPUT_DIR = ROOT / "outputs"
AUDIT_DIR = OUTPUT_DIR / "audit"
REPORT_DIR = OUTPUT_DIR / "reports"

# LLM reasoning layer — Google Gemini (google-genai SDK).
# gemini-2.5-flash is no longer served to new API keys (404); 3.6-flash is the
# current small model with JSON-schema structured output.
LLM_MODEL = os.environ.get("RECON_LLM_MODEL", "gemini-3.6-flash")
LLM_REPLAY_ONLY = os.environ.get("RECON_LLM_REPLAY_ONLY", "1") == "1"  # no live API by default
LLM_CACHE_DIR = DATA_DIR / "llm_cache"     # committed request->response cache for offline replay
GEMINI_API_KEY = os.environ.get("GEMINI_API_KEY", "")  # only needed when RECON_LLM_REPLAY_ONLY=0

# --- Deterministic matching thresholds ---
# Amount: a candidate's amounts must agree within the larger of an absolute and a
# relative band. Absolute covers rounding to whole rupees; relative covers the
# small give from fee models and settlement math.
AMOUNT_ABS_TOLERANCE = Decimal("1.00")   # rupees
AMOUNT_REL_TOLERANCE = 0.015             # 1.5%
# Date: invoice vs settlement dates drift by the settlement lag plus weekends.
DATE_WINDOW_DAYS = 4
# Name: rapidfuzz token_sort_ratio, 0..1. Below this the names are treated as
# unrelated rather than a weak positive.
NAME_SIMILARITY_MIN = 0.72
# Combined score (0..1) at or above which a single clear best candidate is
# accepted as a confident 1:1 match...
MATCH_CONFIDENT = 0.82
# ...and it must beat the second-best candidate by at least this margin, else the
# pairing is ambiguous and goes to the LLM.
MATCH_MARGIN = 0.08
# Settlement (N:1): net-of-fees is computed from the actual fee column, so a
# real batch foots to the paisa. Keep the tolerance tight — a loose one lets a
# wrong subset (drop a different payment) foot by coincidence.
SETTLEMENT_ABS_TOLERANCE = Decimal("0.05")

# --- LLM reasoning ---
LLM_CONFIDENCE_MIN = 0.60  # structured LLM output below this -> escalate, don't force

# --- Audit trail ---
AUDIT_ENABLED = os.environ.get("RECON_AUDIT", "1") == "1"
