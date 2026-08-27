"""The GST/tax policy vector store — chunking, embedding, and retrieval.

Corpus: the ``.txt`` files in ``data/policy/`` (real CGST Act sections and CBIC
circulars — see ``data/policy/SOURCES.md``). Each file has a small header
(``# title`` / ``Source:`` / ``Retrieved:``) then the statutory body.

Chunking splits the body on blank lines and on sub-section markers ``(1)``,
``(2)``, … then merges fragments up to ``~MAX_CHARS`` with a one-line overlap, so
a retrieved chunk is a coherent clause a reader can check.

Embeddings: a local ``sentence-transformers`` model (``all-MiniLM-L6-v2``, 384-d)
— no API key. The model is downloaded once (~90 MB, cached in the HF cache);
after that the store is fully offline. Vectors live in a persistent ChromaDB
collection under ``data/rag_index/`` (committed), so a clean clone can query
without rebuilding.
"""

from __future__ import annotations

import re
from dataclasses import dataclass
from pathlib import Path

from config import settings

EMBED_MODEL = "all-MiniLM-L6-v2"
COLLECTION = "gst_policy"
MAX_CHARS = 700
MIN_CHARS = 250
INDEX_DIR = settings.DATA_DIR / "rag_index"

_SUBSECTION = re.compile(r"(?=\n\(\d+[A-Za-z]?\)\s)")
_HEADER_END = re.compile(r"\n-{3,}\n")


@dataclass(frozen=True)
class Chunk:
    chunk_id: str
    doc_slug: str
    doc_title: str
    source: str
    text: str


@dataclass(frozen=True)
class Retrieved:
    chunk: Chunk
    score: float  # cosine similarity, 0..1 (higher = closer)

    def citation(self) -> str:
        return f"{self.doc_title}"

    @property
    def doc_title(self) -> str:
        return self.chunk.doc_title

    def quote(self, max_chars: int = 320) -> str:
        text = re.sub(r"\s+", " ", self.chunk.text).strip()
        return text if len(text) <= max_chars else text[: max_chars - 1].rstrip() + "…"


# --- corpus + chunking -----------------------------------------------


def _parse_doc(path: Path) -> tuple[str, str, str]:
    """Return (title, source, body) from a policy .txt file."""
    raw = path.read_text(encoding="utf-8")
    title = path.stem
    source = "unknown"
    m = re.match(r"#\s*(.+)", raw)
    if m:
        title = m.group(1).strip()
    m = re.search(r"\nSource:\s*(.+)", raw)
    if m:
        source = m.group(1).strip()
    split = _HEADER_END.split(raw, maxsplit=1)
    body = split[1] if len(split) == 2 else raw
    return title, source, body.strip()


def _hard_wrap(text: str, limit: int) -> list[str]:
    """Split an over-long clause on sentence boundaries near ``limit``."""
    if len(text) <= limit:
        return [text]
    sentences = re.split(r"(?<=[.;:])\s+", text)
    out, buf = [], ""
    for s in sentences:
        if buf and len(buf) + len(s) > limit:
            out.append(buf)
            buf = s
        else:
            buf = f"{buf} {s}".strip()
    if buf:
        out.append(buf)
    return out


def _split_body(body: str) -> list[str]:
    pieces: list[str] = []
    for para in re.split(r"\n\s*\n", body):
        for p in _SUBSECTION.split(para):
            if p.strip():
                pieces.extend(_hard_wrap(p.strip(), MAX_CHARS))

    chunks: list[str] = []
    buf = ""
    for piece in pieces:
        piece = piece.strip()
        if not piece:
            continue
        if buf and len(buf) + len(piece) > MAX_CHARS:
            chunks.append(buf)
            tail = buf.rsplit(". ", 1)[-1]
            buf = (tail + " " if len(tail) < 120 else "") + piece
        else:
            buf = f"{buf}\n{piece}".strip()
    if buf:
        chunks.append(buf)

    # fold a stray short trailing chunk back into the previous one
    if len(chunks) >= 2 and len(chunks[-1]) < MIN_CHARS:
        chunks[-2] = f"{chunks[-2]}\n{chunks.pop()}"
    return chunks


