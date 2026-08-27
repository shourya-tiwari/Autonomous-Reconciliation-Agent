# Reconciliation run — evaluation summary

Corpus: **680 records** across 3 sources (`gateway_export.csv`, `invoice_ledger.csv`, `bank_statement.csv`).
Wall clock: **6.92s** (98.3 records/s), after a one-off 15.62s embedding-model load (30.2/s including it).

## Accuracy

| Metric | Value | Basis |
|--------|-------|-------|
| Bucket accuracy | **100.0%** | 680/680 records in the expected terminal bucket |
| Match precision | **100.0%** | 384/384 asserted 1:1 pairings correct |
| Match recall | **78.4%** | 384/490 true 1:1 pairings auto-paired |
| Match F1 | **87.9%** | harmonic mean |
| Settlement accuracy | **100.0%** | 80/80 N:1 bank settlements matched to the exact batch |

Recall is **78.4%**, not 100%, on purpose. The 106 pairings it does not assert are the genuinely ambiguous ones — partial captures, duplicate references, FX variances — which are escalated to a human rather than guessed. Read it alongside precision: the pipeline never asserted a wrong pairing. Trading recall for precision is the right direction when the output moves money.

## Workload profile

How the corpus resolves — this is what a controller would actually face.

| Outcome | Share | Count | Meaning |
|---------|-------|-------|---------|
| auto_resolved | 68.2% | 464 | reconciled with no human involvement |
| escalated | 16.5% | 112 | a real ambiguity, sent to a human with the LLM's finding |
| exception | 7.1% | 48 | no counterpart; explained and cited from GST policy |
| ignored | 8.1% | 55 | failed payments — no money moved |
| failed | 0.1% | 1 | unparseable row, rejected with a reason |

## Cost control

- **112** of 680 records reached the LLM (16%) — the deterministic layer absorbs the rest.
- **48** reached RAG.
- LLM response source: {'cache': 20, 'gemini': 0, 'fallback': 92} (`cache` = replayed from the committed cache, `fallback` = no cached judgment, escalated safely).

## Failure modes handled

- **Malformed row** — 1 row(s) rejected at ingest with a per-field reason, logged, run continued.
- **LLM API timeout** — 1 transient failure(s) retried with backoff and absorbed; no record lost.

## Misbucketed records

None.
