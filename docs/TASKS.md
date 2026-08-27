# Implementation Tasks

Working breakdown of the build, structured in phases. Ordered — do tasks top to
bottom within a phase. When the instruction is "start implementing phase N",
start at the first unchecked task in that phase.

Source of truth for scope: `docs/PROJECT.md`, `docs/ARCHITECTURE.md`, `docs/PLAN.md`.
Log outcomes in `docs/PROGRESS.md` after each session.

Status key: `[ ]` todo · `[~]` in progress · `[x]` done · `[-]` cut/skipped

---

## Phase 1 — Core Pipeline (Days 1–6) · NON-NEGOTIABLE

The whole pipeline must run end-to-end from a clean clone with no keys/network
by the end of this phase. Audit logging starts at task 1.4 and every stage after
it writes to the trail.

### 1.1 Dataset & ground truth (Day 1) — DONE
- [x] `scripts/pull_razorpay_sandbox.py` — pulls payments/orders/refunds/
      settlements to `data/raw/` + manifest. **Only 8 payments / 6 orders / 1
      refund available** (S2S payment creation not enabled on the test account).
- [x] Curated `data/snapshot/` — the 8 real txns as the schema anchor +
      `provenance.md`. Corpus expanded synthetically (user-approved).
- [x] `scripts/generate_synthetic.py` — generates a ~300-record corpus from the
      real templates, then derives `gateway_export.csv` (paise/ISO),
      `invoice_ledger.csv` (rupees/`YYYY-MM-DD`), `bank_statement.csv`
      (rupees/`DD-MM-YYYY`, settlement-level). Deterministic (`--seed`).
- [x] Injected discrepancies — 4 structural + 11 injected cases, all documented
      in `data/synthetic/mismatch_catalogue.md` with expected buckets; counts in
      `data/synthetic/injection_manifest.json`. Includes both failure modes.
- [x] `data/ground_truth/matches.csv` — `record_id, source, true_match_id,
      expected_bucket, case` for all 680 source records.
- [x] `src/recon/eval/metrics.py` — metric set defined; `compute()` stubbed for Day 6.
- [x] **DoD:** `data/{snapshot,synthetic,ground_truth}/` generated; catalogue
      complete. Corpus buckets: ~464 auto / ~112 escalated / ~48 exception /
      ~55 ignored / 1 failed.

### 1.2 Ingest + canonical schema (Day 2 AM) — DONE
- [x] `src/recon/ingest/schema.py` — frozen pydantic `CanonicalTxn`: `txn_id`,
      `source`, `ref_id`, `amount` (Decimal, **positive magnitude, major units**),
      `currency`, `value_date` (date), `counterparty`, `direction`, `status`,
      `fee`, `raw`. Plus `moved_money` (filters failed payments) and
      `signed_amount`. Field validators reject non-positive amounts, bad ISO
      currency codes, blank ids.
- [x] `src/recon/ingest/loaders.py` — `load_gateway` / `load_invoice` /
      `load_bank`, each owning one file dialect (paise+ISO / rupees+`YYYY-MM-DD`
      / rupees+`DD-MM-YYYY`+debit-credit+narration-parsed ref). `load_all()`
      returns `IngestResult(records, rejects)`.
- [x] `src/recon/ingest/validate.py` — field parsers that raise on bad input and
      a `RowErrors` accumulator so a bad row reports **all** its problems;
      `RejectedRow(source, row_number, reason, raw)`.
- [x] `tests/test_ingest.py` — 22 tests: per-source dialects, exact Decimal
      conversion, day-first vs ISO dates, direction from debit/credit, ref
      extraction, immutability, ids aligned with ground truth.
- [x] **DoD:** 680 rows read → **679 normalised + 1 rejected**; the reject names
      both bad fields (`amount: 'N/A' …; captured_at: '2026-13-45T99:99:99' …`)
      and preserves the raw row. `records + rejects == rows on disk` is asserted.

**Fixed three defects in the 1.1 output first** (they would have poisoned
normalisation): invoice rows labelled `currency=USD` while carrying INR amounts
(ledger is now always INR); refund bank rows pointing at `rfnd_…` ids present in
no source file (now correctly no match — they are exceptions); settlement N:1
truth not expressible in one column (added
`data/ground_truth/settlement_groups.csv`, 208 rows).

### 1.3 Deterministic matching (Day 2 PM) — DONE
- [x] `src/recon/matching/exact.py` — 1:1 match on ref + amount + currency + day;
      a key that maps to >1 record on either side is left for the fuzzy pass
      (this is how duplicate references are surfaced, not mis-resolved).
- [x] `src/recon/matching/fuzzy.py` — `score_pair` → `ScoreBreakdown` (amount /
      date / name via rapidfuzz / ref, weighted). Two hard rules: different
      currencies and real amount disagreements are capped below the confident
      threshold, so partial captures / FX slips / duplicates can only reach
      `unmatched-ambiguous`.
