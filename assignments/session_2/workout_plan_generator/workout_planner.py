"""Core logic for the workout plan generator: validation, Groq calls, parsing.

Deliberately free of Streamlit imports so it can be unit-tested and reused.
"""

from __future__ import annotations

import os
import re
from dataclasses import dataclass, field
from enum import StrEnum
from typing import Final

import groq
from groq import Groq

import prompts

MODEL: Final[str] = "openai/gpt-oss-120b"
BASE_TEMPERATURE: Final[float] = 0.4
REGENERATE_TEMPERATURE: Final[float] = 0.9
MAX_TOKENS: Final[int] = 4096
MIN_DAYS: Final[int] = 1
MAX_DAYS: Final[int] = 7
MAX_LIMITATION_CHARS: Final[int] = 500

_DAY_HEADING = re.compile(r"^#{1,4}\s*Day\s+\d+", re.MULTILINE | re.IGNORECASE)


class FitnessGoal(StrEnum):
    BUILD_MUSCLE = "Build muscle"
    LOSE_FAT = "Lose fat"
    GENERAL_FITNESS = "General fitness"
    IMPROVE_ENDURANCE = "Improve endurance"


class ExperienceLevel(StrEnum):
    BEGINNER = "Beginner"
    INTERMEDIATE = "Intermediate"
    ADVANCED = "Advanced"


class Equipment(StrEnum):
    NONE = "No equipment"
    HOME_DUMBBELLS = "Home dumbbells"
    FULL_GYM = "Full gym"


@dataclass(slots=True)
class PlanResult:
    """Outcome of a generation attempt.

    Always returned - the caller never has to catch an exception. `ok` tells the
    UI which of `plan_markdown` / `error_message` to render.
    """

    ok: bool
    plan_markdown: str = ""
    error_message: str = ""
    warnings: list[str] = field(default_factory=list)

    @classmethod
    def failure(cls, message: str) -> PlanResult:
        return cls(ok=False, error_message=message)


def validate_inputs(
    goal: str,
    experience: str,
    days_per_week: int,
    equipment: str,
    limitations: str | None = None,
) -> list[str]:
    """Return a list of human-readable problems. Empty list means valid."""
    problems: list[str] = []

    if goal not in set(FitnessGoal):
        problems.append("Pick a fitness goal from the list.")
    if experience not in set(ExperienceLevel):
        problems.append("Pick an experience level from the list.")
    if equipment not in set(Equipment):
        problems.append("Pick the equipment you have access to.")

    if not isinstance(days_per_week, int) or isinstance(days_per_week, bool):
        problems.append("Days per week must be a whole number.")
    elif not MIN_DAYS <= days_per_week <= MAX_DAYS:
        problems.append(
            f"Choose between {MIN_DAYS} and {MAX_DAYS} training days per week "
            f"(you chose {days_per_week})."
        )

    if limitations and len(limitations.strip()) > MAX_LIMITATION_CHARS:
        problems.append(
            f"Keep injuries/limitations under {MAX_LIMITATION_CHARS} characters "
            f"(currently {len(limitations.strip())})."
        )

    return problems


def resolve_api_key(explicit_key: str | None = None) -> str | None:
    """Prefer a key typed into the UI, else fall back to the environment."""
    key = (explicit_key or "").strip() or os.environ.get("GROQ_API_KEY", "").strip()
    return key or None


def count_day_sections(plan_markdown: str) -> int:
    """How many 'Day N' sections the model actually produced."""
    return len(_DAY_HEADING.findall(plan_markdown))


def _extract_content(response: object) -> str:
    """Pull message content out of a completion, tolerating malformed shapes."""
    choices = getattr(response, "choices", None) or []
    if not choices:
        return ""
    message = getattr(choices[0], "message", None)
    return (getattr(message, "content", None) or "").strip()


def _friendly_api_error(exc: Exception) -> str:
    """Map an SDK exception to something a user can act on."""
    match exc:
        case groq.AuthenticationError() | groq.PermissionDeniedError():
            return (
                "That Groq API key was rejected. Check the key in your `.env` file "
                "(or paste a valid one in the sidebar) and try again."
            )
        case groq.RateLimitError():
            return (
                "Groq is rate-limiting this key right now. Wait a few seconds and "
                "hit Generate again."
            )
        case groq.APIConnectionError():
            return (
                "Couldn't reach Groq. Check your internet connection and try again."
            )
        case groq.NotFoundError():
            return (
                f"The model `{MODEL}` isn't available to this key. Pick a different "
                "model in `workout_planner.py`."
            )
        case groq.BadRequestError():
            return (
                "Groq rejected the request. If you entered a very long list of "
                "limitations, try shortening it."
            )
        case groq.APIStatusError():
            status = getattr(exc, "status_code", "unknown")
            return f"Groq returned an error (HTTP {status}). Please try again."
        case _:
            return f"Something went wrong while generating the plan: {exc}"


