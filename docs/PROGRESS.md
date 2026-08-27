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

## Session 4 — task 1.2 (ingest + canonical schema)
**Date:** 2026-08-28
**Done:**
- **Fixed 3 defects in the 1.1 corpus** found while designing the schema — each would
  have corrupted normalisation:
  1. Invoice rows had `currency=USD` while `gross_amount` held the INR value. The ledger
     is now always INR (functional currency); the source currency lives on the gateway
     side only. This is what *creates* the FX case rather than being a data bug.
  2. Refund bank rows had `true_match_id=rfnd_…`, an id present in **no** source file —
     unreachable ground truth. They now correctly carry no match (they are exceptions,
     explained by RAG, not matched).
  3. Settlement N:1 truth was crammed into `true_match_id` as `SETTLE:setl_…`. Added
     `data/ground_truth/settlement_groups.csv` (208 rows) as the real N:1 answer key.
- `src/recon/ingest/schema.py` — frozen pydantic `CanonicalTxn`. Amounts are positive
  Decimals in **major units** (paise divided exactly once, here); `direction` carries the
  sign; `value_date` is a plain date; `raw` preserves the source row for the audit trail.
  `status` keeps each source's own vocabulary — `moved_money` is the one cross-source
  question matching asks (filters the 55 failed payments).
- `src/recon/ingest/validate.py` — parsers that raise with the field named, plus a
  `RowErrors` accumulator so a bad row reports **all** its problems at once (better
  audit evidence). `RejectedRow(source, row_number, reason, raw)`.
- `src/recon/ingest/loaders.py` — one loader per dialect; `load_all()` → `IngestResult`.
  Bank loader parses `setl_/pay_/rfnd_/order_` ids out of free-text narration.
- `tests/test_ingest.py` — 22 tests, all passing. ruff clean.

**Verified DoD:** 680 rows read → **679 normalised + 1 rejected**. The reject names both
bad fields and keeps the raw row:
`gateway line 308: amount: 'N/A' is not a whole number of minor units; captured_at:
'2026-13-45T99:99:99' is not an ISO-8601 date/timestamp`.
Counts: 306 gateway / 245 invoice / 128 bank.

**Note / minor friction:** `import recon` only resolves via pytest (`pythonpath` in
pyproject) or the `sys.path` insertion scripts do. Ad-hoc `python -c` needs
`PYTHONPATH="src;."`. Left as-is — an editable install is Phase 2 polish, not worth the
churn now.

**Next:**
- Task 1.3 — deterministic matching (exact then fuzzy) producing the three buckets, and
  task 1.4 — the audit trail, wired into matching before any LLM/RAG work.

---

## Session 5 — tasks 1.3 + 1.4 (deterministic matching + audit trail)
**Date:** 2026-08-28
**Done:**
- **Generator data-flow fix:** bank statement (settlements + refund/charge debits) is now
  derived from the *post-injection* gateway rows, so a settlement credit always foots to
  its members. Partial-capture / duplicate payments are `_held` back from settlement
  (realistic — flagged payments are held pending review). Corpus regenerated; all random
  ids shifted (deterministic generator, different RNG call order). Ingest tests still green.
- `src/recon/matching/`:
  - `types.py` — `Bucket`, `ScoreBreakdown`, `Candidate`, `MatchDecision`, `MatchReport`.
  - `exact.py` — 1:1 on ref+amount+currency+day; a key mapping to >1 record on either side
    is deferred to fuzzy (surfaces duplicate refs instead of mis-resolving).
  - `fuzzy.py` — `score_pair` → weighted amount/date/name(rapidfuzz)/ref. **Hard rule:**
    cross-currency or real amount gap caps the score below `MATCH_CONFIDENT` → those can
    only reach `unmatched-ambiguous`.
  - `engine.py` — `reconcile()`: filter dead → exact → fuzzy (greedy mutual-best + margin
    gate) → settlement N:1 (subset-sum, tolerates a held-back payment) → bank exceptions.
