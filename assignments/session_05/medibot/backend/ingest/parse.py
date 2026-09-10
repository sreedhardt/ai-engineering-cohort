"""Structure-aware document parsing with Docling.

Docling produces a ``DoclingDocument`` that preserves headings, tables and code
blocks as typed items, rather than flattening everything to plain text. That
structure is what the hierarchical chunker splits along.
"""

from __future__ import annotations

import functools
from pathlib import Path

from docling.document_converter import DocumentConverter
from docling_core.types.doc.document import DoclingDocument

SUPPORTED_SUFFIXES = {".pdf", ".md"}


@functools.lru_cache(maxsize=1)
def _converter() -> DocumentConverter:
    # First call downloads the layout/OCR models; subsequent runs are cached.
    return DocumentConverter()


def parse_document(path: Path) -> DoclingDocument:
    return _converter().convert(str(path)).document


def discover_documents(data_dir: Path, collections: list[str]) -> list[tuple[str, Path]]:
    """Yield (collection, path) for every supported file, sorted for determinism."""
    found: list[tuple[str, Path]] = []
    for collection in collections:
        folder = data_dir / collection
        if not folder.is_dir():
            continue
        for path in sorted(folder.iterdir()):
            if path.suffix.lower() in SUPPORTED_SUFFIXES:
                found.append((collection, path))
    return found
