# Project: Autonomous Reconciliation Agent

## Context
Submission for the Razorpay AI Buildathon 2026 — AI Finance Controller track.
Goal: build a working, defensible, end-to-end prototype (not a demo toy) for
Round 1 evaluation. Judges score on: does it run from a clean clone, real
metrics on real(ish) data, at least one gracefully-handled failure, a real
audit trail, and ability to defend design choices live.

## Problem Statement
An autonomous agent that reconciles multi-source transaction records
(bank statement, invoice ledger, payment gateway export), resolves
ambiguous matches via LLM reasoning, and grounds every unmatched
exception in cited policy/tax rules via RAG — with full audit logging
and graceful degradation on malformed input.

## Track
AI Finance Controller (reconciliation-focused).

## Stack (each layer has a real job, not decoration)
- **NLP** — structured field extraction / normalization from transaction records
- **LLM** — reasoning over ambiguous matches (structured output: decision + confidence)
- **RAG** — retrieval over real GST/tax policy docs to ground exception explanations
- **Agent** — orchestrates ingest → match → escalate → log, with retry logic
- **(Stretch, Phase 3 only)** CV/OCR for scanned invoice ingestion, DL-based confidence calibration

## Data Sources (no Kaggle / toy datasets)
- Razorpay sandbox/test-mode API transactions (multi-currency, refunds, partial captures, failed settlements)
- Synthetic mismatches layered on top, with documented failure-mode design (not random noise)
- GST/tax invoicing rules (cbic-gst.gov.in) — RAG corpus
- RBI/NPCI public digital payments data — supplementary realism

## Non-Negotiables for Round 1
1. Runs from a clean clone, no manual fixes
2. Real evaluation numbers (precision/recall/throughput/% auto-resolved) on real(ish) data — never cherry-picked
3. At least one deliberately injected failure mode, shown handled, not just claimed
4. Real audit trail — actual logs of agent decisions and why
5. Clean architecture diagram — judges skim before they read code

## Explicitly Out of Scope for Round 1
- Patent/IP filing — not achievable in 8 days, not what's being judged. Mention only as a forward-looking note in Future Scope, never as a claim.
- CV/OCR — cut from core scope to protect timeline; moved to Phase 3 stretch / Phase 4 future scope.

## Phase Structure
See PLAN.md for day-by-day breakdown.
- Phase 1 (Days 1-6): Core pipeline — non-negotiable
- Phase 2 (Days 7-8): Polish + submission — non-negotiable
- Phase 3 (only if Phase 1 finishes early): CV/OCR, DL confidence calibration, novelty/differentiation features
- Phase 4 (not built): documented in README/pitch as future scope
