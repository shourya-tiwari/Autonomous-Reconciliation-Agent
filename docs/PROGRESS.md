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
- Created repo skeleton matching ARCHITECTURE.md: `src/recon/` with one package per
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
5. **Source count.** ARCHITECTURE.md says "2-3 sources"; skeleton assumes exactly 3
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

## Session 2 — git + task planning
**Date:** 2026-08-27
**Done:**
- `git init` on `main`; pushed to `github.com/shourya-tiwari/razorpay-buildathon`.
  Two commits: `docs: add project documentation`, `chore: scaffold repo skeleton`.
  Author = Shourya Tiwari, no AI co-author trailer. `CLAUDE.md` + `learning_log.md` gitignored.
- Added `docs/TASKS.md` (committed) — build structured into Phase 1 (core pipeline, tasks
  1.1–1.8), Phase 2 (polish + pitch), Phase 3 (stretch), Phase 4 (future scope). Each
  task has a definition of done. This is the entry point for "start implementing phase N".
- Added `learning_log.md` (gitignored) — running per-prompt log of what changed + what
  to learn.
- Reviewed all `.md` files for consistency; updated `CLAUDE.md` (git status, pointers to
  docs/TASKS.md / learning_log.md, commit convention).
- Renamed `docs/architecture.md` → `docs/ARCHITECTURE.md`; moved `tasks.md` → `docs/TASKS.md`.

**Blocked / open questions:**
- The 6 design questions from Session 1 are still unconfirmed (see above + `docs/TASKS.md`
  bottom). They gate a clean Phase 1 start — will confirm at "start implementing phase 1".

**Next:**
- On "start implementing phase 1": begin at task 1.1 (dataset). First confirm the 6 open
  questions, then implement `scripts/pull_razorpay_sandbox.py`.

---

## Session 3 — Phase 1 start (task 1.1, dataset)
**Date:** 2026-08-28
**Done:**
- Resolved all 6 design questions (recorded in `docs/TASKS.md` "Design decisions").
  LLM provider = **Google Gemini** (`google-genai`, `gemini-2.5-flash`) — not Claude.
  Updated `requirements.txt` (google-genai + razorpay, dropped anthropic), `.env.example`
  (GEMINI_API_KEY), `config/settings.py` (LLM_MODEL, LLM_CACHE_DIR), `CLAUDE.md`.
- Implemented `scripts/pull_razorpay_sandbox.py` — paginated pull of payments/orders/
  refunds/settlements to `data/raw/` + `manifest.json`. Refuses non-`rzp_test_` keys.
  Fails cleanly with guidance when keys/deps missing. Smoke-tested.
- Wrote `data/synthetic/mismatch_catalogue.md` — 13 injected discrepancy cases (incl. the
  2 mandatory failure modes: malformed row, simulated API timeout), each with rationale +
  expected pipeline behaviour.
- Implemented `src/recon/eval/metrics.py` — metric set DEFINED (match P/R/F1, bucket
  accuracy, %auto/escalated/exception/failed, throughput, llm/rag call counts). compute()
  is a stub for Day 6.
- `pyproject.toml`: pythonpath += "." so `config` imports resolve in tests.
- `.venv/` created (Python 3.13), `requirements.txt` installed. `pytest` (6 skipped stubs)
  and `ruff` both green. Resolved versions: `google-genai` 2.20, `razorpay` 2.0.1, pandas 3.0.5.

**Task 1.1 completed later same session:**
- User ran the pull → **only 8 payments / 6 orders / 1 refund** (S2S payment creation not
  enabled on the test account; probed `createPaymentJson` → 404). All INR.
- Decision (user-approved): use the 8 real txns as the *schema anchor* in `data/snapshot/`
  + `provenance.md`; expand synthetically to ~300 for the eval corpus. Provenance stated
  honestly — 300 not claimed as real.
- Curated `data/snapshot/` (payments/orders/refunds/pull_manifest + provenance.md).
- Fully implemented `scripts/generate_synthetic.py`: generates Razorpay-shaped payments
  calibrated from the real pull → orders/refunds/T+2 settlement batches → 3 source CSVs
  with independent schemas → injects 11 documented cases → `matches.csv` (680 rows) +
  `injection_manifest.json`. Deterministic. `--dry-run` supported.
- Rewrote `mismatch_catalogue.md` to match the implementation (4 structural + 11 injected
  cases, expected buckets). Added `ignored` bucket to `metrics.py` (failed payments).
- Corpus (seed 42): 307 gateway / 245 invoice / 128 bank rows. GT buckets:
  464 auto_resolved / 112 escalated / 48 exception / 55 ignored / 1 failed.
- `data/` is committed (generated corpus + snapshot). ruff + pytest green.

**Next:**
- Task 1.2 — ingest + canonical `CanonicalTxn` pydantic schema, one loader per source,
  malformed-row rejection path. `tests/test_ingest.py`.

---