- `config/settings.py` — real matching thresholds (`MATCH_CONFIDENT=0.82`, `MATCH_MARGIN`,
  `AMOUNT_ABS/REL_TOLERANCE`, `DATE_WINDOW_DAYS=4`, `NAME_SIMILARITY_MIN=0.72`,
  `SETTLEMENT_ABS_TOLERANCE`). `LLM_CONFIDENCE_MIN=0.60`.
- `src/recon/audit/logger.py` — `AuditLogger`: append-only JSONL, per-line flush, in-memory
  mirror, `seq` counter, `read()` round-trip, context manager, `for_run()` factory.
  Wired into the engine: one entry per decision.
- `tests/test_matching.py` (18) + `tests/test_audit.py` (15). Full suite: 57 passed,
  4 skipped (agent/eval/rag/reasoning stubs). ruff clean.

**Verified DoD:**
- **Deterministic matching: 100% bucket accuracy** vs `data/ground_truth/` on all 679
  records. 464 matched (68%) / 112 → LLM / 48 → RAG / 55 ignored. Zero misbuckets.
- Audit: a run writes 679 JSONL entries; `entries on disk == decisions`; every entry has a
  non-blank rationale; ambiguous entries carry the scored candidates for the LLM stage.
- One real bug caught & fixed: `audit = audit or AuditLogger(...)` — an empty logger is
  falsy (`__len__`), so a passed-in logger was silently replaced. Now `if audit is None`.

**Next:**
- Task 1.5 — LLM reasoning layer (Gemini, structured output, replay cache) on the 112
  `unmatched-ambiguous` records.

---

## Session 6 — tasks 1.5 (LLM reasoning) + 1.6 (RAG grounding)
**Date:** 2026-08-28
**Done — 1.5:**
- `src/recon/reasoning/` — `GeminiReasoner` (`google-genai`, `response_schema`,
  `temperature=0`), content-addressed replay cache, `run_reasoning()` batch runner.
- **Model: `gemini-2.5-flash` → `gemini-3.6-flash`** — 2.5-flash is 404 for new API keys.
- Confident LLM match + residual amount variance (FX / partial) → escalates with the
  LLM's finding attached, not auto-resolved. Keeps ground-truth alignment (all 112
  ambiguous → escalated) while surfacing the analysis.
- `fail_once_ids` hook for the task-1.7 retry demo. Persistent failure escalates that
  record, batch continues.
- 15 tests, fully offline. `scripts/populate_llm_cache.py` (rate-limit backoff).

**Done — 1.6:**
- 9 real GST docs in `data/policy/` (CGST Act ss.16/17/31/34/54, Rules 38/46/53,
  Circular 160/16/2021), extracted from cbic-gst.gov.in PDFs with pypdf. `SOURCES.md`.
- `src/recon/rag/` — `PolicyIndex` (chunk → sentence-transformers embed → ChromaDB),
  `ground_exception` (retrieval + template, verbatim quoted citations, no LLM prose),
  `run_grounding()`. `scripts/build_rag_index.py --check`.
- Index NOT committed (binary/non-deterministic) — auto-builds on first query (~2s once
  the ~90MB embedding model is cached).
- 12 tests. Retrieval gate passes: refund → s.34, charge → s.16/r.38.

**Verified DoD:**
- Reasoning: 112 ambiguous → structured `unsure`/escalate in fallback mode, run completes.
- Grounding: all 48 exceptions grounded + cited (43 refund → s.34, 5 charge → s.16/r.38).
- Full suite: 83 passed, 2 skipped (agent, eval stubs). ruff clean.

**Blocked / waiting on user:**
- **LLM cache is ~20/112** — Gemini **free-tier daily quota exhausted** (429
  `RESOURCE_EXHAUSTED`). To fill it: enable billing on the API key (fastest, ~cents),
  use a `-flash-lite` model, or run over a few days. Non-blocking — the pipeline runs
  with the fallback until then; Day 6 eval wants the full cache.

**Next:**
- Task 1.7 — agent orchestration: wire ingest → match → reason → ground into one loop
  with retry, and demonstrate both injected failure modes handled.

---
