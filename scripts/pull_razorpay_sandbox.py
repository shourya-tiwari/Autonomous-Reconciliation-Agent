"""Day 1 — pull real transactions from the Razorpay sandbox / test-mode API.

Writes raw pulls to data/raw/. A curated, frozen subset is then copied to
data/snapshot/ and committed so the pipeline runs from a clean clone offline.

Needs RAZORPAY_KEY_ID / RAZORPAY_KEY_SECRET in the environment.
TODO: pagination; capture multi-currency, refunds, partial captures, failed
settlements; record the pull timestamp + query params for provenance.
"""

if __name__ == "__main__":
    raise SystemExit("not implemented yet — see docs/PLAN.md Day 1")
