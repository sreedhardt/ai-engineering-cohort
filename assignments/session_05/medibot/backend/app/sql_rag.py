"""SQL RAG over mediassist.db, as a plain Python function.

Three explicit steps, per the spec:
  1. natural language -> SQL, via the LLM
  2. clean the raw LLM output down to a single executable statement
  3. execute, then hand the rows back to the LLM for a natural-language answer
"""

from __future__ import annotations

import re
import sqlite3

from app.config import DB_PATH
from app.llm import complete

MAX_ROWS = 50

SCHEMA = """\
TABLE claims (
    claim_id TEXT PRIMARY KEY, patient_id TEXT, patient_name TEXT,
    department TEXT,          -- e.g. cardiology, nephrology, neurology, gynaecology
    claim_type TEXT,          -- 'cashless' | 'reimbursement'
    diagnosis_code TEXT,      -- ICD-10, e.g. 'I21.4'
    insurer TEXT,             -- e.g. 'HDFC Ergo', 'Bajaj Allianz'
    claimed_amount REAL, approved_amount REAL,  -- INR; approved_amount NULL while pending
    status TEXT,              -- 'pending' | 'approved' | 'rejected' | 'escalated'
    submitted_date TEXT,      -- ISO 'YYYY-MM-DD'
    resolved_date TEXT        -- ISO 'YYYY-MM-DD', NULL while unresolved
)

TABLE maintenance_tickets (
    ticket_id TEXT PRIMARY KEY, equipment_name TEXT, equipment_id TEXT,
    category TEXT,            -- e.g. sterilisation, infusion, radiology, imaging
    campus TEXT,              -- e.g. 'MediAssist Hyderabad Central'
    issue_type TEXT,          -- e.g. sensor_failure, battery_replacement, preventive_maintenance
    fault_code TEXT, raised_by TEXT,
    raised_date TEXT,         -- ISO 'YYYY-MM-DD'
    resolved_date TEXT,       -- ISO 'YYYY-MM-DD', NULL while open
    status TEXT               -- 'open' | 'in_progress' | 'resolved' | 'escalated'
)"""

_SQL_SYSTEM = f"""You are a SQLite expert for MediAssist Health Network.
Translate the user's question into ONE SQLite SELECT statement.

Schema:
{SCHEMA}

Rules:
- Output ONLY the SQL. No prose, no explanation, no markdown fences.
- SELECT queries only. Never write, update or delete.
- Dates are ISO text: use strftime('%Y-%m', col) or direct string comparison.
- A ticket is open if status IN ('open','in_progress','escalated').
- Always alias aggregates with a readable name.
- Limit results to {MAX_ROWS} rows unless the question is an aggregate."""

_ANSWER_SYSTEM = """You answer questions about MediAssist operations using SQL results.
State the figures plainly and directly. Do not invent numbers that are not in the
results. If the result set is empty, say no matching records were found.
Amounts are Indian Rupees (INR)."""

_FENCE = re.compile(r"```(?:sql)?\s*(.*?)```", re.DOTALL | re.IGNORECASE)
_SELECT = re.compile(r"\b(?:SELECT|WITH)\b.*", re.DOTALL | re.IGNORECASE)
_FORBIDDEN = re.compile(
    r"\b(INSERT|UPDATE|DELETE|DROP|ALTER|CREATE|REPLACE|TRUNCATE|ATTACH|PRAGMA)\b",
    re.IGNORECASE,
)


class UnsafeSQLError(ValueError):
    """Raised when the generated statement is not a single read-only SELECT."""


def clean_sql(raw: str) -> str:
    """Step 2: reduce raw LLM output to one safe, executable SELECT.

    LLMs wrap SQL in fences or prefix it with commentary; passing that straight
    to sqlite3 is how you get syntax errors at best.
    """
    text = raw.strip()

    fenced = _FENCE.search(text)
    if fenced:
        text = fenced.group(1).strip()

    match = _SELECT.search(text)
    if not match:
        raise UnsafeSQLError("no SELECT statement found in model output")
    text = match.group(0).strip()

    # Keep only the first statement; a trailing second one would run unchecked.
    text = text.split(";")[0].strip()

    if _FORBIDDEN.search(text):
        raise UnsafeSQLError("generated SQL contains a write operation")
    return text


def _execute(sql: str) -> tuple[list[str], list[tuple]]:
    """Step 3a: run the statement read-only against mediassist.db."""
    uri = f"file:{DB_PATH}?mode=ro"
    with sqlite3.connect(uri, uri=True) as conn:
        cursor = conn.execute(sql)
        columns = [d[0] for d in cursor.description or []]
        rows = cursor.fetchmany(MAX_ROWS)
    return columns, rows


def _format_rows(columns: list[str], rows: list[tuple]) -> str:
    if not rows:
        return "(no rows)"
    header = " | ".join(columns)
    body = "\n".join(" | ".join("" if v is None else str(v) for v in row) for row in rows)
    return f"{header}\n{body}"


def sql_rag_chain(question: str) -> str:
    """Natural language question -> natural language answer, grounded in SQL."""
    raw_sql = complete(_SQL_SYSTEM, question, temperature=0.0, max_tokens=400)
    sql = clean_sql(raw_sql)

    try:
        columns, rows = _execute(sql)
    except sqlite3.Error as exc:
        return f"I could not run that query against the database ({exc})."

    return complete(
        _ANSWER_SYSTEM,
        f"Question: {question}\n\nSQL executed:\n{sql}\n\nResults:\n{_format_rows(columns, rows)}",
        temperature=0.1,
    )


def sql_rag_chain_verbose(question: str) -> dict:
    """Same chain, exposing the intermediate SQL for tests and the API response."""
    raw_sql = complete(_SQL_SYSTEM, question, temperature=0.0, max_tokens=400)
    sql = clean_sql(raw_sql)
    columns, rows = _execute(sql)
    answer = complete(
        _ANSWER_SYSTEM,
        f"Question: {question}\n\nSQL executed:\n{sql}\n\nResults:\n{_format_rows(columns, rows)}",
        temperature=0.1,
    )
    return {"question": question, "sql": sql, "row_count": len(rows), "answer": answer}
