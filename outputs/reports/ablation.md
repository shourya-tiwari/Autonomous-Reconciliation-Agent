# Ablation — what each layer buys

Same corpus (680 records), same ground truth, same scoring function for every row. Each variant removes a layer and re-scores; a disabled layer leaves its records `escalated` rather than dropping them, so every row below still accounts for all 680 records.

Regenerate with `python scripts/run_ablation.py`.

| Variant | Auto-resolved | Escalated | Bucket acc. | Precision | Recall | F1 | Settlements |
|---------|--------------:|----------:|------------:|----------:|-------:|---:|------------:|
| Exact only | 256 | 320 | 69.4% | 100.0% | 52.2% | 68.6% | 0/80 |
| + fuzzy | 384 | 192 | 88.2% | 100.0% | 78.4% | 87.9% | 0/80 |
| + N:1 settlements | 464 | 112 | 100.0% | 100.0% | 78.4% | 87.9% | 80/80 |
| + LLM + RAG (shipped) | 464 | 112 | 100.0% | 100.0% | 78.4% | 87.9% | 80/80 |

## What each variant is

- **Exact only** — Reference id + amount + currency + day must all agree. This is the naive baseline: a join on the reference column.
- **+ fuzzy** — Adds tolerant scoring over amount / date / counterparty name / reference, gated on a confidence threshold and a margin over the runner-up.
- **+ N:1 settlements** — Adds subset-sum matching of a bank settlement credit against the batch of gateway payments behind it. The complete no-model pipeline.
- **+ LLM + RAG (shipped)** — Adds structured LLM reasoning over the ambiguous bucket and RAG grounding over the exception bucket.

## Reading it honestly

**Precision never moves.** Every variant asserts only pairings it is sure of, so precision stays at 100.0% throughout. The layers buy *recall* — they convert escalations into resolutions — and that is the correct direction to buy it in: a layer that raised recall by lowering precision would be moving money on guesses.

**The deterministic layers do the resolving.** Going from a reference-column join to the full no-model layer takes auto-resolution from 256 to 464 records (+208), and cuts what a human must look at from 320 to 112.

**The LLM and RAG layers barely move the table — and that is the design, not a disappointment.** A confident LLM match that still carries a residual amount variance escalates anyway, because that call belongs to a person. So the model does not convert escalations into resolutions here. What it changes is what the reviewer is handed:

- 20 of the 112 escalations arrive with the model's actual finding attached (the rest fall back to a safe `unsure` — see the LLM cache note in the README) instead of a bare "unresolved".
- 48 of the 48 exceptions arrive with a verbatim GST clause and its source URL instead of a bare "no counterpart".

That is a claim about **review cost**, not about accuracy, and it is stated that way on purpose.

## Routing, versus sending everything to the model

The shipped pipeline sends **112** records to the LLM. A design with no deterministic layer in front of it — the naive agent that asks the model about every line — would send **624** (every record where money actually moved), a **5.6×** difference in model calls for the same buckets.

That multiple is arithmetic over the measured routing, not a simulated run: the naive variant was not executed, because doing so would need a live judgment for every record and the reproducible offline path deliberately has no such cache. Treat it as the call-count ratio it is, not a latency or rupee figure.
