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

Runs from a clean clone with **no API keys and no network** — it uses the frozen
Razorpay sandbox snapshot in `data/snapshot/` and replayed LLM responses. Keys
are only needed to refresh the dataset or run the reasoning layer live (see
`.env.example`).

## Pipeline

```
Ingest → Deterministic Match → LLM Reasoning → RAG Grounding → Agent Orchestration → Audit Trail
        (all records)         (ambiguous only)  (exceptions only)  (retry / escalate)   outputs/audit/
```

| Stage | Package | Role |
|-------|---------|------|
| Ingest | `recon.ingest` | Normalize 3 source schemas; route malformed rows, don't drop them |
| Match | `recon.matching` | Exact then fuzzy; split into matched / ambiguous / exception |
| Reasoning | `recon.reasoning` | Structured `{decision, confidence}` on ambiguous cases only |
| RAG | `recon.rag` | Cite GST/tax clauses to explain exceptions |
| Agent | `recon.agent` | Orchestrate, retry transient failures, escalate the rest |
| Audit | `recon.audit` | One JSONL record per decision — the artifact judges inspect |

## Layout

- `src/recon/` — the pipeline, one package per stage above
- `scripts/` — `pull_razorpay_sandbox.py`, `generate_synthetic.py`, `build_rag_index.py`, `run_pipeline.py`
- `data/` — `snapshot/` (committed), `raw/` (live pulls, gitignored), `policy/`, `synthetic/`, `ground_truth/`
- `outputs/` — `audit/` trail, `reports/` metrics
- `docs/` — `PROJECT.md`, `ARCHITECTURE.md`, `PLAN.md`, `PROGRESS.md`, `TASKS.md`

## Dev

```bash
pytest        # tests
ruff check .  # lint
```
