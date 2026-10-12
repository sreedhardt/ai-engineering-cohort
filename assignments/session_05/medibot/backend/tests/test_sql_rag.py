"""SQL RAG: the cleaning step, safety, and live analytical questions."""

import pytest

from app import sql_rag
from app.sql_rag import UnsafeSQLError, _execute, clean_sql, sql_rag_chain_verbose


class TestCleanSQL:
    def test_strips_markdown_fence(self):
        raw = "```sql\nSELECT COUNT(*) FROM claims;\n```"
        assert clean_sql(raw) == "SELECT COUNT(*) FROM claims"

    def test_strips_bare_fence(self):
        assert clean_sql("```\nSELECT 1\n```") == "SELECT 1"

    def test_strips_prose_preamble(self):
        raw = "Sure! Here is the query you asked for:\n\nSELECT * FROM claims LIMIT 5;"
        assert clean_sql(raw) == "SELECT * FROM claims LIMIT 5"

    def test_keeps_cte(self):
        raw = "WITH x AS (SELECT 1 AS n) SELECT n FROM x;"
        assert clean_sql(raw).startswith("WITH")

    def test_drops_trailing_second_statement(self):
        raw = "SELECT 1; DROP TABLE claims;"
        assert clean_sql(raw) == "SELECT 1"

    @pytest.mark.parametrize(
        "raw",
        [
            "DELETE FROM claims",
            "DROP TABLE claims",
            "I'm sorry, I cannot answer that.",
            "",
        ],
    )
    def test_rejects_unsafe_or_missing(self, raw):
        with pytest.raises(UnsafeSQLError):
            clean_sql(raw)


@pytest.mark.parametrize("raw", ["", "DELETE FROM claims"])
def test_chain_degrades_gracefully_without_usable_sql(monkeypatch, raw):
    """Empty model output (token budget spent on reasoning) or an unsafe
    statement must produce a message, never an exception, and never run."""
    calls = []
    monkeypatch.setattr(sql_rag, "complete", lambda *a, **k: calls.append(1) or raw)
    monkeypatch.setattr(sql_rag, "_execute", lambda sql: pytest.fail("must not execute"))
    answer = sql_rag.sql_rag_chain("How many claims are escalated?")
    assert "couldn't build a safe database query" in answer
    assert calls == [1], "no answer-generation call after a rejected query"


class TestDatabase:
    def test_database_is_readable(self):
        cols, rows = _execute("SELECT COUNT(*) AS n FROM claims")
        assert cols == ["n"] and rows[0][0] == 85

    def test_connection_is_read_only(self):
        import sqlite3

        with pytest.raises(sqlite3.OperationalError):
            _execute("DELETE FROM claims")


# The four analytical questions required by the brief.
ANALYTICAL_QUESTIONS = [
    "How many billing claims are currently escalated?",
    "Which equipment category has the most open maintenance tickets?",
    "What is the total claimed amount for cardiology?",
    "How many maintenance tickets were raised per campus?",
]


@pytest.mark.parametrize("question", ANALYTICAL_QUESTIONS)
def test_analytical_questions_end_to_end(question):
    """Each question must produce runnable SQL and a grounded answer."""
    result = sql_rag_chain_verbose(question)
    assert result["sql"].upper().startswith(("SELECT", "WITH"))
    assert result["row_count"] >= 1
    assert result["answer"].strip()
