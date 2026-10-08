"""HTTP gateway: MediBot's API surface, with both guardrails in front of it.

    uv run uvicorn evalguard.gateway.app:app --port 8100

The caller's role comes from MediBot's own signed token — ``/login`` and the
``current_user`` dependency are the target's, imported unchanged — so a client
cannot claim a role in the request body. Responses never carry the guardrail's
reason; that goes to ``logs/events.jsonl`` and the LangSmith trace, keyed by the
``request_id`` returned here.
"""

from __future__ import annotations

from fastapi import Depends, FastAPI, HTTPException, status
from pydantic import BaseModel

from evalguard.gateway.pipeline import guarded_chat
from evalguard.target import _bootstrap

_bootstrap()
from app.auth import Principal, authenticate, current_user, issue_token  # noqa: E402

app = FastAPI(title="MediBot EvalGuard gateway")


class LoginRequest(BaseModel):
    username: str
    password: str


class ChatRequest(BaseModel):
    question: str


class ChatResponse(BaseModel):
    request_id: str
    answer: str
    blocked: bool
    retrieval_type: str
    sources: list[dict]
    latency_seconds: float


@app.get("/health")
def health() -> dict:
    return {"status": "ok"}


@app.post("/login")
def login(payload: LoginRequest) -> dict:
    user = authenticate(payload.username, payload.password)
    if user is None:
        raise HTTPException(status.HTTP_401_UNAUTHORIZED, "Invalid username or password.")
    token, expires_in = issue_token(user)
    return {"access_token": token, "token_type": "bearer", "expires_in": expires_in, "role": user.role}


@app.post("/chat", response_model=ChatResponse)
def chat(payload: ChatRequest, principal: Principal = Depends(current_user)) -> ChatResponse:
    resp = guarded_chat(payload.question, principal.role)
    return ChatResponse(
        request_id=resp.request_id,
        answer=resp.answer,
        blocked=resp.blocked,
        # A blocked request reports only that it was blocked, not which layer.
        retrieval_type="blocked" if resp.blocked else resp.retrieval_type,
        sources=resp.sources,
        latency_seconds=round(resp.latency_seconds, 3),
    )
