# Mismatch Catalogue

Every deliberate discrepancy injected by `scripts/generate_synthetic.py`, why it
is realistic, and what the pipeline is expected to do with it. Judges use this to
check the failure handling is designed, not accidental.

The generator starts from the curated Razorpay snapshot (`data/snapshot/`) — each
payment is a real transaction — then derives three source files with independent
schemas and injects the cases below at a configurable rate.

## The three sources

| Source | Represents | Schema quirks |
|--------|-----------|---------------|
| `bank_statement.csv` | money actually moved in the bank account | settlement-level rows, amount in rupees, `DD-MM-YYYY`, terse `narration` text, no payment ids |
| `invoice_ledger.csv` | what finance expected to receive | per-invoice rows, amount in rupees with tax split out, `YYYY-MM-DD`, customer name + GSTIN |
| `gateway_export.csv` | Razorpay's own record | per-payment rows, amount in **paise**, unix timestamp, `pg_txn_id` + `order_id` |

A clean transaction appears once in each file and reconciles exactly. The cases
below break that.

## Injected cases

| # | Case | How it's injected | Realistic because | Expected pipeline behaviour |
|---|------|-------------------|-------------------|-----------------------------|
| 1 | **Amount rounding** | gateway paise → bank rupees rounded to nearest ₹1 | banks round; FX and fee math lose sub-rupee precision | fuzzy match within `AMOUNT_ABS_TOLERANCE` → `auto_resolved` |
| 2 | **Payment gateway fee deducted** | bank row = payment − Razorpay fee − GST on fee | settlements arrive net of fees | fuzzy match on (amount + known fee model) → `auto_resolved`, note the fee |
| 3 | **Split settlement** | N gateway payments → 1 bank row summing them | Razorpay batches a day's payments into one settlement | ambiguous (no 1:1) → LLM reasons N:1 → `auto_resolved` or `escalated` |
| 4 | **Partial capture** | gateway amount < order amount; invoice = full | authorised more than captured | ambiguous → LLM → `escalated` with rationale |
| 5 | **Duplicate reference id** | two payments reuse one `order_id` | client retries, idempotency slip | exact match would be 1:many → forced to fuzzy/LLM → `escalated` |
| 6 | **Timing offset** | bank `value_date` is payment date + 1–3 days | settlement cycle / weekends | fuzzy match within `DATE_WINDOW_DAYS` → `auto_resolved` |
| 7 | **Counterparty name drift** | invoice "ACME Corp Pvt Ltd" vs bank "ACME CORPORATION" | free-text bank narration | rapidfuzz name similarity ≥ `NAME_SIMILARITY_MIN` → `auto_resolved` |
| 8 | **Missing field** | drop `order_id` from ~5% of gateway rows | export gaps, integration bugs | no exact key → fuzzy on amount+date, or `escalated` |
| 9 | **FX rounding (multi-currency)** | USD payment, bank row in INR at a slightly-off rate | conversion timing | ambiguous → LLM with FX context → `escalated` |
| 10 | **Unmatched — refund** | bank debit with no matching invoice (it's a refund) | refunds aren't invoiced | no candidate → `exception` → RAG cites the GST credit-note rule |
| 11 | **Unmatched — bank fee** | small bank debit (monthly charges) not in any ledger | bank charges | `exception` → RAG cites input-tax-credit / expense treatment |
| 12 | **Malformed row** | one gateway row with a non-numeric amount and a broken date | corrupt export line | `recon.ingest.validate` rejects it with a reason, logs it, run continues — **failure mode 1** |
| 13 | **Simulated API timeout** | one ambiguous record flagged so `llm_client` raises a timeout | transient LLM/API failure | `recon.agent.retry` retries with backoff, then `escalated`, run continues — **failure mode 2** |

## Config

Rates and the specific record ids chosen for cases 12–13 live in
`scripts/generate_synthetic.py` (constant `INJECTION_PLAN`) and are echoed into
`data/synthetic/injection_manifest.json` on every generation so a run is
reproducible.
