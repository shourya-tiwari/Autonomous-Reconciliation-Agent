# Autonomous Reconciliation Agent

Razorpay AI Buildathon 2026 — AI Finance Controller track.

An autonomous agent that reconciles multi-source transaction records (bank
statement, invoice ledger, payment gateway export), resolves ambiguous matches
via LLM reasoning, and grounds every unmatched exception in cited GST/tax policy
via RAG — with a full audit trail and graceful degradation on malformed input.

![Pipeline architecture](docs/ARCHITECTURE.svg)

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

This is verified, not asserted: the pipeline was re-run on a separate copy of
exactly what git hands out, in a fresh venv, with no keys in the environment. It
reproduced every number in the table below. Details in
[`docs/PROGRESS.md`](docs/PROGRESS.md) (session 8).

## Results

680 records, scored against `data/ground_truth/`. Full report:
[`outputs/reports/summary.md`](outputs/reports/summary.md).

| Metric | Value | Basis |
|--------|-------|-------|
| Bucket accuracy | **100.0%** | 680/680 records in the expected terminal bucket |
| Match precision | **100.0%** | 384/384 asserted 1:1 pairings correct |
| Match recall | **78.4%** | 384/490 true pairings auto-paired — the rest are *escalated, not missed* |
| Settlement accuracy | **100.0%** | 80/80 N:1 bank settlements matched to the exact batch |
| Throughput | **94.6 rec/s** | after a one-off 19.1 s embedding-model load |
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

Each stage narrows what the next one sees — that is the cost control. The
deterministic layer resolves 68% of the corpus, so only 16% of records ever
reach the LLM and 7% reach RAG. See the diagram above.

| Stage | Package | Role |
|-------|---------|------|
| Ingest | `recon.ingest` | Normalize 3 source schemas; route malformed rows, don't drop them |
| Match | `recon.matching` | Exact → fuzzy → N:1 settlements; buckets: matched / ambiguous / exception / ignored |
| Reasoning | `recon.reasoning` | Gemini, structured `{decision, confidence}`, on ambiguous cases only |
| RAG | `recon.rag` | Retrieve and **quote** GST clauses to explain exceptions |
| Agent | `recon.agent` | Orchestrate, retry transient failures, escalate the rest |
| Audit | `recon.audit` | One JSONL record per decision — the artifact judges inspect |

## Reading the audit trail

Every decision is written to `outputs/audit/run-<id>.jsonl` as it is made — one
JSON object per line, `rationale` never blank. 842 of them for a full run, which
is not something anyone reads top to bottom, so:

```bash
python scripts/show_audit.py                      # a guided tour of the newest run
python scripts/show_audit.py --record INV-2026-00007   # one record, end to end
```

The tour picks one representative decision of each kind — including both
injected failure modes — and quotes it verbatim from the trail. The committed
copy is [`outputs/reports/audit_walkthrough.md`](outputs/reports/audit_walkthrough.md),
readable from a clean clone without running anything first.

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
- `scripts/` — `run_pipeline.py` (the entrypoint), `show_audit.py` (read the trail), `generate_synthetic.py`, `build_rag_index.py`, `populate_llm_cache.py`, `pull_razorpay_sandbox.py`
- `data/` — `snapshot/` (real, committed), `synthetic/` (corpus), `ground_truth/` (answer key), `policy/` (GST corpus), `llm_cache/`, `raw/` (gitignored)
- `outputs/` — `audit/` trail (regenerated per run), `reports/` metrics + walkthrough (committed)
- `docs/` — `PROJECT.md`, `ARCHITECTURE.md` (+ `.svg`), `PLAN.md`, `PROGRESS.md`, `TASKS.md`, `PITCH.md`

## Future scope

Not built, and not claimed to be. Listed because the pipeline was shaped to
leave room for them, not as a roadmap anyone has committed to:

- **Scanned-document ingestion (CV/OCR).** `recon.ingest` normalises everything
  to `CanonicalTxn` behind a per-source loader, so a scanned-invoice loader is a
  fourth dialect rather than a new pipeline. The interesting part is not the OCR
  — it is that OCR confidence would have to propagate into the match score
  instead of being discarded at the boundary.
- **Learned confidence calibration.** Today's cutoffs (`MATCH_CONFIDENT`,
  `LLM_CONFIDENCE_MIN`) are hand-set constants in `config/settings.py`. With
  enough labelled outcomes they could be fitted per discrepancy class, which is
  what would move recall up without giving up the precision that matters.
- **A fuller LLM cache.** 20 of the 112 ambiguous records currently have a real
  Gemini judgment cached; the rest fall back to a safe escalation, which is the
  bucket they belong in either way. Filling the cache changes the *rationales* a
  reviewer reads, not the numbers reported above.
- **More sources and multi-entity netting.** Three sources is the interesting
  minimum; inter-company netting is where reconciliation actually gets hard.

Patent/IP is out of scope for this submission and is noted only as a
forward-looking possibility, not a claim.

## Dev

```bash
pytest                                      # 130 tests
ruff check .                                # lint
python scripts/build_rag_index.py --check   # rebuild the policy index + retrieval gate
python scripts/generate_synthetic.py        # regenerate the corpus (deterministic, --seed)
python scripts/show_audit.py --markdown     # refresh outputs/reports/audit_walkthrough.md
```
