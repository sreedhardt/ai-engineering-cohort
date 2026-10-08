"""Bridge to the target system (MediBot, session_05).

The target is imported **in-process** rather than called over HTTP. Two reasons:

  * tracing — LangSmith spans nest naturally through retrieval, reranking and
    generation, so a reviewer can open one trace and see the whole path. A proxy
    sitting in front of an opaque HTTP service could only time the black box.
  * evaluation — RAGAS needs the retrieved chunk *text* to compute context
    precision and recall. The target's ``/chat`` response only carries citation
    metadata (document, section, collection), which is right for a UI and
    insufficient for evaluation.

Nothing in the target is modified. Its backend directory is put on sys.path and
its public functions are called as a library, so session_05 stays exactly as it
was submitted.
"""

from __future__ import annotations

import functools
import sys
from dataclasses import dataclass, field

from evalguard.config import settings


@functools.lru_cache(maxsize=1)
def _bootstrap() -> None:
    """Put the target's backend on sys.path, once."""
    backend = settings.target_root / "backend"
    if not backend.is_dir():
        raise RuntimeError(
            f"Target system not found at {backend}. "
            "Set MEDIBOT_ROOT in .env to the MediBot project directory."
        )
    if str(backend) not in sys.path:
        sys.path.insert(0, str(backend))


def target_modules():
    """Import and return the target's modules, bootstrapping sys.path first."""
    _bootstrap()
    from app import rag, rbac, retrieval, router, sql_rag  # noqa: PLC0415

    return {
        "rag": rag,
        "rbac": rbac,
        "retrieval": retrieval,
        "router": router,
        "sql_rag": sql_rag,
    }


@dataclass
class TargetAnswer:
    """One answer from the target system, with everything evaluation needs."""

    question: str
    role: str
    answer: str
    retrieval_type: str  # "hybrid_rag" | "sql_rag" | "rbac_denied"
    access_denied: bool = False
    # Citation metadata, as the target's own API returns it.
    sources: list[dict] = field(default_factory=list)
    # The chunk text behind those citations — needed by RAGAS, not exposed by
    # the target's HTTP API.
    contexts: list[str] = field(default_factory=list)
    latency_seconds: float = 0.0
    error: str | None = None


def generator_model() -> str:
    """The model the target actually answers with, read from its own config."""
    _bootstrap()
    from app.config import settings as target_settings  # noqa: PLC0415

    return target_settings.groq_model


def available_roles() -> list[str]:
    return list(target_modules()["rbac"].ALL_ROLES)


def collections_for(role: str) -> list[str]:
    return target_modules()["rbac"].collections_for(role)


def can_use_sql_rag(role: str) -> bool:
    return target_modules()["rbac"].can_use_sql_rag(role)


def _traced_target():
    """The target's building blocks, each wrapped as a named LangSmith span."""
    from evalguard.observability.tracing import wrap  # noqa: PLC0415

    mods = target_modules()

    # Rerank is called from inside the target's own retrieve(), so wrapping the
    # module attribute (rather than just the function we hold) is what makes the
    # nested call show up as its own span. This rebinds an attribute on the
    # imported module object in memory; the target's source is untouched.
    retrieval = mods["retrieval"]
    if not getattr(retrieval.rerank, "_evalguard_traced", False):
        traced_rerank = wrap(retrieval.rerank, "target.rerank", "chain")
        traced_rerank._evalguard_traced = True
        retrieval.rerank = traced_rerank

    return {
        "is_analytical": wrap(mods["router"].is_analytical, "target.route", "chain"),
        "blocked_collection": mods["router"].blocked_collection,
        "refusal_message": mods["router"].refusal_message,
        "retrieve": wrap(mods["retrieval"].retrieve, "target.retrieve", "retriever"),
        "generate_answer": wrap(mods["rag"].generate_answer, "target.generate", "llm"),
        "sql_rag_chain": wrap(mods["sql_rag"].sql_rag_chain, "target.sql_rag", "chain"),
        "can_use_sql_rag": mods["rbac"].can_use_sql_rag,
    }


def answer_question(question: str, role: str) -> TargetAnswer:
    """Invoke the target system for one question under one role.

    This mirrors the orchestration in the target's own ``/chat`` endpoint, which
    lives inline in a FastAPI handler bound to a request dependency and so
    cannot be imported directly. The building blocks called here are the
    target's own — routing, retrieval, reranking, generation and SQL RAG are
    not reimplemented, only re-composed so each becomes a traceable span and so
    the retrieved chunk text is available to the evaluation pipeline.
    """
    import time  # noqa: PLC0415

    from evalguard.observability.metrics import instrument_target_llm  # noqa: PLC0415

    instrument_target_llm()
    t = _traced_target()
    started = time.perf_counter()
    question = question.strip()  # as the target's /chat does

    try:
        if t["is_analytical"](question):
            if not t["can_use_sql_rag"](role):
                return TargetAnswer(
                    question=question,
                    role=role,
                    # Verbatim from the target's /chat handler.
                    answer=(
                        f"Operational statistics are limited to billing and admin staff, "
                        f"so I can't run that query for a {role.replace('_', ' ')}. "
                        "I can still answer questions from your document collections."
                    ),
                    retrieval_type="rbac_denied",
                    access_denied=True,
                    latency_seconds=time.perf_counter() - started,
                )
            return TargetAnswer(
                question=question,
                role=role,
                answer=t["sql_rag_chain"](question),
                retrieval_type="sql_rag",
                latency_seconds=time.perf_counter() - started,
            )

        blocked = t["blocked_collection"](question, role)
        if blocked is not None:
            return TargetAnswer(
                question=question,
                role=role,
                answer=t["refusal_message"](role, blocked),
                retrieval_type="rbac_denied",
                access_denied=True,
                latency_seconds=time.perf_counter() - started,
            )

        top_chunks, _pool = t["retrieve"](question, role)
        answer = t["generate_answer"](question, role, top_chunks)
        return TargetAnswer(
            question=question,
            role=role,
            answer=answer,
            retrieval_type="hybrid_rag",
            sources=[c.as_source() for c in top_chunks],
            contexts=[c.text for c in top_chunks],
            latency_seconds=time.perf_counter() - started,
        )
    except Exception as exc:  # noqa: BLE001 - surfaced to the caller as an error field
        return TargetAnswer(
            question=question,
            role=role,
            answer="",
            retrieval_type="error",
            latency_seconds=time.perf_counter() - started,
            error=f"{type(exc).__name__}: {exc}",
        )
