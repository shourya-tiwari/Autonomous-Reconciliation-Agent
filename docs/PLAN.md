# 8-Day Build Plan

Time budget: ~6-8 hrs/day, solo build.

## Day 1 — Dataset — DONE
- [x] Pull real transactions from Razorpay sandbox/test-mode API — only 8 payments / 6 orders / 1 refund available (S2S payment creation not enabled on the test account). Kept as the schema anchor in `data/snapshot/`; corpus expanded synthetically from them. See `data/snapshot/provenance.md`.
- [x] Construct 3 messy transaction sources — `gateway_export.csv` (paise, ISO-8601), `invoice_ledger.csv` (rupees, `YYYY-MM-DD`, INR-denominated), `bank_statement.csv` (rupees, `DD-MM-YYYY`, settlement-level)
- [x] Design deliberate mismatches, each documented with rationale + expected behaviour — 4 structural + 11 injected cases in `data/synthetic/mismatch_catalogue.md`
- [x] Define evaluation metrics upfront — `src/recon/eval/metrics.py` (defined Day 1, computed Day 6)

## Day 2 — Deterministic Matching Layer
- [x] Ingest + canonical schema (prerequisite, task 1.2) — 680 rows → 679 normalised + 1 rejected
- [x] Exact match (ref + amount + currency + day); ambiguous keys deferred to fuzzy
- [x] Fuzzy match (rapidfuzz name similarity, amount/date tolerance) + N:1 settlement subset-sum
- [x] The accuracy floor: 100% bucket accuracy, 68% of the corpus resolved deterministically. Audit logging wired in from here on

## Day 3 — LLM Reasoning Layer — DONE
- [x] Only deterministic-layer punts (112 ambiguous records, 16% of the corpus) go to the LLM
- [x] Structured output only — Gemini `response_schema` → `{decision, matched_candidate_id, confidence, rationale}`
- [x] Every call logged with its input context, raw output and mapped outcome; committed replay cache keeps it reproducible offline

## Day 4 — RAG Layer — DONE
- [x] Vector store from 9 real GST documents (CGST Act ss.16/17/31/34/54, Rules 38/46/53, Circular 160/16/2021)
- [x] Exception explanations quote the retrieved clause verbatim, with document title and source URL
- [x] Retrieval quality gate in `build_rag_index.py --check`: refund → s.34, bank charge → s.16/r.38

## Day 5 — Agent Orchestration — DONE
- [x] Full loop: ingest → match → reason → ground → log; every row lands in exactly one terminal bucket
- [x] Bounded retry with exponential backoff; only genuinely transient errors are retried
- [x] **Two** failure modes injected and demonstrated: malformed row rejected with a reason, LLM timeout retried and absorbed — both visible in the run output and the audit trail

## Day 6 — Evaluation — DONE
- [x] Full pipeline run on the complete 680-record dataset
- [x] Real metrics: bucket accuracy 100%, match precision 100% (384/384), recall 78.4%, settlement accuracy 100% (80/80), 94.6 rec/s, 68.2% auto-resolved / 16.5% escalated
- [x] Fixed in the Phase-1 scan: an offline first run could not fetch the embedding model and killed the pipeline; it now degrades instead
- [x] Failure-handling demo is clean and printed by `run_pipeline.py`

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
