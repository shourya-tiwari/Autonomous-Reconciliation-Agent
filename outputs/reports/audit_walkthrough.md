# Audit trail walkthrough

Every decision the pipeline makes is written to `outputs/audit/run-<id>.jsonl` as it is made -- one JSON object per line, append-only. This file is a guided tour of one such run: a representative entry of each kind, quoted verbatim from the trail, in the order the pipeline decided them.

Regenerate with `python scripts/show_audit.py --markdown`.

- **Run** `20260827T223009.977685Z`
- **Source** `outputs/audit/run-20260827T223009.977685Z.jsonl`
- **Entries** 842

| Count | Stage | Decision |
|------:|-------|----------|
| 464 | `match` | `matched` |
| 112 | `match` | `escalated-to-llm` |
| 112 | `reason` | `escalated` |
| 55 | `match` | `ignored` |
| 48 | `match` | `escalated-to-rag` |
| 48 | `ground` | `grounded` |
| 1 | `ingest` | `rejected` |
| 1 | `agent` | `retry` |
| 1 | `agent` | `completed` |

## 1. Failure mode 1 -- a malformed row is rejected, not dropped

Two fields are unparseable at once and the entry names both, keeps the raw row, and lets the run continue. The record ends in the `failed` bucket, so it still shows up in the totals instead of quietly vanishing from them.

```
seq 1     2026-08-27T22:30:10.000784+00:00
  record      pay_MALFORMED0001
  stage       ingest  ->  rejected
  source      validate:gateway   confidence 1.00
  rationale   amount: 'N/A' is not a whole number of minor units; captured_at: '2026-13-45T99:99:99' is not an ISO-8601 date/timestamp
  inputs
    row_number = 308
    raw:
      pg_payment_id = pay_MALFORMED0001
      pg_order_id = order_MALFORMED01
      captured_at = 2026-13-45T99:99:99
      amount = N/A
      currency = INR
      method = card
      status = captured
      customer_name = 
      fee = 
      tax = 
```


## 2. Failed payments are filtered out before matching

No money moved, so there is nothing to reconcile. Excluding them here -- and saying so on the record -- keeps them out of the accuracy denominator rather than padding it with free wins.

```
seq 2     2026-08-27T22:30:10.001064+00:00
  record      pay_GubDZzstveWEcs
  stage       match  ->  ignored
  source      deterministic:filter   confidence 1.00
  rationale   status='failed': no money moved, excluded from matching
  inputs
    status = failed
```


## 3. The deterministic layer carries the load -- exact

Reference, amount, currency and day all agree, so no model is consulted. This one path resolves 256 of the 680 records.

```
seq 57    2026-08-27T22:30:10.003695+00:00
  record      INV-2026-00001
  stage       match  ->  matched
  source      deterministic:exact   confidence 1.00
  matched_to  pay_0S2YZRFvLYTCbd
  rationale   exact agreement on reference, amount, currency and day
  inputs
    counterpart = pay_0S2YZRFvLYTCbd
```


## 4. ...and fuzzy, when the fields drift

Scored on amount / date / name / ref. A pairing is only accepted here if it clears MATCH_CONFIDENT (0.82) *and* beats the runner-up by MATCH_MARGIN (0.08). Everything else becomes ambiguous on purpose.

```
seq 313   2026-08-27T22:30:10.290270+00:00
  record      INV-2026-00004
  stage       match  ->  matched
  source      deterministic:fuzzy   confidence 0.92
  matched_to  pay_S8RpLRl9XFrA62
  rationale   fuzzy match to pay_S8RpLRl9XFrA62 (amount=1.00, date=0.60, ref=1, name=1.00)
  inputs
    source = invoice
    candidates:
      - txn_id=pay_S8RpLRl9XFrA62  source=gateway  total=0.92  amount=1  date=0.6  name=1  ref=1  same_currency=true
```


## 5. N:1 -- one bank credit against a batch of payments

A settlement credit is matched to the exact subset of gateway payments that foots to it net of fees. The tolerance is deliberately tight, because a loose one lets a wrong subset add up by coincidence.

