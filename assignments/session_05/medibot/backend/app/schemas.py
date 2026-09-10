"""Request and response models.

Note what ChatRequest does *not* contain: a role. The caller supplies only a
question; the role is derived from the verified token. Accepting a client-sent
role would let anyone curl their way to admin access.
"""

from __future__ import annotations

from pydantic import BaseModel, Field


class LoginRequest(BaseModel):
    username: str
    password: str


class LoginResponse(BaseModel):
    access_token: str
    token_type: str = "bearer"
    expires_in: int
    role: str
    display_name: str
    department: str
    collections: list[str]


class ChatRequest(BaseModel):
    question: str = Field(min_length=1, max_length=2000)

    model_config = {"extra": "forbid"}  # reject a smuggled "role" field outright


class Source(BaseModel):
    source_document: str
    section_title: str
    collection: str


class ChatResponse(BaseModel):
    answer: str
    sources: list[Source]
    retrieval_type: str  # "hybrid_rag" | "sql_rag" | "rbac_denied"
    role: str
    access_denied: bool = False


class CollectionsResponse(BaseModel):
    role: str
    collections: list[str]
    sql_rag_enabled: bool


class HealthResponse(BaseModel):
    status: str
    qdrant: str
    indexed_chunks: int | None = None