def _call_groq(
    client: Groq, system_prompt: str, user_message: str, temperature: float
) -> str:
    response = client.chat.completions.create(
        model=MODEL,
        temperature=temperature,
        max_tokens=MAX_TOKENS,
        messages=[
            {"role": "system", "content": system_prompt},
            {"role": "user", "content": user_message},
        ],
    )
    return _extract_content(response)


def generate_workout_plan(
    goal: FitnessGoal | str,
    experience: ExperienceLevel | str,
    days_per_week: int,
    equipment: Equipment | str,
    limitations: str | None = None,
    *,
    variation_seed: int = 0,
    api_key: str | None = None,
    client: Groq | None = None,
) -> PlanResult:
    """Generate a weekly workout plan from structured inputs.

    Args:
        goal: What the client is training for.
        experience: How much training history they have.
        days_per_week: Training days available, 1-7.
        equipment: What they can train with.
        limitations: Free-text injuries or restrictions, if any.
        variation_seed: Bump to push the model toward a different plan.
        api_key: Groq key; falls back to the GROQ_API_KEY environment variable.
        client: Pre-built Groq client, primarily for tests.

    Returns:
        A PlanResult. Never raises - failures come back as `ok=False` with a
        message suitable for showing directly to the user.
    """
    problems = validate_inputs(
        str(goal), str(experience), days_per_week, str(equipment), limitations
    )
    if problems:
        return PlanResult.failure(" ".join(problems))

    if client is None:
        key = resolve_api_key(api_key)
        if not key:
            return PlanResult.failure(
                "No Groq API key found. Add `GROQ_API_KEY=...` to a `.env` file in "
                "this folder, or paste a key into the sidebar."
            )
        try:
            client = Groq(api_key=key)
        except Exception as exc:  # pragma: no cover - client init rarely fails
            return PlanResult.failure(f"Couldn't start the Groq client: {exc}")

    limitation_text = (limitations or "").strip()
    system_prompt = prompts.build_system_prompt(
        has_limitations=bool(limitation_text), variation_seed=variation_seed
    )
    user_message = prompts.build_user_message(
        goal=str(goal),
        experience=str(experience),
        days_per_week=days_per_week,
        equipment=str(equipment),
        limitations=limitation_text or None,
    )
    temperature = BASE_TEMPERATURE if variation_seed == 0 else REGENERATE_TEMPERATURE

    try:
        content = _call_groq(client, system_prompt, user_message, temperature)
    except Exception as exc:
        return PlanResult.failure(_friendly_api_error(exc))

    if not content:
        return PlanResult.failure(
            "The model returned an empty plan. This usually clears up on a retry - "
            "press Generate again."
        )

    day_count = count_day_sections(content)
    if day_count == 0:
        return PlanResult.failure(
            "The model returned a response that doesn't look like a workout plan. "
            "Press Generate again to retry."
        )

    warnings: list[str] = []
    if day_count != days_per_week:
        warnings.append(
            f"You asked for {days_per_week} training day(s) but the plan came back "
            f"with {day_count}. Try Regenerate if that doesn't work for you."
        )

    return PlanResult(ok=True, plan_markdown=content, warnings=warnings)


def swap_exercise(
    exercise: str,
    plan_markdown: str,
    equipment: Equipment | str,
    limitations: str | None = None,
    *,
    api_key: str | None = None,
    client: Groq | None = None,
) -> PlanResult:
    """Suggest a single constraint-respecting replacement for one exercise."""
    if not exercise.strip():
        return PlanResult.failure("Type the exercise you'd like to swap out.")
    if not plan_markdown.strip():
        return PlanResult.failure("Generate a plan first, then swap an exercise.")

    if client is None:
        key = resolve_api_key(api_key)
        if not key:
            return PlanResult.failure(
                "No Groq API key found. Add `GROQ_API_KEY=...` to a `.env` file in "
                "this folder, or paste a key into the sidebar."
            )
        try:
            client = Groq(api_key=key)
        except Exception as exc:  # pragma: no cover - client init rarely fails
            return PlanResult.failure(f"Couldn't start the Groq client: {exc}")

    system_prompt = prompts.build_system_prompt(
        has_limitations=bool((limitations or "").strip())
    )
    user_message = prompts.build_swap_message(
        exercise=exercise.strip(),
        plan_markdown=plan_markdown,
        equipment=str(equipment),
        limitations=limitations,
    )

    try:
        content = _call_groq(client, system_prompt, user_message, BASE_TEMPERATURE)
    except Exception as exc:
        return PlanResult.failure(_friendly_api_error(exc))

    if not content:
        return PlanResult.failure(
            "The model didn't suggest a swap. Try again, or rephrase the exercise "
            "name to match the plan."
        )

    return PlanResult(ok=True, plan_markdown=content)
