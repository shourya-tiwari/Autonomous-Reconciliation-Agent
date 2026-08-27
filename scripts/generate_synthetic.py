"""Day 1 — build the 3 messy sources + inject deliberate, documented mismatches.

Takes data/snapshot/ transactions and derives:
  - bank statement, invoice ledger, payment gateway export (schema drift between them)
  - deliberate mismatches: amount rounding, split/partial payments, duplicate refs,
    missing fields, plus >=1 malformed row and a simulated API-timeout case
Every mismatch class is config-driven and written to a spec file (what + why)
alongside data/ground_truth/ labels.
TODO: implement generators; keep the mismatch catalogue in data/synthetic/.
"""

if __name__ == "__main__":
    raise SystemExit("not implemented yet — see docs/PLAN.md Day 1")
