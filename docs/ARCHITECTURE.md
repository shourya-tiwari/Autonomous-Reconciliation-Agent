# Architecture (Draft — refine as built)

## Pipeline Flow

Ingest -> Deterministic Match -> LLM Reasoning (ambiguous cases only) -> RAG Grounding (exceptions only) -> Agent Orchestration (retry / escalate / log) -> Audit Trail Output

## Components

### 1. Ingest
- Reads transaction records from 2-3 sources (bank statement, invoice ledger, gateway export)
- Normalizes schema across sources
- Validates input; malformed records routed to failure-handling path, not silently dropped

### 2. Deterministic Matching Layer
- Exact match: reference ID, amount, date
- Fuzzy match: tolerant amount/date/name matching
- Output: matched / unmatched-ambiguous / unmatched-exception

### 3. LLM Reasoning Layer
- Input: unmatched-ambiguous records only (not the full dataset — cost/latency control)
- Output: structured decision (match/no-match) + confidence score
- Low-confidence outputs escalate rather than force a decision

### 4. RAG Grounding Layer
- Input: unmatched-exception records
- Retrieves relevant clauses from GST/tax policy vector store
- Output: natural-language exception explanation citing the retrieved clause

### 5. Agent Orchestration
- Coordinates the full loop: ingest -> match -> reason -> ground -> log
- Retry logic for transient failures (e.g. API timeout)
- Escalation path for anything the pipeline can't confidently resolve
- At least one deliberate failure mode demonstrated as gracefully handled

### 6. Audit Trail
- Every decision (deterministic, LLM, RAG-grounded) logged with: input, decision, confidence/source, timestamp
- This is the artifact judges will actually inspect — treat it as a first-class output, not incidental logging

## Stretch (Phase 3, not core)
- CV/OCR ingestion for scanned invoices, feeding into step 1
- DL-based confidence calibration, replacing/augmenting LLM confidence scoring in step 3

## Diagram
TODO: convert flow above into a visual diagram for the README / pitch deck once pipeline is stable.
