"""Hybrid retrieval followed by cross-encoder reranking.

The two stages do different jobs. Hybrid search casts a wide net cheaply, scoring
each chunk independently of the others. The cross-encoder then reads the query
and each candidate *together*, which is far more accurate but too slow to run
over the whole corpus — so it only ever sees the shortlist.
"""

from __future__ import annotations

import functools
from dataclasses import dataclass

from app.config import settings
from app.rbac import qdrant_filter
from app.vectorstore import dense_only_search, hybrid_search


@dataclass
class RetrievedChunk:
    text: str
    context_text: str
    source_document: str
    section_title: str
    collection: str
    chunk_type: str
    fusion_score: float
    rerank_score: float | None = None

    def as_source(self) -> dict:
        return {
            "source_document": self.source_document,
            "section_title": self.section_title,
            "collection": self.collection,
        }


@functools.lru_cache(maxsize=1)
def _reranker():
    from fastembed.rerank.cross_encoder import TextCrossEncoder

    return TextCrossEncoder(settings.rerank_model)


def _to_chunks(points) -> list[RetrievedChunk]:
    chunks = []
    for point in points:
        payload = point.payload or {}
        chunks.append(
            RetrievedChunk(
                text=payload.get("text", ""),
                context_text=payload.get("context_text") or payload.get("text", ""),
                source_document=payload.get("source_document", "unknown"),
                section_title=payload.get("section_title", ""),
                collection=payload.get("collection", ""),
                chunk_type=payload.get("chunk_type", "text"),
                fusion_score=point.score,
            )
        )
    return chunks


def rerank(query: str, candidates: list[RetrievedChunk], top_k: int) -> list[RetrievedChunk]:
    """Score candidates jointly against the query and keep the strongest."""
    if not candidates:
        return []
    # Score the heading-qualified text, matching what was embedded.
    scores = list(_reranker().rerank(query, [c.context_text for c in candidates]))
    for chunk, score in zip(candidates, scores, strict=True):
        chunk.rerank_score = float(score)
    ranked = sorted(candidates, key=lambda c: c.rerank_score, reverse=True)
    return ranked[:top_k]


def retrieve(
    query: str,
    role: str,
    *,
    candidates: int | None = None,
    top_k: int | None = None,
) -> tuple[list[RetrievedChunk], list[RetrievedChunk]]:
    """RBAC-filtered hybrid retrieval, then reranking.

    Returns (reranked_top_k, full_candidate_set) — the second is for logging and
    the dense-vs-hybrid comparison, never for the LLM prompt.
    """
    candidates = candidates or settings.retrieval_candidates
    top_k = top_k or settings.rerank_top_k

    pool = _to_chunks(hybrid_search(query, qdrant_filter(role), candidates))
    return rerank(query, pool, top_k), pool


def retrieve_dense_only(query: str, role: str, limit: int) -> list[RetrievedChunk]:
    """Dense-only retrieval for the retrieval-quality comparison in docs/."""
    return _to_chunks(dense_only_search(query, qdrant_filter(role), limit))
