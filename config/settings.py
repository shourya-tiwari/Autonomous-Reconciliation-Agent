"""Central configuration: paths, matching thresholds, model + RAG settings.

Values here are read from environment variables (see .env.example) with safe
defaults so the pipeline runs from a clean clone with no setup.
TODO: fill in as components land. Keep every tunable (fuzzy tolerances,
confidence cutoffs, top-k) in this file, not scattered across modules.
"""
from __future__ import annotations

import os
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

# LLM reasoning layer — provider/model TBD (see docs/PROGRESS.md Session 1 flags)
LLM_MODEL = os.environ.get("RECON_LLM_MODEL", "claude-sonnet-5")
LLM_REPLAY_ONLY = os.environ.get("RECON_LLM_REPLAY_ONLY", "1") == "1"  # no live API by default

# Matching thresholds — TODO tune on real data (Day 2/6)
AMOUNT_ABS_TOLERANCE = 0.0
DATE_WINDOW_DAYS = 0
NAME_SIMILARITY_MIN = 0.0
LLM_CONFIDENCE_MIN = 0.0  # below this -> escalate
