# Progress Log

Log each session here: date, what got done, what's blocked, what's next.
Keep entries short — this exists so a new session has continuity without re-reading everything.

---

## Session Template
**Date:**
**Done:**
-
**Blocked / open questions:**
-
**Next:**
-

---

## Session 1 — setup
**Date:** 2026-08-27
**Done:**
- Read all four docs; confirmed problem / stack / phase structure.
- Created repo skeleton matching architecture.md: `src/recon/` with one package per
  pipeline stage (ingest, matching, reasoning, rag, agent, audit) + `eval`, plus
  `scripts/`, `tests/`, `data/{snapshot,raw,policy,synthetic,ground_truth}`, `outputs/{audit,reports}`.
- All modules are docstring + TODO stubs — no pipeline logic yet (per instruction).
- Added `requirements.txt`, `pyproject.toml` (src layout, pytest, ruff), `config/settings.py`
  (central thresholds/paths), `.env.example`, `.gitignore`, `README.md`.
- Not a git repo yet — `git init` not run.

**Blocked / open questions (flagged for confirmation):**
1. **Clean-clone vs. live data.** "Runs from a clean clone, no manual fixes" conflicts with
   pulling live Razorpay sandbox data (needs API keys). Resolution taken: commit a frozen
   curated pull to `data/snapshot/` (used by default); `data/raw/` live pulls are gitignored.
   Need confirmation this is acceptable to judges / matches intent.
2. **LLM reproducibility.** Same tension for the reasoning layer. Plan: record every LLM
   request/response and replay from cache by default (`RECON_LLM_REPLAY_ONLY=1`), so eval
   numbers are reproducible offline. Live calls only when explicitly enabled.
3. **LLM provider/model not locked.** Assumed Anthropic Claude (`claude-sonnet-5`) for
   structured output + cost. Confirm provider; confirm we're OK committing cached responses.
4. **Vector store / embeddings.** Chose ChromaDB (embedded, no server) + local
   sentence-transformers embeddings (no API key, offline, reproducible) over API embeddings.
   Confirm — API embeddings would be higher quality but break clean-clone/offline.
5. **Source count.** architecture.md says "2-3 sources"; skeleton assumes exactly 3
   (bank statement, invoice ledger, gateway export).
6. **Python 3.11+** assumed; `pandas` + `pydantic v2` + `rapidfuzz` as the core stack.

**Next (Day 1 — dataset):**
- Implement `scripts/pull_razorpay_sandbox.py`; pull multi-currency / refunds / partial
  captures / failed settlements; curate frozen snapshot into `data/snapshot/`.
- Implement `scripts/generate_synthetic.py`: 3 source schemas + documented mismatch
  catalogue (amount rounding, split payments, duplicate refs, missing fields, malformed
  row, API-timeout case) with `data/ground_truth/` labels.
- Write the metric definitions in `src/recon/eval/metrics.py` (define now, run Day 6).

---
