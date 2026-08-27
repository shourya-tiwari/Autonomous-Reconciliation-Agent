# Architecture (Draft — refine as built)

## Pipeline Flow

Ingest -> Deterministic Match -> LLM Reasoning (ambiguous cases only) -> RAG Grounding (exceptions only) -> Agent Orchestration (retry / escalate / log) -> Audit Trail Output

## Components

### 1. Ingest
- Reads transaction records from 2-3 sources (bank statement, invoice ledger, gateway export)
- Normalizes schema across sources
- Validates input; malformed records routed to failure-handling path, not silently dropped

### 2. Deterministic Matching Layer
- Exact match: reference id + amount + currency + day (ambiguous keys deferred)
- Fuzzy match: rapidfuzz-scored amount/date/name/ref; a currency or amount
  disagreement is capped below the confident threshold
- Settlement N:1: bank credit vs the batch of gateway payments (subset-sum)
- Output buckets: `matched` / `unmatched-ambiguous` (→ LLM) /
  `unmatched-exception` (→ RAG) / `ignored` (failed payments, filtered pre-match)
- 100% bucket accuracy vs ground truth on the current corpus; resolves ~68%
  deterministically

### 3. LLM Reasoning Layer
- Input: `unmatched-ambiguous` records only (not the full dataset — cost/latency control)
- Google Gemini (`gemini-3.6-flash`) via `google-genai`, `response_schema` for
  structured output: `{decision, matched_candidate_id, confidence, rationale}`
- Content-addressed replay cache (`data/llm_cache/`, committed) — the pipeline
  runs offline with no API key; a cache miss falls back to a safe `unsure`/escalate
- Low confidence, or a confident match with a residual amount variance, escalates
  to a human — with the model's finding attached, never forcing money to move

### 4. RAG Grounding Layer
- Input: `unmatched-exception` records only (refund debits, bank charges)
- Corpus: 9 real GST documents (CGST Act sections + Rules + a circular) in
  `data/policy/`; chunked, embedded with local `sentence-transformers`
  (`all-MiniLM-L6-v2`), stored in ChromaDB (`data/rag_index/`, built on first run)
- Output: a `GroundedExplanation` — summary + recommended action + **verbatim
  quoted citations** (doc title + source). Retrieval + template, no LLM prose, so
  every claim traces to a quote a controller can check
- refund debit → s.34 credit-note rule · bank charge → s.16 / s.17 / r.38 ITC

### 5. Agent Orchestration
- Coordinates the full loop: ingest -> match -> reason -> ground -> log
- Retry logic for transient failures (e.g. API timeout)
- Escalation path for anything the pipeline can't confidently resolve
- At least one deliberate failure mode demonstrated as gracefully handled

### 6. Audit Trail
- `src/recon/audit/logger.py` — append-only JSONL, one object per decision:
  `run_id, seq, ts, record_id, stage, decision, confidence, source, matched_to,
  inputs, rationale`
- `rationale` is never blank; ambiguous decisions carry the scored candidates so
  the LLM stage (and a human) can see what was weighed
- This is the artifact judges will actually inspect — treat it as a first-class output, not incidental logging

## Stretch (Phase 3, not core)
- CV/OCR ingestion for scanned invoices, feeding into step 1
- DL-based confidence calibration, replacing/augmenting LLM confidence scoring in step 3

## Diagram
TODO: convert flow above into a visual diagram for the README / pitch deck once pipeline is stable.
