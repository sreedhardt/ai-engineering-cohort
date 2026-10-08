"""Structured guardrail verdicts with fail-closed semantics.

Two rules encoded here rather than left to caller discipline:

  1. A verdict is a typed object, never a substring of a free-text reply. If a
     model returns prose, malformed JSON, or nothing at all, there is no verdict
     to read — and a missing verdict is a block, not a pass.
  2. The reason a request was blocked is internal. Echoing it to the user turns
     the guardrail into an oracle: "explain why you can't help" becomes a way to
     enumerate the policy. Users get a fixed, generic refusal.
"""

from __future__ import annotations

from enum import StrEnum

from pydantic import BaseModel, Field

# What the user sees, regardless of which check fired or why.
GENERIC_INPUT_REFUSAL = (
    "I can't help with that request. Please rephrase your question, or contact "
    "the owning department if you need access to something specific."
)
GENERIC_OUTPUT_REFUSAL = (
    "I wasn't able to produce a response I can safely share. Please rephrase "
    "your question, or contact the owning department."
)


class Verdict(StrEnum):
    ALLOW = "allow"
    BLOCK = "block"


class CheckResult(BaseModel):
    """One individual check within a guardrail layer."""

    name: str
    verdict: Verdict
    # Internal detail. Never returned to the end user.
    reason: str = ""
    score: float | None = None
    # True when the check could not reach a conclusion and defaulted to BLOCK.
    failed_closed: bool = False
    deterministic: bool = True

    @property
    def blocked(self) -> bool:
        return self.verdict is Verdict.BLOCK


class GuardrailDecision(BaseModel):
    """The aggregate decision for one guardrail layer."""

    stage: str  # "input" | "output"
    verdict: Verdict
    checks: list[CheckResult] = Field(default_factory=list)
    latency_seconds: float = 0.0

    @property
    def blocked(self) -> bool:
        return self.verdict is Verdict.BLOCK

    @property
    def triggered(self) -> list[CheckResult]:
        return [c for c in self.checks if c.blocked]

    @property
    def internal_reason(self) -> str:
        """Why it was blocked — for logs and traces only."""
        if not self.triggered:
            return "allowed"
        return "; ".join(f"{c.name}: {c.reason}" for c in self.triggered)

    @property
    def failed_closed(self) -> bool:
        return any(c.failed_closed for c in self.triggered)

    def user_message(self) -> str:
        return (
            GENERIC_INPUT_REFUSAL if self.stage == "input" else GENERIC_OUTPUT_REFUSAL
        )

    @classmethod
    def from_checks(cls, stage: str, checks: list[CheckResult], latency: float = 0.0):
        """Any single blocking check blocks the request."""
        verdict = (
            Verdict.BLOCK if any(c.blocked for c in checks) else Verdict.ALLOW
        )
        return cls(stage=stage, verdict=verdict, checks=checks, latency_seconds=latency)


def fail_closed(name: str, reason: str, deterministic: bool = False) -> CheckResult:
    """Build the verdict used whenever a check cannot reach a conclusion."""
    return CheckResult(
        name=name,
        verdict=Verdict.BLOCK,
        reason=f"fail-closed: {reason}",
        failed_closed=True,
        deterministic=deterministic,
    )