```
seq 553   2026-08-27T22:30:10.301899+00:00
  record      UTR380370543651
  stage       match  ->  matched
  source      deterministic:settlement-group   confidence 1.00
  matched_to  pay_0S2YZRFvLYTCbd, pay_518Y2bTu5X1YqW
  rationale   batch of 2 payment(s) on 2026-05-29 nets to 5474.71 (fees deducted)
  inputs
    members = 2
    net = 5474.71
```


## 6. Ambiguity reaches the LLM -- and the LLM's answer still does not move money

The deterministic layer capped this pairing because the currencies differ. Gemini returns structured output, not prose, and identifies the counterpart with high confidence. The pipeline escalates anyway: a residual amount variance is a human's call. The model's finding is attached to the escalation, not acted on.

```
seq 314   2026-08-27T22:30:10.290400+00:00
  record      INV-2026-00007
  stage       match  ->  escalated-to-llm
  source      deterministic:fuzzy   confidence 0.55
  rationale   best candidate pay_HOl0amiYY5sIJ0 is a different currency — needs review
  inputs
    source = invoice
    candidates:
      - txn_id=pay_HOl0amiYY5sIJ0  source=gateway  total=0.55  amount=0  date=1  name=1  ref=1  same_currency=false
```

```
seq 681   2026-08-27T22:30:10.358208+00:00
  record      INV-2026-00007
  stage       reason  ->  escalated
  source      cache:recon-reason-v1   confidence 0.95
  rationale   LLM identifies this as pay_HOl0amiYY5sIJ0 but a residual amount variance needs review — The invoice amount of 191,859.20 INR matc…
  inputs
    record:
      txn_id = INV-2026-00007
      source = invoice
      amount = 191859.20
      currency = INR
      value_date = 2026-06-01
      counterparty = Ananya Patel Technologies
      ref_id = order_PHxTXQeUvgoeLM
      status = booked
    candidates = pay_HOl0amiYY5sIJ0
    llm_raw:
      decision = match
      matched_candidate_id = pay_HOl0amiYY5sIJ0
      confidence = 0.95
      rationale = The invoice amount of 191,859.20 INR matches the gateway payment of 2,306.00 USD at an exact exchange rate of 83.20 INR/USD. Both…
```


## 7. A cache miss escalates safely instead of failing

Offline, with no API key and no cached judgment for this record, the reasoner returns a deterministic `unsure` at confidence 0.0 and the record escalates -- carrying the deterministic layer's reason with it. This is what keeps the clean-clone run honest rather than merely green.

```
seq 701   2026-08-27T22:30:10.488305+00:00
  record      INV-2026-00096
  stage       reason  ->  escalated
  source      fallback:recon-reason-v1   confidence 0.00
  rationale   No cached LLM judgment available; escalated for human review. Deterministic layer said: 3 near-equal candidates (pay_EuVx518ox1zt…
  inputs
    record:
      txn_id = INV-2026-00096
      source = invoice
      amount = 284119.00
      currency = INR
      value_date = 2026-07-06
      counterparty = Vivaan Reddy Pvt Ltd
      ref_id = order_x9XOj7h7ug0g4a
      status = booked
    candidates = pay_EuVx518ox1ztD4, pay_6H3JgzqQ4lVS5l, pay_YZ8fOcR9GLw4yW
    llm_raw:
      decision = unsure
      confidence = 0
```


## 8. Failure mode 2 -- a transient API timeout, retried and absorbed

The error is classified as transient, so it is retried with backoff; a malformed request would not be. The retry itself is logged, so 'we handled a timeout' is a checkable claim rather than a story. The second attempt returns, and the record then follows the ordinary reasoning path -- here a cache miss, so it escalates. The timeout cost one record 0.25s, not the run.

```
seq 780   2026-08-27T22:30:10.499880+00:00
  record      pay_8FwHA6as6GWOBJ
  stage       agent  ->  retry
  source      retry:attempt-1   confidence 0.00
  rationale   transient ReasoningTimeout on attempt 1; retrying after 0.25s
  inputs
    error = reasoning call for pay_8FwHA6as6GWOBJ timed out
    backoff_seconds = 0.25
```

