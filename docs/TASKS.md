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

### 1.1 Dataset & ground truth (Day 1)
- [ ] `scripts/pull_razorpay_sandbox.py` — pull test-mode transactions (payments,
      refunds, settlements, orders). Cover multi-currency, refunds, partial
      captures, failed settlements. Write raw JSON to `data/raw/`, record pull
      timestamp + query params.
- [ ] Curate a frozen subset → `data/snapshot/` (committed). Target ~300–800
      transactions — enough for real metrics, small enough to run fast.
- [ ] `scripts/generate_synthetic.py` — derive the 3 source files from the
      snapshot, each with its own schema/column names/date formats:
      `bank_statement.csv`, `invoice_ledger.csv`, `gateway_export.csv` in
      `data/synthetic/`.
- [ ] Inject documented mismatches, config-driven, each written to
      `data/synthetic/mismatch_catalogue.md` (what + why + expected pipeline
      behaviour): amount rounding, split/partial payments, duplicate refs,
      missing fields, FX rounding, timing offset, one malformed row, one
      simulated API-timeout marker.
- [ ] `data/ground_truth/matches.csv` — the true match mapping + expected
      resolution bucket (auto / escalate / exception) for every record.
- [ ] `src/recon/eval/metrics.py` — define (not run) the metric set: match
      precision/recall, % auto-resolved, % escalated, throughput (records/sec).
- [ ] **DoD:** `data/snapshot/` + `data/synthetic/` + `data/ground_truth/`
      committed; mismatch catalogue explains every injected error.

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
      `{decision: match|no_match|unsure, confidence: 0..1, rationale}`. Provider
      per confirmed decision (assumed Anthropic Claude; see open questions).
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

## Open questions blocking clean starts

Carried from `docs/PROGRESS.md` Session 1 — confirm before/at Phase 1 start:
1. Frozen snapshot in `data/snapshot/` acceptable? (vs live pulls)
2. Commit LLM replay cache for offline reproducibility?
3. LLM provider/model — Anthropic Claude `claude-sonnet-5`?
4. RAG: local sentence-transformers + ChromaDB (vs API embeddings)?
5. Exactly 3 sources?
6. Python 3.11+, pandas + pydantic v2 + rapidfuzz?