- [x] `src/recon/matching/engine.py` — `reconcile()`: filter dead → exact →
      fuzzy (greedy mutual-best with a margin gate) → settlement N:1 (subset-sum,
      so a batch with a held-back payment still foots) → bank exceptions.
      Buckets: `matched | unmatched-ambiguous | unmatched-exception | ignored`.
- [x] Real thresholds in `config/settings.py` (`MATCH_CONFIDENT`, `MATCH_MARGIN`,
      `AMOUNT_*_TOLERANCE`, `DATE_WINDOW_DAYS`, `NAME_SIMILARITY_MIN`,
      `SETTLEMENT_ABS_TOLERANCE`).
- [x] `tests/test_matching.py` — 18 tests incl. the accuracy contract.
- [x] **DoD:** on the full corpus, **bucket accuracy = 100%** against
      `data/ground_truth/`. 464 matched / 112 → LLM / 48 → RAG / 55 ignored.
      Deterministic baseline resolves 68% with zero misbuckets.

**Also fixed a generator data-flow bug:** the bank statement (settlements +
refund/charge debits) is now derived from the *post-injection* gateway rows, so a
settlement credit always foots to its members. Partial-capture / duplicate
payments are `_held` back from settlement.

### 1.4 Audit trail foundation (Day 2, before anything LLM/RAG) — DONE
- [x] `src/recon/audit/logger.py` — `AuditLogger`: append-only JSONL to
      `outputs/audit/run-<run_id>.jsonl`, flushed per line, mirrored in memory.
      Each entry: `run_id`, `seq`, `ts`, `record_id`, `stage`, `decision`,
      `confidence`, `source`, `matched_to`, `inputs`, `rationale`. `path=None` →
      memory only (tests); `AuditLogger.for_run()` → default file.
- [x] Wired into the matching engine — one entry per decision, `rationale` never
      blank, ambiguous entries carry the scored candidates for the LLM stage.
- [x] `tests/test_audit.py` — 15 tests: JSONL validity, round-trip, and that
      `entries on disk == decisions`.
- [x] **DoD:** a matching run writes 679 entries; every decision is explained
      in-line without re-running.

### 1.5 LLM reasoning layer (Day 3) — DONE
- [x] `src/recon/reasoning/llm_client.py` — `GeminiReasoner`. `response_schema`
      (`ReasoningOutput`: `decision ∈ {match,no_match,unsure}`, `matched_candidate_id`,
      `confidence`, `rationale`) via `google-genai`. Model **`gemini-3.6-flash`**
      — `gemini-2.5-flash` is 404 for new API keys now.
