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

### 1.2 Ingest + canonical schema (Day 2 AM)
- [ ] `src/recon/ingest/schema.py` — pydantic `CanonicalTxn` model: `txn_id`,
      `source`, `ref_id`, `amount` (Decimal), `currency`, `value_date`,
      `counterparty`, `direction`, `raw` (original dict).
- [ ] `src/recon/ingest/loaders.py` — one loader per source, each mapping its
      schema → `CanonicalTxn`, returning `(records, rejects)`.
- [ ] `src/recon/ingest/validate.py` — type/format checks; malformed rows become
      `RejectedRow(reason, raw)` routed to the failure path, never dropped.
- [ ] `tests/test_ingest.py` — schema mapping per source; malformed row is
      rejected with a reason, not silently lost.
- [ ] **DoD:** all 3 synthetic sources load to a single normalized list; the
      malformed row surfaces as a reject.

### 1.3 Deterministic matching (Day 2 PM)
- [ ] `src/recon/matching/exact.py` — 1:1 match on (ref_id, amount, value_date);
      exact hits removed from the pool.
- [ ] `src/recon/matching/fuzzy.py` — tolerant match: amount within
      `AMOUNT_ABS_TOLERANCE`, date within `DATE_WINDOW_DAYS`, name similarity
      ≥ `NAME_SIMILARITY_MIN` (rapidfuzz). Produce scored candidates.
- [ ] Classifier → `matched` | `unmatched-ambiguous` (has candidates, none
      confident) | `unmatched-exception` (no candidate at all).
- [ ] Set real threshold values in `config/settings.py`.
- [ ] `tests/test_matching.py` — each injected mismatch lands in the intended
      bucket.
- [ ] **DoD:** running matching on the dataset produces the three buckets with
      sane counts; this is the accuracy baseline.

### 1.4 Audit trail foundation (Day 2, before anything LLM/RAG)
- [ ] `src/recon/audit/logger.py` — `AuditLogger` appending one JSONL record per
      decision to `outputs/audit/run-<timestamp>.jsonl`: `record_id`, `stage`,
      `decision`, `confidence`, `source` (rule id / model / retrieved clause),
      `inputs`, `timestamp`, `run_id`.
- [ ] Wire it into matching (1.3) retroactively.
- [ ] `tests/` — a run produces a valid, parseable trail.
- [ ] **DoD:** every matching decision is in the trail with enough context to
      explain it without rerunning.

### 1.5 LLM reasoning layer (Day 3)
- [ ] `src/recon/reasoning/llm_client.py` — structured-output call returning
      `{decision: match|no_match|unsure, confidence: 0..1, rationale}`. Google
      Gemini via the `google-genai` SDK, `response_schema` for structured output,
      model `gemini-2.5-flash` (`config.settings.LLM_MODEL`).
- [ ] **Replay cache**: hash the request → store/lookup response under
      `data/llm_cache/`. `RECON_LLM_REPLAY_ONLY=1` (default) never hits network;
      live mode records new entries. Commit the cache.
- [ ] `src/recon/reasoning/prompts.py` — versioned prompt + output schema; audit
      records the prompt version.
- [ ] Route `unmatched-ambiguous` only. `confidence < LLM_CONFIDENCE_MIN` →
      escalate, don't force.
- [ ] Log every call (input context, raw output, parsed result) to the trail.
- [ ] `tests/test_reasoning.py` — runs fully offline against the cache.
- [ ] **DoD:** ambiguous records get a structured decision or an escalation;
      pipeline still runs with no API key.

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
