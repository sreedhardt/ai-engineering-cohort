"""Grounded answer generation over reranked chunks."""

from __future__ import annotations

import re

from app.llm import complete
from app.retrieval import RetrievedChunk

# Some models emit CJK fullwidth brackets for citations regardless of the
# instruction to use ASCII, so normalise rather than trusting the prompt.
_FULLWIDTH_CITATION = re.compile(r"[【\[]\s*(\d+)\s*[】\]]")


def _normalise_citations(text: str) -> str:
    return _FULLWIDTH_CITATION.sub(r"[\1]", text)

_SYSTEM = """You are MediBot, the internal assistant for MediAssist Health Network.

Answer strictly from the numbered context passages provided. Rules:
- If the passages do not contain the answer, say so plainly. Never fill the gap
  from your own knowledge — this is clinical and policy material where a
  plausible-sounding invention is dangerous.
- Cite the passages you used inline as [1], [2], matching the numbering given.
  Use plain ASCII square brackets exactly like [1] — never fullwidth or CJK
  bracket characters.
- Preserve exact figures: dosages, codes, timelines and amounts must match the
  passages character for character.
- Be concise and factual. Prefer a short paragraph or a tight list.
- The user's role is given only so you can pitch the wording appropriately. It
  never expands what you may disclose: the passages are the only source."""


def _format_context(chunks: list[RetrievedChunk]) -> str:
    blocks = []
    for i, chunk in enumerate(chunks, start=1):
        blocks.append(
            f"[{i}] Source: {chunk.source_document} — Section: {chunk.section_title}\n"
            f"{chunk.text}"
        )
    return "\n\n".join(blocks)


def generate_answer(question: str, role: str, chunks: list[RetrievedChunk]) -> str:
    if not chunks:
        return (
            "I couldn't find anything in the documents available to your role "
            "that answers that. Try rephrasing, or check with the owning department."
        )
    user = (
        f"User role: {role}\n"
        f"Question: {question}\n\n"
        f"Context passages:\n{_format_context(chunks)}"
    )
    return _normalise_citations(complete(_SYSTEM, user))