- [x] **Replay cache** — SHA-256 of `{prompt_version, model, request}` →
      `data/llm_cache/<key>.json`. Three paths: cache hit / cache-miss+replay
      (deterministic `unsure` fallback, run continues) / live (calls Gemini, writes
      cache). `RECON_LLM_REPLAY_ONLY=1` default. `scripts/populate_llm_cache.py`
      fills it live (rate-limit backoff for the free tier's 5 rpm).
- [x] `src/recon/reasoning/prompts.py` — `PROMPT_VERSION = "recon-reason-v1"`,
      system instruction, output schema. Version stamped on every audit entry and
      folded into the cache key.
- [x] Routes `unmatched-ambiguous` only. `confidence < LLM_CONFIDENCE_MIN` (0.60)
      → escalate. A confident match with a residual amount variance
      (cross-currency / partial) also escalates, but with the LLM's finding
      attached to the rationale — it doesn't force money to move.
- [x] Every call logged to the trail (stage `reason`): record, candidate ids,
      raw model output, mapped outcome, source (`cache|gemini|fallback`).
- [x] `tests/test_reasoning.py` — 14 tests, fully offline (fallback path + seeded
      cache fixtures).
- [x] `fail_once_ids` hook on the reasoner — raises `ReasoningTimeout` once per id
      (the wiring point for task 1.7's retry demo).
- [x] **DoD:** in replay mode with no key, all 112 ambiguous records get a
      structured `unsure`/escalation and the run completes. With the cache
      populated, they get real Gemini judgments.

**Cache population:** `scripts/populate_llm_cache.py` run live over all 112; cache
committed. (In progress / done — see PROGRESS.md.)

### 1.6 RAG grounding layer (Day 4)
- [ ] Collect 5–10 real GST/tax invoicing docs from cbic-gst.gov.in →
      `data/policy/` (PDF/txt). Note source URL + retrieval date per doc.
- [ ] `scripts/build_rag_index.py` — chunk, embed (local sentence-transformers),
      persist ChromaDB index. Rebuildable; index committed or built on first run
      (decide based on size).
- [ ] `src/recon/rag/index.py` — load/query the store.
- [ ] `src/recon/rag/ground.py` — for each `unmatched-exception`: retrieve top-k
      clauses, produce an explanation that **cites the specific clause** (doc +
      section + quoted text).
- [ ] Retrieval quality check: does the cited clause actually fit the exception?
      Record a few worked examples.
- [ ] Log retrieval + explanation to the trail.
- [ ] `tests/test_rag.py` — offline; asserts citations are present and resolve to
      real chunks.
- [ ] **DoD:** every exception record gets a grounded, cited explanation.

### 1.7 Agent orchestration + failure modes (Day 5)
- [ ] `src/recon/agent/orchestrator.py` — the full loop: ingest → match →
      reason → ground → log. Per-record state; collects final buckets
      (auto-resolved / escalated / exception / failed).
- [ ] `src/recon/agent/retry.py` — bounded retry + backoff for transient
      failures; exhausted retries escalate, never crash the run.
- [ ] Injected failure modes, demonstrably handled:
      - malformed row (from 1.1) → rejected, logged, run continues
      - simulated LLM API timeout → retried, then escalated, run continues
- [ ] `scripts/run_pipeline.py` — CLI: `--data`, `--report`, `--live-llm`.
- [ ] `tests/test_agent.py` — both failure modes; run completes with a full
      trail.
- [ ] **DoD:** `python scripts/run_pipeline.py --report` runs clean end-to-end
      from a fresh clone; failure cases visibly handled in output + trail.

### 1.8 Evaluation & hardening (Day 6)
- [ ] Implement `src/recon/eval/metrics.py` against `data/ground_truth/`.
- [ ] Full run → `outputs/reports/metrics.json` + a readable
      `outputs/reports/summary.md`.
- [ ] Record real numbers in `docs/PROGRESS.md` — no cherry-picking.
- [ ] Fix whatever breaks; confirm the failure-handling demo is clean.
- [ ] `pip install -r requirements.txt && python scripts/run_pipeline.py --report`
      verified on a clean checkout (separate clone / clean venv).
- [ ] **DoD:** honest metrics committed; clean-clone run reproduced.

---

## Phase 2 — Polish & Submission (Days 7–8) · NON-NEGOTIABLE

### 2.1 Repo & docs polish (Day 7)
- [ ] README: real quickstart, real metrics table, architecture image,
      "failure modes handled" section, future scope.
- [ ] Prune dead stubs; ensure `ruff check .` and `pytest` are green.
- [ ] `docs/ARCHITECTURE.md` updated to match what was actually built.

### 2.2 Architecture diagram (Day 7)
- [ ] Visual diagram of the pipeline (not ASCII) → `docs/ARCHITECTURE.png` /
      `.svg`, referenced from README.

### 2.3 Audit trail presentation (Day 7)
- [ ] Script or notebook that renders `outputs/audit/*.jsonl` into a readable
      walkthrough (a few representative decisions of each type).

### 2.4 Pitch video (Day 8)
- [ ] 5-min script: problem → architecture → live demo → metrics → one failure
      case handled → future scope.
- [ ] Record + buffer for re-takes.

### 2.5 Final submission checklist (Day 8)
- [ ] Repo public, clean clone works, docs complete, video uploaded, metrics
      match what the video claims.

---

## Phase 3 — Stretch (only if Phase 1 finishes early)

- [ ] 3.1 CV/OCR ingestion for scanned invoices → feeds `recon.ingest`.
- [ ] 3.2 DL-based confidence calibration → augments/replaces LLM confidence.
- [ ] 3.3 One novelty/differentiation feature, benchmarked vs a naive baseline.

---

## Phase 4 — Future Scope (documented only, NOT built)

- [ ] Anything from Phase 3 left undone — write up in README future scope.
- [ ] Patent/IP exploration — forward-looking note only, never a claim.

---

## Design decisions (resolved — Session 3, 2026-08-28)

1. Frozen curated snapshot committed to `data/snapshot/`; live pulls in `data/raw/` (gitignored). ✔
2. LLM replay cache committed to `data/llm_cache/` for offline reproducibility. ✔
3. LLM provider: **Google Gemini**, `google-genai` SDK, model `gemini-2.5-flash`. ✔
4. RAG: local sentence-transformers embeddings + embedded ChromaDB (no API key). ✔
5. Exactly 3 sources: bank statement, invoice ledger, payment gateway export. ✔
6. Python 3.11+ (3.13 on the dev box); pandas + pydantic v2 + rapidfuzz. ✔

Dataset sourcing: user supplies Razorpay **test-mode** API keys; `scripts/pull_razorpay_sandbox.py`
pulls live, then the snapshot is curated by hand from `data/raw/`.