def load_chunks(policy_dir: Path | None = None) -> list[Chunk]:
    directory = policy_dir or settings.POLICY_DIR
    files = sorted(directory.glob("*.txt"))
    if not files:
        raise FileNotFoundError(
            f"no policy documents in {directory} — see data/policy/SOURCES.md"
        )
    out: list[Chunk] = []
    for path in files:
        title, source, body = _parse_doc(path)
        for i, text in enumerate(_split_body(body)):
            out.append(
                Chunk(
                    chunk_id=f"{path.stem}#{i:02d}",
                    doc_slug=path.stem,
                    doc_title=title,
                    source=source,
                    text=text,
                )
            )
    return out


# --- the store -----------------------------------------------------


class PolicyIndex:
    def __init__(self, index_dir: Path | None = None, model_name: str = EMBED_MODEL) -> None:
        self.index_dir = Path(index_dir) if index_dir is not None else INDEX_DIR
        self.model_name = model_name
        self._client = None
        self._collection = None
        self._embedder = None

    # -- lazy deps ----------------------------------------------

    def _embed(self, texts: list[str]) -> list[list[float]]:
        if self._embedder is None:
            from sentence_transformers import SentenceTransformer

            self._embedder = SentenceTransformer(self.model_name)
        vecs = self._embedder.encode(texts, normalize_embeddings=True)
        return [v.tolist() for v in vecs]

    def _chroma(self):
        if self._client is None:
            import chromadb

            self.index_dir.mkdir(parents=True, exist_ok=True)
            self._client = chromadb.PersistentClient(path=str(self.index_dir))
        return self._client

    # -- build / load ---------------------------------------

    def build(self, policy_dir: Path | None = None) -> int:
        """(Re)build the collection from the policy corpus. Returns chunk count."""
        client = self._chroma()
        try:
            client.delete_collection(COLLECTION)
        except Exception:  # noqa: BLE001, S110 - a rebuild when nothing exists yet is fine
            pass
        collection = client.create_collection(COLLECTION, metadata={"hnsw:space": "cosine"})
        chunks = load_chunks(policy_dir)
        collection.add(
            ids=[c.chunk_id for c in chunks],
            documents=[c.text for c in chunks],
            embeddings=self._embed([c.text for c in chunks]),
            metadatas=[
                {"doc_slug": c.doc_slug, "doc_title": c.doc_title, "source": c.source}
                for c in chunks
            ],
        )
        self._collection = collection
        return len(chunks)

    def _load(self):
        if self._collection is None:
            client = self._chroma()
            try:
                self._collection = client.get_collection(COLLECTION)
            except Exception:  # noqa: BLE001 - collection absent -> build it now
                self._collection = None
                self.build()
        return self._collection

    def is_built(self) -> bool:
        try:
            return self._chroma().get_collection(COLLECTION).count() > 0
        except Exception:  # noqa: BLE001
            return False

    # -- query --------------------------------------------

    def query(self, text: str, k: int = 4) -> list[Retrieved]:
        collection = self._load()
        res = collection.query(query_embeddings=self._embed([text]), n_results=k)
        out: list[Retrieved] = []
        for cid, doc, meta, dist in zip(
            res["ids"][0], res["documents"][0], res["metadatas"][0], res["distances"][0],
            strict=True,
        ):
            out.append(
                Retrieved(
                    chunk=Chunk(
                        chunk_id=cid,
                        doc_slug=meta["doc_slug"],
                        doc_title=meta["doc_title"],
                        source=meta["source"],
                        text=doc,
                    ),
                    score=max(0.0, 1.0 - float(dist)),  # cosine distance -> similarity
                )
            )
        return out
