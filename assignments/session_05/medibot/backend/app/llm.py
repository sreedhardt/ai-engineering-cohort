"""Groq client wrapper. All language generation goes through here."""

from __future__ import annotations

import functools

from groq import Groq

from app.config import settings


@functools.lru_cache(maxsize=1)
def _client() -> Groq:
    return Groq(api_key=settings.groq_api_key)


def complete(
    system: str,
    user: str,
    *,
    temperature: float = 0.1,
    max_tokens: int = 1024,
) -> str:
    """Single-turn completion. Low temperature: this is a reference assistant."""
    response = _client().chat.completions.create(
        model=settings.groq_model,
        messages=[
            {"role": "system", "content": system},
            {"role": "user", "content": user},
        ],
        temperature=temperature,
        max_tokens=max_tokens,
    )
    return (response.choices[0].message.content or "").strip()
