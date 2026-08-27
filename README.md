# Autonomous Reconciliation Agent

Razorpay AI Buildathon 2026 — AI Finance Controller track.

An autonomous agent that reconciles multi-source transaction records (bank
statement, invoice ledger, payment gateway export), resolves ambiguous matches
via LLM reasoning, and grounds every unmatched exception in cited GST/tax policy
via RAG — with a full audit trail and graceful degradation on malformed input.

See [`docs/`](docs/) for the problem statement, architecture, and 8-day plan.

## Quickstart

```bash
python -m venv .venv && . .venv/Scripts/activate    # Windows; use .venv/bin/activate on macOS/Linux
pip install -r requirements.txt
python scripts/run_pipeline.py --report
```

Runs from a clean clone with **no API keys**. The corpus (`data/synthetic/`) is
committed, and LLM judgments are replayed from `data/llm_cache/` — a cache miss
escalates safely rather than failing. The one network call is a first-run
download of the ~90 MB embedding model used for RAG retrieval; it is cached
afterwards, and if it cannot be fetched the exceptions are still reported
(without their citations) rather than the run failing. API keys are only needed
to refresh the dataset or call Gemini live — see `.env.example`.

## Results

680 records, scored against `data/ground_truth/`. Full report:
[`outputs/reports/summary.md`](outputs/reports/summary.md).

| Metric | Value | Basis |
|--------|-------|-------|
| Bucket accuracy | **100.0%** | 680/680 records in the expected terminal bucket |
| Match precision | **100.0%** | 384/384 asserted 1:1 pairings correct |
| Match recall | **78.4%** | 384/490 true pairings auto-paired — the rest are *escalated, not missed* |
| Settlement accuracy | **100.0%** | 80/80 N:1 bank settlements matched to the exact batch |
| Throughput | **99 rec/s** | after a one-off ~16 s model load |
| LLM usage | **16%** | only 112 of 680 records reach the model |

Recall is below 100% on purpose: partial captures, duplicate references and FX
variances are escalated to a human rather than guessed. Precision is what must
not slip when the output moves money.

**Workload profile** — 68.2% auto-resolved · 16.5% escalated · 7.1% exceptions ·
8.1% ignored (failed payments) · 0.1% failed (the malformed row).

## Failure modes, handled

| Injected | What happens |
|----------|--------------|
| **Malformed row** (`amount="N/A"`, broken timestamp) | Rejected at ingest with a per-field reason, logged, run continues → bucket `failed` |
| **LLM API timeout** | Retried with exponential backoff and absorbed; the retry is in the audit trail. Exhausted retries escalate that one record — the run still finishes |

## Pipeline

```
Ingest → Deterministic Match → LLM Reasoning → RAG Grounding → Agent Orchestration → Audit Trail
        (all records)         (ambiguous only)  (exceptions only)  (retry / escalate)   outputs/audit/
```

| Stage | Package | Role |
|-------|---------|------|
| Ingest | `recon.ingest` | Normalize 3 source schemas; route malformed rows, don't drop them |
| Match | `recon.matching` | Exact → fuzzy → N:1 settlements; buckets: matched / ambiguous / exception / ignored |
| Reasoning | `recon.reasoning` | Gemini, structured `{decision, confidence}`, on ambiguous cases only |
| RAG | `recon.rag` | Retrieve and **quote** GST clauses to explain exceptions |
| Agent | `recon.agent` | Orchestrate, retry transient failures, escalate the rest |
| Audit | `recon.audit` | One JSONL record per decision — the artifact judges inspect |

## Data

`data/snapshot/` holds **8 real transactions** pulled from the live Razorpay
test-mode API — the schema-fidelity anchor. Razorpay's server-to-server payment
API is not enabled on a standard test account, so the ~300-record evaluation
corpus in `data/synthetic/` is generated from those templates with distributions
calibrated from the real pull. The 300 are **not** claimed to be real; see
[`data/snapshot/provenance.md`](data/snapshot/provenance.md) and
[`data/synthetic/mismatch_catalogue.md`](data/synthetic/mismatch_catalogue.md),
which documents all 15 discrepancy cases and the pipeline behaviour each expects.

The RAG corpus is 9 real GST documents from cbic-gst.gov.in —
[`data/policy/SOURCES.md`](data/policy/SOURCES.md).

## Layout

- `src/recon/` — the pipeline, one package per stage above
- `scripts/` — `pull_razorpay_sandbox.py`, `generate_synthetic.py`, `build_rag_index.py`, `populate_llm_cache.py`, `run_pipeline.py`
- `data/` — `snapshot/` (real, committed), `synthetic/` (corpus), `ground_truth/` (answer key), `policy/` (GST corpus), `llm_cache/`, `raw/` (gitignored)
- `outputs/` — `audit/` trail (regenerated), `reports/` metrics (committed)
- `docs/` — `PROJECT.md`, `ARCHITECTURE.md`, `PLAN.md`, `PROGRESS.md`, `TASKS.md`

## Dev

```bash
pytest                                  # 117 tests
ruff check .                            # lint
python scripts/build_rag_index.py --check   # rebuild the policy index + retrieval gate
python scripts/generate_synthetic.py    # regenerate the corpus (deterministic, --seed)
```
