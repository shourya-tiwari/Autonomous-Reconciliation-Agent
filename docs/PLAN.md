# 8-Day Build Plan

Time budget: ~6-8 hrs/day, solo build.

## Day 1 — Dataset
- [ ] Pull real transactions from Razorpay sandbox/test-mode API (multi-currency, refunds, partial captures, failed settlements)
- [ ] Construct 2-3 messy transaction sources (bank statement, invoice ledger, payment gateway export)
- [ ] Design deliberate mismatches: amount rounding, split payments, duplicate refs, missing fields — document *why* each one is included
- [ ] Define evaluation metrics upfront: match precision/recall, % auto-resolved, % escalated, throughput

## Day 2 — Deterministic Matching Layer
- [ ] Exact match (ref/amount/date)
- [ ] Fuzzy match (tolerant amount/date/name matching)
- [ ] This is the accuracy floor / baseline — log everything from this point on

## Day 3 — LLM Reasoning Layer
- [ ] Route deterministic-layer punts (ambiguous cases) to LLM
- [ ] Structured output only: decision + confidence score (no free-form prose as the primary output)
- [ ] Log every LLM decision + input context for audit trail

## Day 4 — RAG Layer
- [ ] Build vector store from 5-10 real GST/tax policy documents
- [ ] Wire exception explanations to cite retrieved clauses
- [ ] Validate retrieval quality (does the cited clause actually match the exception?)

## Day 5 — Agent Orchestration
- [ ] Full loop: ingest → match → escalate → log
- [ ] Retry logic for transient failures
- [ ] Inject at least one deliberate failure mode (malformed row, API timeout, etc.) and handle it gracefully — this must be demonstrable, not theoretical

## Day 6 — Evaluation
- [ ] Run full pipeline on complete dataset
- [ ] Compute real metrics (precision/recall/throughput/% auto-resolved/% escalated)
- [ ] Fix whatever breaks
- [ ] Confirm the failure-handling case is demo-ready

## Day 7 — Repo & Docs Polish
- [ ] Clean repo structure, clear README
- [ ] Architecture diagram (visual, not just text)
- [ ] Audit trail output — make it readable/presentable, not raw logs

## Day 8 — Pitch Video
- [ ] Record 5-min video: problem → architecture → live demo → metrics → one failure case shown handled → future scope
- [ ] Buffer time for re-records
- [ ] Final submission check (repo public? docs complete? video uploaded?)

## Phase 3 (Stretch — only if ahead of schedule)
- [ ] CV/OCR layer for scanned invoice ingestion
- [ ] DL-based confidence calibration
- [ ] Novelty/differentiation feature, benchmarked against naive baseline

## Phase 4 (Future Scope — documented only, not built)
- [ ] Anything from Phase 3 not completed
- [ ] Patent/IP exploration — noted as forward-looking, not claimed
