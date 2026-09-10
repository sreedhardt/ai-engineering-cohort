"""Hierarchical chunking with metadata, built on Docling's HybridChunker.

HybridChunker splits along the document's own structure first (section →
subsection → paragraph/table), then applies a token-aware second pass so no
chunk overflows the embedding model's window.

The important subtlety is that two different texts come out of each chunk:

  * ``contextualize(chunk)`` prepends the heading path — this is what gets
    embedded, so "25mg twice daily" carries its parent section into the vector
  * ``chunk.text`` is the raw body — this is what a citation displays
"""

from __future__ import annotations

import functools
from pathlib import Path

from docling_core.transforms.chunker.hybrid_chunker import HybridChunker
from docling_core.types.doc.document import DoclingDocument
from docling_core.types.doc.labels import DocItemLabel

from app.rbac import roles_for_collection

# BAAI/bge-small-en-v1.5 has a 512-token window. The chunker budgets against the
# chunk *body*, while contextualize() later prepends the heading path, so we
# leave headroom for that prefix — otherwise the longest table chunks overflow
# and get silently truncated at embed time.
TOKENIZER = "BAAI/bge-small-en-v1.5"
EMBED_WINDOW = 512
HEADING_HEADROOM = 64
MAX_TOKENS = EMBED_WINDOW - HEADING_HEADROOM

_TABLE_LABELS = {DocItemLabel.TABLE}
_HEADING_LABELS = {DocItemLabel.SECTION_HEADER, DocItemLabel.TITLE}
_CODE_LABELS = {DocItemLabel.CODE, DocItemLabel.FORMULA}


@functools.lru_cache(maxsize=1)
def _chunker() -> HybridChunker:
    return HybridChunker(
        tokenizer=TOKENIZER,
        max_tokens=MAX_TOKENS,
        merge_peers=True,
    )


def _chunk_type(chunk) -> str:
    """Classify a chunk by the document items it was built from.

    HybridChunker merges peer items, so a single chunk routinely mixes labels —
    a prose section may carry one incidental code block. A label therefore has
    to *dominate* the chunk to name it, not merely appear in it. Tables are the
    exception: a dosage table with a caption is still a table, and preserving
    that is the whole point of structural parsing.
    """
    labels = [
        item.label for item in (chunk.meta.doc_items or []) if hasattr(item, "label")
    ]
    if not labels:
        return "text"
    if any(label in _TABLE_LABELS for label in labels):
        return "table"

    body = [label for label in labels if label not in _HEADING_LABELS]
    if not body:
        return "heading"
    if all(label in _CODE_LABELS for label in body):
        return "code"
    return "text"


def _section_title(chunk) -> str:
    """Deepest heading this chunk sits under, falling back to the doc title."""
    headings = chunk.meta.headings or []
    if headings:
        return headings[-1]
    return chunk.meta.origin.filename if chunk.meta.origin else "Untitled"


def chunk_document(
    doc: DoclingDocument, collection: str, source_path: Path
) -> list[dict]:
    """Chunk one document into payload dicts ready for the vector store."""
    chunker = _chunker()
    access_roles = roles_for_collection(collection)

    records: list[dict] = []
    for chunk in chunker.chunk(dl_doc=doc):
        body = (chunk.text or "").strip()
        if not body:
            continue
        records.append(
            {
                # Embedded text: carries the heading path as context.
                "embed_text": chunker.contextualize(chunk=chunk),
                # Displayed text: the raw passage behind a citation.
                "text": body,
                "source_document": source_path.name,
                "collection": collection,
                "access_roles": access_roles,
                "section_title": _section_title(chunk),
                "chunk_type": _chunk_type(chunk),
            }
        )
    return records
