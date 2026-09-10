"""MediBot FastAPI application."""

from __future__ import annotations

import logging

from fastapi import Depends, FastAPI, HTTPException, status
from fastapi.middleware.cors import CORSMiddleware

from app.auth import Principal, authenticate, current_user, issue_token
from app.config import settings
from app.rag import generate_answer
from app.rbac import ALL_ROLES, can_use_sql_rag, collections_for
from app.retrieval import retrieve
from app.router import blocked_collection, is_analytical, refusal_message
from app.schemas import (
    ChatRequest,
    ChatResponse,
    CollectionsResponse,
    HealthResponse,
    LoginRequest,
    LoginResponse,
    Source,
)
from app.sql_rag import sql_rag_chain
from app.vectorstore import get_client

logger = logging.getLogger("medibot")

app = FastAPI(
    title="MediBot",
    description="Advanced RAG assistant for MediAssist Health Network",
    version="0.1.0",
)

app.add_middleware(
    CORSMiddleware,
    allow_origins=["http://localhost:3000"],
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)


@app.get("/health", response_model=HealthResponse)
def health() -> HealthResponse:
    try:
        info = get_client().get_collection(settings.qdrant_collection)
        return HealthResponse(
            status="ok", qdrant="connected", indexed_chunks=info.points_count
        )
    except Exception as exc:  # noqa: BLE001 - health must never raise
        logger.warning("qdrant unreachable: %s", exc)
        return HealthResponse(status="degraded", qdrant="unavailable")


@app.post("/login", response_model=LoginResponse)
def login(payload: LoginRequest) -> LoginResponse:
    user = authenticate(payload.username, payload.password)
    if user is None:
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail="Invalid username or password.",
        )
    token, expires_in = issue_token(user)
    return LoginResponse(
        access_token=token,
        expires_in=expires_in,
        role=user.role,
        display_name=user.display_name,
        department=user.department,
        collections=collections_for(user.role),
    )


@app.get("/collections/{role}", response_model=CollectionsResponse)
def collections(role: str, principal: Principal = Depends(current_user)) -> CollectionsResponse:
    if role not in ALL_ROLES:
        raise HTTPException(status_code=404, detail=f"Unknown role: {role}")
    # A user may inspect their own permissions; only admin may inspect others'.
    if role != principal.role and principal.role != "admin":
        raise HTTPException(
            status_code=status.HTTP_403_FORBIDDEN,
            detail="You may only view the collections for your own role.",
        )
    return CollectionsResponse(
        role=role,
        collections=collections_for(role),
        sql_rag_enabled=can_use_sql_rag(role),
    )


@app.post("/chat", response_model=ChatResponse)
def chat(payload: ChatRequest, principal: Principal = Depends(current_user)) -> ChatResponse:
    """Route a question, enforce RBAC, and answer with citations.

    The role comes from the verified token — never from the request body.
    """
    role = principal.role
    question = payload.question.strip()

    # --- analytical branch: SQL RAG -------------------------------------
    if is_analytical(question):
        if not can_use_sql_rag(role):
            logger.info("sql_rag denied role=%s", role)
            return ChatResponse(
                answer=(
                    f"Operational statistics are limited to billing and admin staff, "
                    f"so I can't run that query for a {role.replace('_', ' ')}. "
                    "I can still answer questions from your document collections."
                ),
                sources=[],
                retrieval_type="rbac_denied",
                role=role,
                access_denied=True,
            )
        return ChatResponse(
            answer=sql_rag_chain(question),
            sources=[],
            retrieval_type="sql_rag",
            role=role,
        )

    # --- advisory pre-check: a helpful refusal, not the security boundary --
    blocked = blocked_collection(question, role)
    if blocked is not None:
        logger.info("rbac pre-check blocked role=%s collection=%s", role, blocked)
        return ChatResponse(
            answer=refusal_message(role, blocked),
            sources=[],
            retrieval_type="rbac_denied",
            role=role,
            access_denied=True,
        )

    # --- document branch: hybrid retrieval + rerank ----------------------
    top_chunks, pool = retrieve(question, role)
    logger.info(
        "retrieval role=%s candidates=%d reranked=%d", role, len(pool), len(top_chunks)
    )
    return ChatResponse(
        answer=generate_answer(question, role, top_chunks),
        sources=[Source(**c.as_source()) for c in top_chunks],
        retrieval_type="hybrid_rag",
        role=role,
    )
