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

**Cache population:** `scripts/populate_llm_cache.py`, run live. **~20/112 done —
the Gemini free-tier daily quota is exhausted.** Options: enable billing (fastest,
costs pennies), switch to a `-flash-lite` model, or run over several days. The
pipeline runs fine meanwhile (cache miss → escalate fallback).

### 1.6 RAG grounding layer (Day 4) — DONE
- [x] **9 real GST documents** from cbic-gst.gov.in in `data/policy/` — CGST Act
      ss.16/17/31/34/54, CGST Rules 38/46/53, Circular 160/16/2021. Each `.txt`
      carries its `Source:` URL + `Retrieved:` date; `SOURCES.md` maps each to
      what it governs. Extracted from the official PDFs with `pypdf`; amendment
      footnotes / running headers stripped.
- [x] `scripts/build_rag_index.py` — chunks (sub-section aware, ~700 chars),
      embeds with local `sentence-transformers` (`all-MiniLM-L6-v2`), persists
      ChromaDB to `data/rag_index/`. **Not committed** (binary, non-deterministic)
      — auto-builds on first query, or run the script. `--check` runs the
      retrieval gate.
- [x] `src/recon/rag/index.py` — `PolicyIndex`: `load_chunks`, `build`, `query`
      → `Retrieved` with cosine similarity.
- [x] `src/recon/rag/ground.py` — `ground_exception`: exception kind → targeted
      query → top-k clauses → `GroundedExplanation` (summary + action + verbatim
      quoted citations, doc title + source). No LLM prose — retrieval + template,
      so every claim traces to a quote.
- [x] Retrieval gate (in the build script + `tests/test_rag.py`): refund query →
      s.34 (credit notes) in top-3; charge query → s.16/s.17/r.38 (ITC).
- [x] `run_grounding()` logs every grounding to the trail (stage `ground`): cited
      doc, score, exception kind.
- [x] `tests/test_rag.py` — 12 tests: corpus shape, chunk sizing, retrieval
      quality, verbatim-quote check, audit coverage. (Skips if the embedding
      model can't load offline.)
- [x] **DoD:** all 48 exception records get a grounded, cited explanation
      (43 refund → s.34, 5 charge → s.16/r.38).

### 1.7 Agent orchestration + failure modes (Day 5) — DONE
- [x] `src/recon/agent/orchestrator.py` — `run_pipeline()` drives the full loop
      and reduces every input row to one terminal bucket: `auto_resolved |
      escalated | exception | ignored | failed`. Every line accounted for.
- [x] `src/recon/agent/retry.py` — bounded retry + exponential backoff.
      **Only transient** errors are retried (a malformed request is not).
      `RetryingReasoner` reports which record it retried, so the retry lands in
      the audit trail.
- [x] Injected failure modes, demonstrably handled:
      - malformed row → rejected at ingest with a per-field reason, logged,
        run continues → `failed`
      - LLM API timeout (armed via `injection_manifest.json`) → retried with
        backoff, absorbed; exhausted retries escalate that one record and the run
        still finishes (both covered by tests)
- [x] **Bonus, found in the Phase-1 scan:** an unavailable policy store (offline
      first run, embedding model not downloadable) now degrades — exceptions are
      still reported without citations instead of killing the run.
- [x] `scripts/run_pipeline.py` — `--data`, `--report`, `--live-llm`,
      `--no-inject`. Warms the policy index first so the one-off model load
      doesn't inflate throughput.
- [x] `tests/test_agent.py` — 19 tests.
- [x] **DoD met:** `python scripts/run_pipeline.py --report` runs end to end; both
      failure modes are printed in the output and present in the trail.

### 1.8 Evaluation & hardening (Day 6) — DONE
- [x] `src/recon/eval/metrics.py` implemented against `data/ground_truth/`.
      N:1 settlements scored separately (a single `true_match_id` column can't
      express a group); setup time reported beside throughput, not folded in.
- [x] Full run → `outputs/reports/metrics.json` + `summary.md`, **both committed**
      as the evidence for the numbers quoted.
- [x] Real numbers recorded in `docs/PROGRESS.md` and the README.
- [x] `tests/test_eval.py` — 14 tests, including hand-built cases pinning the
      precision/recall arithmetic so a scoring bug can't flatter the result.
- [x] **DoD met:** honest metrics committed.

**Measured (680 records):** bucket accuracy **100%** · match precision **100%**
(384/384) · match recall **78.4%** (384/490 — the rest escalated, not missed) ·
settlement accuracy **100%** (80/80) · throughput **99 rec/s** · **16%** of
records reach the LLM · 1 retry absorbed · 1 malformed row rejected.

- [ ] Re-verify on a genuinely separate clone + clean venv (deferred to task 2.5;
      the local venv already proves `pip install -r requirements.txt` works).

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
