"""The guarded request path: input guardrail → target → output guardrail.

This is the seam the platform exists to provide. Nothing calls the target
directly; every request passes both guardrails, every decision is logged as a
structured event, and the whole path is one LangSmith trace.
"""

from __future__ import annotations

import time
from dataclasses import dataclass, field

from evalguard.guardrails.input_guard import check_input
from evalguard.guardrails.output_guard import check_output
from evalguard.guardrails.schemas import GuardrailDecision
from evalguard.observability.events import emit, new_request_id
from evalguard.observability.metrics import collecting
from evalguard.observability.tracing import attach_metadata, traced
from evalguard.target import TargetAnswer, answer_question


@dataclass
class GuardedResponse:
    request_id: str
    question: str
    role: str
    answer: str
    blocked: bool
    blocked_stage: str | None = None
    retrieval_type: str = "unknown"
    sources: list[dict] = field(default_factory=list)
    contexts: list[str] = field(default_factory=list)
    latency_seconds: float = 0.0
    usage: dict = field(default_factory=dict)
    # Guardrail detail: for logs, traces and the evaluation report — not the user.
    input_decision: GuardrailDecision | None = None
    output_decision: GuardrailDecision | None = None
    target: TargetAnswer | None = None

    @property
    def internal_reason(self) -> str:
        if self.blocked_stage == "input" and self.input_decision:
            return self.input_decision.internal_reason
        if self.blocked_stage == "output" and self.output_decision:
            return self.output_decision.internal_reason
        return "allowed"


def _log_guardrail(request_id: str, role: str, decision: GuardrailDecision) -> None:
    emit(
        "guardrail",
        request_id=request_id,
        stage=decision.stage,
        role=role,
        decision=decision.verdict.value,
        reason=decision.internal_reason,
        failed_closed=decision.failed_closed,
        latency_seconds=round(decision.latency_seconds, 4),
        checks=[
            {
                "name": c.name,
                "verdict": c.verdict.value,
                "reason": c.reason,
                "score": c.score,
                "deterministic": c.deterministic,
                "failed_closed": c.failed_closed,
            }
            for c in decision.checks
        ],
    )


@traced("guarded_chat", run_type="chain")
def guarded_chat(question: str, role: str, *, use_policy_model: bool = True) -> GuardedResponse:
    request_id = new_request_id()
    started = time.perf_counter()

    emit("request", request_id=request_id, role=role, question=question)

    with collecting() as usage:
        # --- input guardrail ------------------------------------------------
        input_decision = check_input(question)
        _log_guardrail(request_id, role, input_decision)

        if input_decision.blocked:
            elapsed = time.perf_counter() - started
            emit(
                "response",
                request_id=request_id,
                role=role,
                blocked=True,
                stage="input",
                retrieval_type="blocked",
                latency_seconds=round(elapsed, 4),
                **usage.as_dict(),
            )
            attach_metadata(
                request_id=request_id,
                role=role,
                blocked=True,
                blocked_stage="input",
                retrieval_type="blocked",
                latency_seconds=round(elapsed, 4),
                **usage.as_dict(),
            )
            return GuardedResponse(
                request_id=request_id,
                question=question,
                role=role,
                answer=input_decision.user_message(),
                blocked=True,
                blocked_stage="input",
                retrieval_type="blocked",
                latency_seconds=elapsed,
                usage=usage.as_dict(),
                input_decision=input_decision,
            )

        # --- target system --------------------------------------------------
        target = answer_question(question, role)

        # --- output guardrail -----------------------------------------------
        output_decision = check_output(
            role, question, target.answer, target.sources, target.contexts,
            use_policy_model=use_policy_model,
        )
        _log_guardrail(request_id, role, output_decision)

        elapsed = time.perf_counter() - started
        blocked = output_decision.blocked
        emit(
            "response",
            request_id=request_id,
            role=role,
            blocked=blocked,
            stage="output" if blocked else None,
            retrieval_type=target.retrieval_type,
            source_count=len(target.sources),
            latency_seconds=round(elapsed, 4),
            target_latency_seconds=round(target.latency_seconds, 4),
            error=target.error,
            **usage.as_dict(),
        )

        # Surface the metrics on the trace itself, so a reviewer opening a run
        # sees cost and outcome without cross-referencing the event log.
        attach_metadata(
            request_id=request_id,
            role=role,
            blocked=blocked,
            blocked_stage="output" if blocked else None,
            retrieval_type=target.retrieval_type,
            latency_seconds=round(elapsed, 4),
            **usage.as_dict(),
        )

        return GuardedResponse(
            request_id=request_id,
            question=question,
            role=role,
            answer=output_decision.user_message() if blocked else target.answer,
            blocked=blocked,
            blocked_stage="output" if blocked else None,
            retrieval_type=target.retrieval_type,
            sources=[] if blocked else target.sources,
            contexts=target.contexts,
            latency_seconds=elapsed,
            usage=usage.as_dict(),
            input_decision=input_decision,
            output_decision=output_decision,
            target=target,
        )