```
seq 781   2026-08-27T22:30:10.750688+00:00
  record      pay_8FwHA6as6GWOBJ
  stage       reason  ->  escalated
  source      fallback:recon-reason-v1   confidence 0.00
  rationale   No cached LLM judgment available; escalated for human review. Deterministic layer said: best candidate INV-2026-00216 matches on …
  inputs
    record:
      txn_id = pay_8FwHA6as6GWOBJ
      source = gateway
      amount = 32699.28
      currency = INR
      value_date = 2026-08-19
      counterparty = Sara Reddy LLP
      ref_id = order_RG9vHngYolsZ7L
      status = captured
    candidates = INV-2026-00216, INV-2026-00155
    llm_raw:
      decision = unsure
      confidence = 0
```


## 9. No counterpart at all -- explained against a quoted GST clause

A refund debit has no invoice to match. Rather than reporting a bare unmatched line, the exception is grounded in the retrieved statute with the clause quoted verbatim and its source URL recorded, so a controller can check the citation instead of trusting it.

```
seq 633   2026-08-27T22:30:10.306570+00:00
  record      UTR569276233815
  stage       match  ->  escalated-to-rag
  source      deterministic:no-candidate   confidence 1.00
  rationale   refund debit — no invoice counterpart; needs a GST credit-note explanation
  inputs
    status = refund
    narration = RAZORPAY REFUND pay_0S2YZRFvLYTCbd
```

```
seq 794   2026-08-27T22:30:16.282550+00:00
  record      UTR569276233815
  stage       ground  ->  grounded
  source      rag:Section 34 CGST Act - Credit and debit notes   confidence 0.76
  rationale   INR 3882.00 debit on 2026-06-01 (RAZORPAY REFUND pay_0S2YZRFvLYTCbd) has no matching invoice or gateway record. Treat as a custom…
  inputs
    exception_kind = refund
    citations:
      - doc=Section 34 CGST Act - Credit and debit notes  source=https://cbic-gst.gov.in/pdf/CGST-Act-Updated-31082021.pdf (CGST Act 2017…
      - doc=Section 16 CGST Act - Eligibility and conditions for taking input tax credit  source=https://cbic-gst.gov.in/pdf/CGST-Act-Upd…
      - doc=Section 34 CGST Act - Credit and debit notes  source=https://cbic-gst.gov.in/pdf/CGST-Act-Updated-31082021.pdf (CGST Act 2017…
```


## 10. A different exception grounds in a different clause

The bank charge is an input-tax-credit question, not a credit-note one, and retrieval routes it to the ITC sections. Retrieval is doing real work here -- it is not one canned answer wearing two labels.

```
seq 796   2026-08-27T22:30:16.320560+00:00
  record      UTR708958838017
  stage       ground  ->  grounded
  source      rag:Section 16 CGST Act - Eligibility and conditions for taking input tax credit   confidence 0.80
  rationale   INR 236.00 debit on 2026-06-07 (ACCT MAINTENANCE CHARGE) has no matching invoice or gateway record. Treat as a bank charge / fina…
  inputs
    exception_kind = charge
    citations:
      - doc=Section 16 CGST Act - Eligibility and conditions for taking input tax credit  source=https://cbic-gst.gov.in/pdf/CGST-Act-Upd…
      - doc=Rule 38 CGST Rules - Claim of credit by a banking company or a financial instit…  source=https://cbic-gst.gov.in/pdf/cgst-rul…
      - doc=Rule 38 CGST Rules - Claim of credit by a banking company or a financial instit…  source=https://cbic-gst.gov.in/pdf/cgst-rul…
```


## 11. The run closes its own books

The final entry reconciles the reconciler: every input row is accounted for in exactly one terminal bucket, and the counts are logged next to the elapsed time that produced them.

```
seq 842   2026-08-27T22:30:17.169842+00:00
  record      __run__
  stage       agent  ->  completed
  source      orchestrator   confidence 1.00
  rationale   processed 680 records in 7.19s; 1 retry(ies)
  inputs
    auto_resolved = 464
    escalated = 112
    exception = 48
    ignored = 55
    failed = 1
    total = 680
    elapsed_seconds = 7.19
    throughput_rps = 94.6
```
