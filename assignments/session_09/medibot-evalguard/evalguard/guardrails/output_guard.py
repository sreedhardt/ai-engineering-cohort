"""Output guardrail: runs after the target answers, before the user sees it.

The ordering here is the whole point. The **deterministic** RBAC-leak check is
the boundary; the policy model is corroborating signal only.

That is a measured decision, not a stylistic one. Asked to judge whether the
answer "ICD-10 code I21.4 is billed at tariff 45000 INR per the billing
reference" should be shown to a nurse, the policy model returned
``{"verdict":"allow"}``. It is wrong in the unsafe direction. Meanwhile the
target already returns structured citations carrying a ``collection`` field, so
"did this answer cite something this role cannot read" is decidable with
certainty and no model at all.

So: deterministic checks decide, the models add second opinions that can only
ever make the guardrail *stricter*, and anything unparseable fails closed. The
two model checks are:

  * groundedness — OpenEvals' RAG groundedness judge, for fabricated claims
  * policy_model — gpt-oss-safeguard, for policy-level disclosure only. It once
    also flagged "unattributed figures", and blocked correct SQL answers ("8
    claims are escalated") whose source is the database rather than a passage.
    Fabrication is groundedness's job, which can see the passages.
"""

from __future__ import annotations

import functools
import json
import re
import time

from groq import Groq

from evalguard.config import settings
from evalguard.guardrails.openevals_checks import check_groundedness
from evalguard.guardrails.patterns import find_pii
from evalguard.guardrails.schemas import (
    CheckResult,
    GuardrailDecision,
    Verdict,
    fail_closed,
)
from evalguard.observability.metrics import record_usage
from evalguard.observability.tracing import traced
from evalguard.target import collections_for

_FENCE = re.compile(r"```(?:json)?\s*(.*?)```", re.DOTALL)


@functools.lru_cache(maxsize=1)
def _client() -> Groq:
    return Groq(api_key=settings.groq_api_key, max_retries=settings.llm_max_retries)


def check_non_empty(answer: str) -> CheckResult:
    name = "non_empty_answer"
    if not answer or not answer.strip():
        return CheckResult(
            name=name, verdict=Verdict.BLOCK, reason="target returned an empty answer"
        )
    return CheckResult(name=name, verdict=Verdict.ALLOW)


def check_rbac_leak(role: str, sources: list[dict]) -> CheckResult:
    """The real boundary: no citation may come from a collection this role cannot read."""
    name = "rbac_leak"
    try:
        permitted = set(collections_for(role))
    except Exception as exc:  # noqa: BLE001 - unknown role must not pass silently
        return fail_closed(name, f"cannot resolve permissions for role {role!r} ({exc})", deterministic=True)

    cited = {s.get("collection") for s in (sources or []) if s.get("collection")}
    leaked = sorted(cited - permitted)
    if leaked:
        return CheckResult(
            name=name,
            verdict=Verdict.BLOCK,
            reason=f"role {role!r} cited restricted collections: {leaked}",
        )
    return CheckResult(
        name=name,
        verdict=Verdict.ALLOW,
        reason=f"all {len(cited)} cited collection(s) permitted for {role!r}",
    )


def check_pii(answer: str) -> CheckResult:
    name = "pii_leak"
    hits = find_pii(answer)
    if hits:
        kinds = sorted({kind for kind, _ in hits})
        return CheckResult(
            name=name,
            verdict=Verdict.BLOCK,
            reason=f"answer contains {len(hits)} PII match(es): {kinds}",
        )
    return CheckResult(name=name, verdict=Verdict.ALLOW)


_POLICY_TEMPLATE = """You are a safety classifier for a hospital assistant with \
role-based access control.

The user's role is: {role}
Collections this role MAY read: {permitted}
Collections this role MUST NOT read: {forbidden}

Block the response if it discloses substantive content from a forbidden \
collection, or exposes patient-identifying information. Whether the response is \
factually supported is checked separately; do not judge that here.

Respond with JSON only, no other text:
{{"verdict": "allow" or "block", "reason": "<one short sentence>"}}"""


@traced("policy_model", run_type="llm")
def check_policy_model(role: str, question: str, answer: str) -> CheckResult:
    """Secondary opinion from a policy-classification model. May only add blocks."""
    name = "policy_model"
    try:
        permitted = collections_for(role)
    except Exception as exc:  # noqa: BLE001
        return fail_closed(name, f"unknown role {role!r} ({exc})")

    all_collections = {"general", "clinical", "nursing", "billing", "equipment"}
    system = _POLICY_TEMPLATE.format(
        role=role,
        permitted=", ".join(sorted(permitted)),
        forbidden=", ".join(sorted(all_collections - set(permitted))) or "none",
    )

    try:
        response = _client().chat.completions.create(
            model=settings.output_policy_model,
            messages=[
                {"role": "system", "content": system},
                {"role": "user", "content": f"Question: {question}\n\nResponse:\n{answer}"},
            ],
            temperature=0.0,
            max_tokens=settings.output_policy_max_tokens,
            reasoning_effort=settings.output_policy_reasoning_effort,
        )
        choice = response.choices[0]
        raw = (choice.message.content or "").strip()
    except Exception as exc:  # noqa: BLE001
        return fail_closed(name, f"policy model unavailable ({type(exc).__name__})")
    record_usage(response)

    if not raw:
        # This model reasons before answering out of a shared token budget; an
        # exhausted budget yields empty content rather than an error.
        return fail_closed(
            name, f"empty verdict (finish_reason={choice.finish_reason})"
        )

    fenced = _FENCE.search(raw)
    if fenced:
        raw = fenced.group(1).strip()

    try:
        payload = json.loads(raw)
    except json.JSONDecodeError:
        return fail_closed(name, f"unparseable verdict: {raw[:60]!r}")

    verdict_raw = str(payload.get("verdict", "")).strip().lower()
    if verdict_raw not in {"allow", "block"}:
        return fail_closed(name, f"missing or invalid verdict field: {verdict_raw!r}")

    reason = str(payload.get("reason", ""))[:200]
    return CheckResult(
        name=name,
        verdict=Verdict.BLOCK if verdict_raw == "block" else Verdict.ALLOW,
        reason=reason or "no reason given",
        deterministic=False,
    )


@traced("output_guardrail", run_type="chain")
def check_output(
    role: str,
    question: str,
    answer: str,
    sources: list[dict] | None = None,
    contexts: list[str] | None = None,
    *,
    use_policy_model: bool = True,
) -> GuardrailDecision:
    started = time.perf_counter()
    checks = [
        check_non_empty(answer),
        check_rbac_leak(role, sources or []),
        check_pii(answer),
    ]
    # Model-backed checks only add blocks, so skip them once the deterministic
    # checks have already decided.
    if use_policy_model and not any(c.blocked for c in checks):
        checks.append(check_groundedness(answer, contexts or []))
        checks.append(check_policy_model(role, question, answer))
    return GuardrailDecision.from_checks(
        "output", checks, time.perf_counter() - started
    )
