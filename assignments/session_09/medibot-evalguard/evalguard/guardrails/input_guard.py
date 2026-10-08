"""Input guardrail: runs before the target system sees the request.

Four checks, cheapest first, stopping at the first block:

  1. shape          — empty, oversized, or non-text input (deterministic)
  2. override_intent— phrases that try to escalate role or access (deterministic)
  3. prompt_guard   — Llama Prompt Guard 2, a classifier trained specifically on
                      injection and jailbreak attempts
  4. topic_scope    — OpenEvals LLM-as-judge: off-topic or abusive requests,
                      which are not injections and so pass Prompt Guard

Prompt Guard is used rather than "ask an LLM whether this looks like an attack"
because it returns a **probability**, so the decision is a threshold on a number
rather than an interpretation of prose. Measured on our own adversarial set it
scores ~0.0004 on benign clinical questions and >0.99 on injection attempts, so
the default 0.5 cut-off sits in a wide gap rather than on a knife edge.

Every failure path blocks: a transport error, a non-numeric response or an
unparseable score all produce a fail-closed verdict.
"""

from __future__ import annotations

import functools
import re
import time

from groq import Groq

from evalguard.config import settings
from evalguard.guardrails.openevals_checks import check_topic
from evalguard.guardrails.schemas import (
    CheckResult,
    GuardrailDecision,
    Verdict,
    fail_closed,
)
from evalguard.observability.metrics import record_usage
from evalguard.observability.tracing import traced

MAX_QUESTION_CHARS = 2000

# Attempts to override role or access control. These are deliberately narrow:
# the probabilistic classifier is the general net, this catches the specific
# phrasings that matter for a role-scoped assistant.
_OVERRIDE_PATTERNS = [
    (r"\bignore\s+(all\s+|your\s+|previous\s+|the\s+)*instruction", "instruction override"),
    (r"\bdisregard\s+(all\s+|your\s+|previous\s+|the\s+)*(instruction|rule|restriction)", "instruction override"),
    (r"\b(you are|act as|pretend to be)\s+(now\s+)?(an?\s+)?(admin|administrator|root|superuser)", "role escalation"),
    (r"\b(maintenance|developer|debug|god)\s+mode\b", "mode escalation"),
    (r"\bno\s+(restrictions|limits|rules|guardrails)\b", "restriction bypass"),
    (r"\bbypass\s+(the\s+)?(rbac|access|permission|restriction|filter)", "access bypass"),
    (r"\b(as|i am)\s+the\s+(cmo|ceo|cto|chief|director)\b.{0,40}\b(authorise|authorize|grant|give me)", "false authority"),
    (r"\boverride\s+(your\s+|the\s+)?(access|role|permission|restriction)", "access override"),
    (r"\bsystem\s*prompt\b", "system prompt probing"),
]
_COMPILED = [(re.compile(p, re.IGNORECASE), label) for p, label in _OVERRIDE_PATTERNS]


@functools.lru_cache(maxsize=1)
def _client() -> Groq:
    return Groq(api_key=settings.groq_api_key, max_retries=settings.llm_max_retries)


def check_shape(question: str) -> CheckResult:
    name = "input_shape"
    if not isinstance(question, str) or not question.strip():
        return CheckResult(
            name=name, verdict=Verdict.BLOCK, reason="empty or non-text question"
        )
    if len(question) > MAX_QUESTION_CHARS:
        return CheckResult(
            name=name,
            verdict=Verdict.BLOCK,
            reason=f"question exceeds {MAX_QUESTION_CHARS} chars ({len(question)})",
        )
    return CheckResult(name=name, verdict=Verdict.ALLOW)


def check_override_intent(question: str) -> CheckResult:
    name = "override_intent"
    hits = [label for pattern, label in _COMPILED if pattern.search(question)]
    if hits:
        return CheckResult(
            name=name,
            verdict=Verdict.BLOCK,
            reason=f"matched {', '.join(sorted(set(hits)))}",
        )
    return CheckResult(name=name, verdict=Verdict.ALLOW)


@traced("prompt_guard", run_type="llm")
def check_prompt_guard(question: str) -> CheckResult:
    """Llama Prompt Guard 2 — returns P(injection) as a bare number."""
    name = "prompt_guard"
    try:
        response = _client().chat.completions.create(
            model=settings.input_guard_model,
            messages=[{"role": "user", "content": question}],
            max_tokens=16,
        )
        raw = (response.choices[0].message.content or "").strip()
    except Exception as exc:  # noqa: BLE001 - any failure must fail closed
        return fail_closed(name, f"classifier unavailable ({type(exc).__name__})")
    record_usage(response)

    if not raw:
        return fail_closed(name, "classifier returned empty response")

    try:
        score = float(raw)
    except ValueError:
        return fail_closed(name, f"classifier returned non-numeric output: {raw[:40]!r}")

    if not 0.0 <= score <= 1.0:
        return fail_closed(name, f"score out of range: {score}")

    blocked = score >= settings.input_guard_threshold
    return CheckResult(
        name=name,
        verdict=Verdict.BLOCK if blocked else Verdict.ALLOW,
        reason=(
            f"injection probability {score:.4f} >= threshold "
            f"{settings.input_guard_threshold}"
            if blocked
            else f"injection probability {score:.4f}"
        ),
        score=score,
        deterministic=False,
    )


@traced("input_guardrail", run_type="chain")
def check_input(question: str) -> GuardrailDecision:
    """Run the input guardrail. Short-circuits on the cheap deterministic checks."""
    started = time.perf_counter()
    checks: list[CheckResult] = []

    # Looked up at call time so tests can substitute any one check.
    for check in (check_shape, check_override_intent, check_prompt_guard, check_topic):
        result = check(question)
        checks.append(result)
        if result.blocked:
            # No point paying for a model call on input we have already rejected.
            break

    return GuardrailDecision.from_checks(
        "input", checks, time.perf_counter() - started
    )
