# Pitch video script — 5 minutes

Razorpay AI Buildathon 2026 · AI Finance Controller track.

Structure: problem → architecture → live demo → metrics → one failure handled →
future scope.

**768 spoken words.** That is 5:07 at a deliberate 150 wpm and 4:39 at a more
typical 165 — so time a read-through before you record rather than trusting the
section markers below. Budget another ~20s for the pipeline actually running and
for scrolling in the demo. If you come out long, cut from the problem section:
it is the one part that carries no evidence. Don't cut the demo.

Every number below is from [`../outputs/reports/metrics.json`](../outputs/reports/metrics.json).
If a re-run changes them, change them here too rather than quoting the old ones.

---

## Before recording

```bash
python -m pytest -q                        # 130 passed
ruff check .                               # clean
python scripts/build_rag_index.py          # warm the embedding model *before* recording
python scripts/run_pipeline.py --report    # confirm the numbers you're about to quote
```

Warm the index first. Otherwise the first thing on camera is a 19-second model
download with nothing to look at. The run itself takes about 7 seconds, which is
the right length to talk over.

Terminal at a legible font size, window wide enough that the summary table
doesn't wrap. Have `outputs/reports/audit_walkthrough.md` open in a second tab.

---

## 0:00 – 0:42 · The problem

> **On screen:** the three source CSVs side by side, scrolled to a row that
> disagrees.

"A finance team closing the books gets the same transaction three times: from the
payment gateway, from their own invoice ledger, from the bank statement.
Different amount units, different date formats, different identifiers. Most lines
agree. The ones that don't are the job — a partial capture, a refund with no
invoice behind it, a settlement that batches several payments into one credit.

Today a controller works those by hand. The naive automation hands the whole file
to an LLM and takes its word for it: expensive, slow, and when the output moves
money, not defensible. You can't show an auditor a chat log.

So the question isn't 'can a model match transactions'. It's what a
reconciliation agent has to do to be *trusted* with the ones it can't match."

## 0:42 – 1:32 · Architecture

> **On screen:** `docs/ARCHITECTURE.svg`.

"Five stages, each narrowing what the next one sees.

Ingest normalises three dialects into one schema. Deterministic matching does
exact, then fuzzy, then N-to-1 settlement matching — that layer alone resolves
68% of the corpus with no model involved.

Only what's left goes further, and it splits two ways. Genuinely ambiguous
pairings — 16% of records — go to Gemini, which returns structured output, a
decision and a confidence, not prose. Records with no counterpart at all — 7% —
go to a RAG layer over nine real GST documents from cbic-gst.gov.in, which
explains the exception and quotes the clause it relies on.

That routing is the cost argument: 84% never reaches the LLM. And every decision,
at every stage, is written to an append-only audit trail as it's made."

## 1:32 – 3:05 · Live demo

> **On screen:** the terminal. Run it live.

```bash
python scripts/run_pipeline.py --report
```

"This is a clean clone with no API keys set. It runs anyway — the corpus is
committed and the LLM judgments replay from a cache in the repo, so these numbers
are reproducible on your machine, not just mine.

680 records, about seven seconds.

Every input row lands in exactly one bucket. 464 auto-resolved. 112 escalated —
real ambiguity going to a human. 48 exceptions with a cited explanation. 55
ignored, failed payments where no money moved. One failed, which we'll come back
to. That adds to 680: nothing was dropped to make the totals look better."

> **On screen:** switch to `python scripts/show_audit.py`, scroll to scene 6.

"Here's the part I'd want to see if I were the one signing off. This is the audit
trail, quoted verbatim. This record was capped at 0.55 confidence because the
currencies differ. Gemini then identified the counterpart at 0.95 and worked out
the exact FX rate — and the pipeline escalated it *anyway*, because a residual
amount variance is a human's call. The model's finding is attached to the
escalation. It isn't acted on.

That's the rule: the LLM explains, it doesn't move money."

## 3:05 – 3:47 · Metrics

> **On screen:** the `--report` output, or `outputs/reports/summary.md`.

"Scored against a ground-truth file written before any of this ran.

Bucket accuracy 100% — every record ended up where it should. Match precision
100%: of 384 pairings the system asserted, all 384 are correct. Recall 78.4%, and
I want to be direct about that: the missing 22% aren't errors, they're the records
it escalated instead of guessing. I could trade precision for recall by loosening
the thresholds. On something that moves money that's the wrong trade, so the
escalation rate is a feature — and the summary says so rather than burying it.

Throughput is about 95 records a second after the one-off model load."

## 3:47 – 4:33 · A failure, handled

> **On screen:** `python scripts/show_audit.py`, scenes 1 and 8.

"Two failure modes are deliberately injected, and both are in the trail rather
than on a slide.

First: a malformed row — amount 'N/A', a timestamp of month 13, day 45. Rejected
at ingest, both bad fields named, the raw row kept, run continues. It lands in the
`failed` bucket, so it still shows up in the totals instead of disappearing from
them.

Second: an injected API timeout on the reasoning call. Classified as transient,
retried with backoff, absorbed — here's the retry, logged against its record. A
malformed request would *not* be retried. And a third I didn't plan: if the policy
index can't be built offline, exceptions are still reported without citations
rather than the run dying. I found that by testing the clean-clone claim instead
of assuming it."

## 4:33 – 5:00 · Future scope and close

> **On screen:** the README's future-scope section.

"Honest boundaries. Eight transactions are real, pulled from the Razorpay test
API; server-to-server payment creation isn't enabled on a standard test account,
so the 300-record corpus is generated from those templates — documented, not
passed off as real. Scanned-document ingestion and learned confidence calibration
are designed for, not built.

What is built: an agent that resolves two-thirds of a reconciliation without a
model, uses a model only where a human would actually hesitate, cites statute for
the rest, and writes down why it did each one. Thanks for watching."

---

## Do not say

Guard rails for the take. Each of these is a claim the repo does not support:

- ~~"trained on real Razorpay data"~~ — 8 real records; the rest is generated
  from them and documented as such in `data/synthetic/mismatch_catalogue.md`.
- ~~"100% accurate"~~ — bucket accuracy and match *precision* are 100%; recall is
  78.4%. Say which.
- ~~"fully autonomous"~~ — 16.5% is escalated to a human by design. That is the
  point, not a shortfall.
- ~~"no network at all"~~ — first run downloads a ~90 MB embedding model unless
  it's already cached. It degrades gracefully if it can't, but it does try.
- ~~"every ambiguous case gets a Gemini judgment"~~ — 20 of 112 are cached; the
  rest fall back to a safe escalation.
- ~~"a settlement batches dozens of payments"~~ — the largest real batch is 7
  (19 singles, 29 pairs, 21 triples, then 5, 5 and 1 larger).
- ~~"patented" / "patentable"~~ — out of scope, forward-looking mention only.
