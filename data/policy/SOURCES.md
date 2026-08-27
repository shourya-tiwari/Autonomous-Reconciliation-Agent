# RAG corpus — sources

Nine real GST documents from **cbic-gst.gov.in** (Central Board of Indirect Taxes
and Customs, Government of India). Each `.txt` file carries its own `Source:` and
`Retrieved:` header. Text extracted from the official consolidated PDFs with
`pypdf`; amendment footnotes and running page headers stripped, statutory wording
otherwise verbatim (minor PDF-text-layer artifacts remain, e.g. "sup plies").

| File | Instrument | Governs |
|------|-----------|---------|
| `cgst-act-s16.txt` | CGST Act 2017, s.16 | Eligibility & conditions for input tax credit |
| `cgst-act-s17.txt` | CGST Act 2017, s.17 | Apportionment of credit; blocked credits; the banking 50% option |
| `cgst-act-s31.txt` | CGST Act 2017, s.31 | Tax invoice — when and how issued |
| `cgst-act-s34.txt` | CGST Act 2017, s.34 | **Credit & debit notes** — returns, deficient supply, value reduction |
| `cgst-act-s54.txt` | CGST Act 2017, s.54 | Refund of tax |
| `cgst-rules-r38.txt` | CGST Rules 2017, r.38 | Credit claim by a banking company / financial institution |
| `cgst-rules-r46.txt` | CGST Rules 2017, r.46 | Particulars a tax invoice must contain |
| `cgst-rules-r53.txt` | CGST Rules 2017, r.53 | Particulars of a revised invoice / credit / debit note |
| `circular-160-2021-itc.txt` | Circular 160/16/2021-GST | ITC on debit notes; time limits |

## How the pipeline uses it

`scripts/build_rag_index.py` chunks these files, embeds each chunk with a local
`sentence-transformers` model (`all-MiniLM-L6-v2`), and persists a ChromaDB
collection to `data/rag_index/`. The RAG layer (`recon.rag`) queries it for every
`unmatched-exception` record and quotes the retrieved clause in the audit trail.

Two exception kinds, two governing clauses:
- **refund debit** (money returned to a customer, no sales invoice) → s.34 credit note
- **bank charge** (fee in no ledger) → s.16 / s.17 / r.38 input tax credit

The embedding model (~90 MB) downloads once on first run, then everything is offline.
