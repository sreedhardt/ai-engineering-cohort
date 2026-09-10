"""Question routing and the advisory RBAC pre-check.

Two independent decisions live here:

  * ``is_analytical`` — SQL RAG or document RAG? A keyword heuristic answers the
    obvious cases for free; only genuinely ambiguous questions cost an LLM call.
  * ``blocked_collection`` — is the user plainly asking for a collection their
    role cannot see? This exists purely to produce a *helpful refusal message*.
    It is not the security boundary; the Qdrant filter in rbac.py is. A question
    that slips past this check still retrieves nothing it shouldn't.
"""

from __future__ import annotations

import logging
import re

from app.llm import complete
from app.rbac import COLLECTION_LABELS, collections_for

logger = logging.getLogger("medibot.router")

# Reasoning models spend part of their token budget thinking before emitting any
# content, and that budget is shared. Too small a cap returns an empty string
# with finish_reason="length" rather than an error, which silently disables the
# fallback — so leave generous room for a one-word answer.
_ROUTER_MAX_TOKENS = 256

# --- Stage 1: analytical vs document -------------------------------------

_ANALYTICAL_HINTS = re.compile(
    r"\b(how many|how much|count|number of|total|sum|average|avg|median|"
    r"highest|lowest|most|least|top \d+|breakdown|per (month|department|category)|"
    r"trend|statistics|stats|percentage|ratio)\b",
    re.IGNORECASE,
)
_DATA_NOUNS = re.compile(
    r"\b(claim|claims|ticket|tickets|maintenance|escalated|approved|rejected|"
    r"pending|insurer|reimbursement|cashless)\b",
    re.IGNORECASE,
)
_DOCUMENT_HINTS = re.compile(
    r"\b(what is|what are|how do i|how should|procedure|protocol|policy|guideline|"
    r"dosage|dose|steps|explain|describe|define|when should|precautions?)\b",
    re.IGNORECASE,
)

_ROUTER_SYSTEM = """You classify questions for a hospital assistant.

Answer with exactly one word:
  SQL       - the question asks for counts, totals, averages, rankings or other
              figures computed over billing claims or equipment maintenance tickets
  DOCUMENT  - the question asks about policies, clinical protocols, drug dosages,
              nursing procedures, billing rules or equipment instructions

Answer with one word only."""


def is_analytical(question: str) -> bool:
    """Route to SQL RAG (True) or document RAG (False).

    The heuristic decides when it sees a clear aggregation cue over a data noun,
    or clear documentation phrasing. Anything else falls through to the LLM.
    """
    has_aggregation = bool(_ANALYTICAL_HINTS.search(question))
    has_data_noun = bool(_DATA_NOUNS.search(question))
    has_doc_phrasing = bool(_DOCUMENT_HINTS.search(question))

    if has_aggregation and has_data_noun:
        return True
    if has_doc_phrasing and not has_aggregation:
        return False

    verdict = complete(
        _ROUTER_SYSTEM, question, temperature=0.0, max_tokens=_ROUTER_MAX_TOKENS
    ).strip().upper()

    if "SQL" in verdict:
        return True
    if "DOCUMENT" in verdict:
        return False

    # Neither keyword came back. Fall back to document retrieval deliberately:
    # it is RBAC-filtered and open to every role, whereas SQL RAG is restricted,
    # so an unclear verdict should not grant access to operational figures.
    logger.warning("router fallback returned no verdict (%r); using document RAG", verdict)
    return False


# --- Stage 2: advisory RBAC pre-check ------------------------------------

_COLLECTION_CUES = {
    "billing": re.compile(
        r"\b(billing|insurance|insurer|claim|claims|reimburs\w*|cashless|"
        r"pre-?auth\w*|icd[- ]?10|procedure code|diagnosis code|copay|deductible|"
        r"tariff|invoice)\b",
        re.IGNORECASE,
    ),
    "clinical": re.compile(
        r"\b(drug|drugs|formulary|dosage|dose|mg\b|prescri\w+|treatment protocol|"
        r"diagnostic|differential diagnosis|contraindicat\w+|antimicrobial|"
        r"pharmacolog\w+)\b",
        re.IGNORECASE,
    ),
    "equipment": re.compile(
        r"\b(equipment|calibrat\w+|maintenance schedule|ventilator|infusion pump|"
        r"sterilis\w+|steriliz\w+|fault code|servicing|technician manual)\b",
        re.IGNORECASE,
    ),
    "nursing": re.compile(
        r"\b(nursing|nurse|icu procedure|infection control|cannula|catheter|"
        r"bedside|patient care|hand hygiene)\b",
        re.IGNORECASE,
    ),
}


def target_collection(question: str) -> str | None:
    """Best guess at which collection a question is aimed at, or None."""
    hits = [name for name, cue in _COLLECTION_CUES.items() if cue.search(question)]
    # Only act on an unambiguous signal — a question hitting several collections
    # is better served by letting the filtered search decide.
    return hits[0] if len(hits) == 1 else None


def blocked_collection(question: str, role: str) -> str | None:
    """The restricted collection this question targets, if any."""
    target = target_collection(question)
    if target is None or target in collections_for(role):
        return None
    return target


def refusal_message(role: str, blocked: str) -> str:
    """The user-facing explanation for a pre-empted question."""
    allowed = collections_for(role)
    readable = [COLLECTION_LABELS.get(c, c) for c in allowed]
    if len(readable) > 1:
        allowed_text = ", ".join(readable[:-1]) + f" and {readable[-1]}"
    else:
        allowed_text = readable[0]
    label = COLLECTION_LABELS.get(blocked, blocked)
    role_words = role.replace("_", " ")
    article = "an" if role_words[0].lower() in "aeiou" else "a"
    return (
        f"As {article} {role_words}, you don't have access to {label} "
        f"documents. I can only answer questions from the {allowed_text} collections."
    )
