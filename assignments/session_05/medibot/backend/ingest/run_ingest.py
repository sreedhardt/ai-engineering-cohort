"""Ingestion entrypoint: parse -> chunk -> embed -> upsert.

Run once before the demo (first run downloads Docling and FastEmbed models):

    docker compose up -d
    uv run python -m ingest.run_ingest
"""

from __future__ import annotations

import argparse
import time
import uuid

from qdrant_client import models

from app.config import DATA_DIR
from app.rbac import COLLECTION_ROLES
from app.vectorstore import (
    DENSE_VECTOR,
    SPARSE_VECTOR,
    embed_documents,
    recreate_collection,
    upsert_chunks,
)
from ingest.chunking import chunk_document
from ingest.parse import discover_documents, parse_document

BATCH_SIZE = 32


def _to_points(records: list[dict]) -> list[models.PointStruct]:
    dense, sparse = embed_documents([r["embed_text"] for r in records])
    points = []
    for record, dense_vec, sparse_vec in zip(records, dense, sparse, strict=True):
        # The contextualized text is stored as well as embedded: the reranker
        # must score the same heading-qualified text the vectors were built
        # from, or it judges a contextless fragment ("- Default - 300 mmHg").
        payload = dict(record)
        payload["context_text"] = payload.pop("embed_text")
        points.append(
            models.PointStruct(
                id=str(uuid.uuid4()),
                vector={
                    DENSE_VECTOR: dense_vec.tolist(),
                    SPARSE_VECTOR: models.SparseVector(**sparse_vec.as_object()),
                },
                payload=payload,
            )
        )
    return points


def main() -> None:
    parser = argparse.ArgumentParser(description="Ingest MediAssist documents")
    parser.add_argument(
        "--collections",
        nargs="*",
        default=list(COLLECTION_ROLES),
        help="Subset of collections to ingest (default: all)",
    )
    args = parser.parse_args()

    documents = discover_documents(DATA_DIR, args.collections)
    if not documents:
        raise SystemExit(f"No documents found under {DATA_DIR}")

    print(f"Rebuilding collection with {len(documents)} documents...")
    recreate_collection()

    totals: dict[str, int] = {}
    started = time.perf_counter()

    for collection, path in documents:
        t0 = time.perf_counter()
        doc = parse_document(path)
        records = chunk_document(doc, collection, path)

        for i in range(0, len(records), BATCH_SIZE):
            upsert_chunks(_to_points(records[i : i + BATCH_SIZE]))

        totals[collection] = totals.get(collection, 0) + len(records)
        kinds: dict[str, int] = {}
        for r in records:
            kinds[r["chunk_type"]] = kinds.get(r["chunk_type"], 0) + 1
        summary = ", ".join(f"{v} {k}" for k, v in sorted(kinds.items()))
        print(
            f"  [{collection:9}] {path.name:32} {len(records):4} chunks "
            f"({summary}) in {time.perf_counter() - t0:5.1f}s"
        )

    elapsed = time.perf_counter() - started
    print(f"\nDone in {elapsed:.1f}s. Chunks per collection:")
    for collection, count in sorted(totals.items()):
        roles = ", ".join(COLLECTION_ROLES[collection])
        print(f"  {collection:10} {count:4}  access_roles=[{roles}]")


if __name__ == "__main__":
    main()
