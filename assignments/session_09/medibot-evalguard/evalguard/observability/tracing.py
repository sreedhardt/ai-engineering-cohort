"""LangSmith tracing over the target system's request path.

The target's own functions are wrapped here rather than decorated in place, so
session_05 stays untouched while its internals still appear as named spans:

    guarded_chat
      ├─ input_guardrail
      ├─ target.retrieve          (hybrid search + RBAC filter)
      │    └─ target.rerank       (cross-encoder)
      ├─ target.generate_answer   (LLM)
      └─ output_guardrail

Tracing degrades to a no-op when no API key is configured, so the evaluation
pipeline still runs offline.
"""

from __future__ import annotations

import functools
import os
from typing import Any, Callable

from evalguard.config import settings

_configured = False


def configure() -> bool:
    """Point the LangSmith SDK at the configured project. Idempotent."""
    global _configured
    if _configured:
        return bool(settings.langsmith_api_key)
    _configured = True

    if not settings.langsmith_api_key:
        os.environ["LANGSMITH_TRACING"] = "false"
        return False

    os.environ["LANGSMITH_API_KEY"] = settings.langsmith_api_key
    os.environ["LANGSMITH_PROJECT"] = settings.langsmith_project
    os.environ["LANGSMITH_TRACING"] = "true" if settings.langsmith_tracing else "false"
    return True


def traced(name: str, run_type: str = "chain") -> Callable:
    """Decorate a function as a LangSmith span.

    Falls back to the undecorated function when tracing is unavailable, so a
    missing key degrades observability without breaking the request path.
    """

    def decorator(func: Callable) -> Callable:
        @functools.wraps(func)
        def wrapper(*args: Any, **kwargs: Any) -> Any:
            if not configure():
                return func(*args, **kwargs)
            from langsmith import traceable  # noqa: PLC0415

            return traceable(name=name, run_type=run_type)(func)(*args, **kwargs)

        return wrapper

    return decorator


def wrap(func: Callable, name: str, run_type: str = "chain") -> Callable:
    """Wrap an existing function (one we do not own) as a span."""
    return traced(name, run_type)(func)


def attach_metadata(**fields) -> bool:
    """Attach key/value metadata to the currently active span.

    Token counts are known only once a request finishes, and the target is not
    a LangChain model so LangSmith cannot infer them. Writing them onto the run
    keeps cost and outcome visible in the trace UI rather than only in the logs.
    """
    if not configure():
        return False
    try:
        from langsmith.run_helpers import get_current_run_tree  # noqa: PLC0415

        run = get_current_run_tree()
        if run is None:
            return False
        run.extra.setdefault("metadata", {}).update(
            {k: v for k, v in fields.items() if v is not None}
        )
        return True
    except Exception:  # noqa: BLE001 - observability must never break the request
        return False
