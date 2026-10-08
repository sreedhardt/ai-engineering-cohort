"""Per-request latency and token accounting.

Three sources feed one context-local tally, so concurrent requests never mix
their counts:

  * the target's Groq client — its ``llm.complete()`` returns only text, so the
    client it already holds is wrapped; nothing in the target changes
  * the guardrails' direct Groq calls (Prompt Guard, policy model) — recorded
    by the caller from ``response.usage``
  * the OpenEvals checks, which run through LangChain — captured with
    LangChain's usage-metadata callback for the duration of the request
"""

from __future__ import annotations

import contextvars
from contextlib import contextmanager
from dataclasses import dataclass, field

_usage: contextvars.ContextVar["Usage | None"] = contextvars.ContextVar(
    "evalguard_usage", default=None
)


@dataclass
class Usage:
    prompt_tokens: int = 0
    completion_tokens: int = 0
    calls: int = 0
    by_model: dict[str, int] = field(default_factory=dict)

    # LangChain's usage callback, live for the request; read when reporting.
    langchain: object | None = None

    def _langchain_usage(self) -> dict[str, dict]:
        return dict(getattr(self.langchain, "usage_metadata", None) or {})

    @property
    def total_tokens(self) -> int:
        extra = sum(u.get("total_tokens", 0) for u in self._langchain_usage().values())
        return self.prompt_tokens + self.completion_tokens + extra

    def record(self, model: str, prompt: int, completion: int) -> None:
        self.prompt_tokens += prompt
        self.completion_tokens += completion
        self.calls += 1
        self.by_model[model] = self.by_model.get(model, 0) + prompt + completion

    def as_dict(self) -> dict:
        prompt, completion = self.prompt_tokens, self.completion_tokens
        by_model = dict(self.by_model)
        for model, u in self._langchain_usage().items():
            prompt += u.get("input_tokens", 0)
            completion += u.get("output_tokens", 0)
            by_model[model] = by_model.get(model, 0) + u.get("total_tokens", 0)
        return {
            "prompt_tokens": prompt,
            "completion_tokens": completion,
            "total_tokens": prompt + completion,
            "llm_calls": self.calls,
            "by_model": by_model,
        }


@contextmanager
def collecting():
    """Accumulate token usage for everything inside this block."""
    from langchain_core.callbacks import get_usage_metadata_callback  # noqa: PLC0415

    usage = Usage()
    token = _usage.set(usage)
    try:
        with get_usage_metadata_callback() as callback:
            usage.langchain = callback
            yield usage
    finally:
        _usage.reset(token)


def record(model: str, prompt: int, completion: int) -> None:
    current = _usage.get()
    if current is not None:
        current.record(model, prompt, completion)


def record_usage(response) -> None:
    """Record a Groq/OpenAI response's token usage against the current request."""
    usage = getattr(response, "usage", None)
    if usage is not None:
        record(
            getattr(response, "model", "unknown"),
            getattr(usage, "prompt_tokens", 0) or 0,
            getattr(usage, "completion_tokens", 0) or 0,
        )


_instrumented = False


def instrument_target_llm() -> bool:
    """Wrap the target's Groq client so its token usage is observable.

    Returns True if instrumentation was applied. Safe to call repeatedly.
    """
    global _instrumented
    if _instrumented:
        return True

    from evalguard.target import _bootstrap  # noqa: PLC0415

    _bootstrap()
    from app import llm as target_llm  # noqa: PLC0415

    client = target_llm._client()
    # The target was built for one user at a time and keeps the SDK default of
    # two retries. Under an evaluation run that turns rate limits into errored
    # answers, which would be scored as the system failing.
    from evalguard.config import settings  # noqa: PLC0415

    client.max_retries = max(client.max_retries, settings.llm_max_retries)
    completions = client.chat.completions
    original_create = completions.create

    def create(*args, **kwargs):
        response = original_create(*args, **kwargs)
        usage = getattr(response, "usage", None)
        if usage is not None:
            record(
                kwargs.get("model", "unknown"),
                getattr(usage, "prompt_tokens", 0) or 0,
                getattr(usage, "completion_tokens", 0) or 0,
            )
        return response

    # Traced as an LLM span so the exact system and user messages the target
    # sent — generation, routing and SQL alike — are visible in the trace.
    from evalguard.observability.tracing import wrap  # noqa: PLC0415

    completions.create = wrap(create, "target.llm", "llm")
    _instrumented = True
    return True
