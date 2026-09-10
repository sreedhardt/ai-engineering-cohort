"""Question routing: SQL RAG vs document RAG.

The existing SQL tests call sql_rag_chain directly, so nothing exercised the
routing decision itself. These do — including the LLM fallback path, which
failed silently once because a reasoning model spent its whole token budget
thinking and returned an empty verdict.
"""

import pytest

from app.router import _ROUTER_MAX_TOKENS, is_analytical

# Decided by the heuristic alone — no LLM call, so these are fast and free.
HEURISTIC_SQL = [
    "How many claims were escalated?",
    "Which equipment category has the most open maintenance tickets?",
    "What is the total number of pending claims?",
]

HEURISTIC_DOCUMENT = [
    "What is the standard adult dose of Amoxicillin?",
    "What is the hand hygiene procedure?",
    "Explain the sepsis treatment protocol.",
]

# No clear heuristic signal — these exercise the LLM fallback.
FALLBACK_SQL = [
    "Total claimed amount for cardiology",
    "Escalated claims in March",
    "average approved amount by insurer",
]

FALLBACK_DOCUMENT = [
    "Show me the drug formulary",
    "Bowie-Dick test frequency",
]


@pytest.mark.parametrize("question", HEURISTIC_SQL)
def test_heuristic_routes_to_sql(question):
    assert is_analytical(question) is True


@pytest.mark.parametrize("question", HEURISTIC_DOCUMENT)
def test_heuristic_routes_to_documents(question):
    assert is_analytical(question) is False


@pytest.mark.parametrize("question", FALLBACK_SQL)
def test_fallback_routes_to_sql(question):
    assert is_analytical(question) is True


@pytest.mark.parametrize("question", FALLBACK_DOCUMENT)
def test_fallback_routes_to_documents(question):
    assert is_analytical(question) is False


def test_router_budget_leaves_room_for_reasoning():
    """Guard the regression directly: a tiny cap silently disables the fallback.

    With max_tokens=5 the model returned finish_reason="length" and empty
    content, so every ambiguous question fell through to document RAG.
    """
    assert _ROUTER_MAX_TOKENS >= 64
