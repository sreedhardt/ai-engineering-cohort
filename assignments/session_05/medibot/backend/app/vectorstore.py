"""Qdrant schema and the single hybrid+RBAC query.

All five document collections live in **one** Qdrant collection, distinguished by
a ``collection`` payload field. That is deliberate: it lets one server-side
request carry the dense vector, the BM25 sparse vector, the RRF fusion step and
the RBAC filter together, rather than issuing separate queries and merging them
in application code.
"""

from __future__ import annotations

import functools

from qdrant_client import QdrantClient, models

from app.config import settings

DENSE_VECTOR = "dense"
SPARSE_VECTOR = "bm25"


@functools.lru_cache(maxsize=1)
def get_client() -> QdrantClient:
    return QdrantClient(url=settings.qdrant_url, timeout=120)


@functools.lru_cache(maxsize=1)
def _dense_model():
    from fastembed import TextEmbedding

    return TextEmbedding(settings.dense_model)


@functools.lru_cache(maxsize=1)
def _sparse_model():
    from fastembed import SparseTextEmbedding

    return SparseTextEmbedding(settings.sparse_model)


@functools.lru_cache(maxsize=1)
def dense_dim() -> int:
    for desc in _dense_model().list_supported_models():
        if desc["model"] == settings.dense_model:
            return desc["dim"]
    raise RuntimeError(f"unknown dense model: {settings.dense_model}")


def embed_documents(texts: list[str]):
    """Dense + sparse vectors for chunks being indexed."""
    dense = list(_dense_model().passage_embed(texts))
    sparse = list(_sparse_model().embed(texts))
    return dense, sparse


def embed_query(text: str):
    """Dense + sparse vectors for a search query."""
    dense = next(iter(_dense_model().query_embed(text)))
    sparse = next(iter(_sparse_model().query_embed(text)))
    return dense, sparse


def recreate_collection() -> None:
    """(Re)build the collection with both vector types and a payload index."""
    client = get_client()
    client.delete_collection(settings.qdrant_collection)
    client.create_collection(
        collection_name=settings.qdrant_collection,
        vectors_config={
            DENSE_VECTOR: models.VectorParams(
                size=dense_dim(), distance=models.Distance.COSINE
            )
        },
        sparse_vectors_config={
            # IDF is computed server-side by Qdrant; BM25 needs this modifier.
            SPARSE_VECTOR: models.SparseVectorParams(modifier=models.Modifier.IDF)
        },
    )
    # Indexed so the RBAC filter stays cheap as the corpus grows.
    client.create_payload_index(
        collection_name=settings.qdrant_collection,
        field_name="access_roles",
        field_schema=models.PayloadSchemaType.KEYWORD,
    )
    client.create_payload_index(
        collection_name=settings.qdrant_collection,
        field_name="collection",
        field_schema=models.PayloadSchemaType.KEYWORD,
    )


def upsert_chunks(points: list[models.PointStruct]) -> None:
    get_client().upsert(
        collection_name=settings.qdrant_collection, points=points, wait=True
    )


def hybrid_search(
    query: str,
    rbac_filter: models.Filter,
    limit: int,
) -> list[models.ScoredPoint]:
    """Dense + BM25 in one request, fused with RRF, RBAC-filtered server-side.

    The filter is passed to *both* prefetch branches, so restricted chunks are
    never candidates in either arm of the search — not removed afterwards.
    """
    dense_vec, sparse_vec = embed_query(query)
    prefetch_limit = limit * 2

    response = get_client().query_points(
        collection_name=settings.qdrant_collection,
        prefetch=[
            models.Prefetch(
                query=dense_vec.tolist(),
                using=DENSE_VECTOR,
                filter=rbac_filter,
                limit=prefetch_limit,
            ),
            models.Prefetch(
                query=models.SparseVector(**sparse_vec.as_object()),
                using=SPARSE_VECTOR,
                filter=rbac_filter,
                limit=prefetch_limit,
            ),
        ],
        query=models.FusionQuery(fusion=models.Fusion.RRF),
        query_filter=rbac_filter,
        limit=limit,
        with_payload=True,
    )
    return response.points


def dense_only_search(
    query: str, rbac_filter: models.Filter, limit: int
) -> list[models.ScoredPoint]:
    """Dense-only retrieval, used by the comparison script in docs/."""
    dense_vec, _ = embed_query(query)
    response = get_client().query_points(
        collection_name=settings.qdrant_collection,
        query=dense_vec.tolist(),
        using=DENSE_VECTOR,
        query_filter=rbac_filter,
        limit=limit,
        with_payload=True,
    )
    return response.points
