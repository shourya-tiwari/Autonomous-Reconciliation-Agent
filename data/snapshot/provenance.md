# Snapshot provenance

These files are a **verbatim, curated pull from the live Razorpay test-mode API**
(`scripts/pull_razorpay_sandbox.py`, 2026-08-27). Nothing here is synthetic.

| File | Records | Notes |
|------|---------|-------|
| `payments.json` | 8 | 4 captured, 3 failed, 1 refunded. All INR. Methods: card, netbanking, wallet. Real `fee`/`tax` on captured. |
| `orders.json` | 6 | linked to the payments above |
| `refunds.json` | 1 | full refund of `pay_TUvIIpzEisUOVb` |
| `pull_manifest.json` | — | pull timestamp, key mode, date window |

## Why only 8

The Razorpay S2S payment-creation API (`payments/create/json`) is not enabled on
this test account, so test payments can't be generated programmatically at scale,
and manual checkout for hundreds is not practical.

**Role of this snapshot:** it is the *schema-fidelity anchor* — proof that the
pipeline pulls from the real Razorpay API and handles real Razorpay object shapes
(field names, paise amounts, `fee`/`tax` structure, status lifecycle, order
linkage).

The evaluation corpus (~300 records) is built by `scripts/generate_synthetic.py`,
which uses these 8 as templates and generates additional Razorpay-shaped
transactions with distributions calibrated from this pull. Reconciliation failure
modes are then injected per `data/synthetic/mismatch_catalogue.md`. The README and
pitch state this provenance explicitly — the 300 records are **not** claimed to be
real.
