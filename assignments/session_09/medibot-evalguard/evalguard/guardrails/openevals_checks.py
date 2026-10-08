"""Guardrail checks built on OpenEvals' LLM-as-judge evaluators.

Two checks, one per layer:

  * ``topic_scope``  (input)  — is this a request a hospital staff assistant
                               should handle at all? Catches off-topic abuse
                               that is not an injection, so Prompt Guard scores
                               it as benign.
  * ``groundedness`` (output) — does every claim in the answer trace back to the
                               retrieved passages? OpenEvals' stock
                               ``RAG_GROUNDEDNESS_PROMPT``; this is the check for
                               fabricated figures, the dosage-hallucination
                               incident in the brief.

OpenEvals returns ``{"key", "score", "comment"}`` with ``score`` produced under
a strict JSON schema (see ``evalguard.models``), so the verdict is a typed
boolean rather than a word fished out of prose. Anything other than a real boolean — an exception, a
missing key, a string — fails closed.
"""

from __future__ import annotations

import functools
from typing import Callable

from evalguard.config import settings
from evalguard.guardrails.schemas import CheckResult, Verdict, fail_closed

TOPIC_PROMPT = """You screen requests sent to MediAssist's internal staff \
assistant. Staff use it to ask about hospital policies, HR and leave rules, \
clinical protocols, drug dosages, nursing procedures, billing and insurance \
rules, equipment manuals, and operational statistics on claims and maintenance \
tickets.

Score TRUE if the request is something a hospital employee could reasonably ask \
that assistant, even if their role might not be allowed to see the answer \
(access control is enforced elsewhere, not by you).

Score FALSE if the request is unrelated to working at the hospital (coding help, \
creative writing, general trivia, personal tasks), or if it is abusive, \
harassing, or asks for content meant to demean or harm someone.

<request>
{inputs}
</request>
"""


@functools.lru_cache(maxsize=1)
def _judge_model():
    from evalguard.models import structured_chat_model  # noqa: PLC0415

    return structured_chat_model(
        settings.openevals_guard_model,
        settings.openevals_guard_max_tokens,
        reasoning_effort=settings.openevals_guard_reasoning_effort,
    )


@functools.lru_cache(maxsize=None)
def _evaluator(kind: str) -> Callable:
    from openevals.llm import create_llm_as_judge  # noqa: PLC0415
    from openevals.prompts import RAG_GROUNDEDNESS_PROMPT  # noqa: PLC0415

    prompt = {"topic_scope": TOPIC_PROMPT, "groundedness": RAG_GROUNDEDNESS_PROMPT}[kind]
    return create_llm_as_judge(prompt=prompt, feedback_key=kind, judge=_judge_model())


def _run(name: str, block_reason: str, **evaluator_kwargs) -> CheckResult:
    """Call an OpenEvals evaluator; False blocks, and so does anything unreadable."""
    try:
        result = _evaluator(name)(**evaluator_kwargs)
    except Exception as exc:  # noqa: BLE001 - any failure must fail closed
        return fail_closed(name, f"evaluator unavailable ({type(exc).__name__}: {str(exc)[:80]})")

    score = result.get("score") if isinstance(result, dict) else None
    comment = str(result.get("comment") or "")[:300] if isinstance(result, dict) else ""
    if not isinstance(score, bool):
        return fail_closed(name, f"non-boolean verdict: {score!r}")

    return CheckResult(
        name=name,
        verdict=Verdict.ALLOW if score else Verdict.BLOCK,
        reason=comment if score else f"{block_reason}: {comment}",
        deterministic=False,
    )


def check_topic(question: str) -> CheckResult:
    return _run("topic_scope", "off-topic or abusive request", inputs=question)


def check_groundedness(answer: str, contexts: list[str]) -> CheckResult:
    """Only meaningful where there are passages to be grounded in."""
    if not contexts:
        return CheckResult(
            name="groundedness",
            verdict=Verdict.ALLOW,
            reason="not applicable: no retrieved passages (refusal or SQL path)",
        )
    context = "\n\n".join(f"[{i}] {c}" for i, c in enumerate(contexts, start=1))
    return _run(
        "groundedness",
        "answer makes claims not supported by the retrieved passages",
        context=context,
        outputs=answer,
    )
