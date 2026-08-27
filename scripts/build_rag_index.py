"""Day 4 (task 1.6) — build the GST/tax policy vector store.

Chunks every ``.txt`` in ``data/policy/``, embeds each chunk with the local
``sentence-transformers`` model, and persists a ChromaDB collection under
``data/rag_index/`` (committed, so a clean clone queries without rebuilding).

The embedding model (~90 MB) is downloaded once on first run and cached in the
Hugging Face cache; after that the store is fully offline.

Usage
-----
    python scripts/build_rag_index.py
    python scripts/build_rag_index.py --check   # rebuild, then run the retrieval sanity checks
"""

from __future__ import annotations

import argparse
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path[:0] = [str(ROOT), str(ROOT / "src")]

for _stream in (sys.stdout, sys.stderr):
    try:
        _stream.reconfigure(encoding="utf-8", errors="replace")
    except (AttributeError, ValueError):
        pass

from recon.rag.ground import _PLAYBOOK
from recon.rag.index import PolicyIndex, load_chunks

# The gate that matters: each pipeline query must retrieve the right *family* of
# clauses in its top-k. Keyed by exception kind, value = acceptable doc slugs.
_CHECKS = {
    "refund": {"cgst-act-s34", "cgst-rules-r53"},
    "charge": {"cgst-act-s16", "cgst-act-s17", "cgst-rules-r38", "circular-160-2021-itc"},
}


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--check", action="store_true", help="run retrieval sanity checks after build")
    args = parser.parse_args()

    chunks = load_chunks()
    print(f"{len({c.doc_slug for c in chunks})} documents -> {len(chunks)} chunks")

    index = PolicyIndex()
    n = index.build()
    print(f"embedded and persisted {n} chunks to {index.index_dir.relative_to(ROOT)}")

    if not args.check:
        return

    print("\nretrieval checks (pipeline queries):")
    failures = 0
    for kind, acceptable in _CHECKS.items():
        query = _PLAYBOOK[kind][0]
        slugs = [h.chunk.doc_slug for h in index.query(query, k=3)]
        ok = bool(acceptable & set(slugs))
        failures += not ok
        print(f"  [{'ok ' if ok else 'MISS'}] {kind:8s} -> {slugs}")
    if failures:
        sys.exit(f"\n{failures} retrieval check(s) failed")
    print("\nall retrieval checks passed")


if __name__ == "__main__":
    main()
