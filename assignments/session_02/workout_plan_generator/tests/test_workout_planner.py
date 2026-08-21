"""Unit tests for the planner. No network calls - the Groq client is faked."""

from __future__ import annotations

import pytest

import prompts
import workout_planner as wp


class _FakeMessage:
    def __init__(self, content: str | None) -> None:
        self.content = content


class _FakeChoice:
    def __init__(self, content: str | None) -> None:
        self.message = _FakeMessage(content)


class _FakeResponse:
    def __init__(self, content: str | None, *, choices: bool = True) -> None:
        self.choices = [_FakeChoice(content)] if choices else []


class _FakeCompletions:
    def __init__(self, response: object | None, error: Exception | None) -> None:
        self._response = response
        self._error = error
        self.last_kwargs: dict = {}

    def create(self, **kwargs):
        self.last_kwargs = kwargs
        if self._error is not None:
            raise self._error
        return self._response


class FakeGroq:
    """Stands in for groq.Groq, recording the request it was given."""

    def __init__(self, content: str | None = None, *, error: Exception | None = None,
                 choices: bool = True) -> None:
        response = _FakeResponse(content, choices=choices)
        self.chat = type("Chat", (), {})()
        self.chat.completions = _FakeCompletions(response, error)


THREE_DAY_PLAN = """## Your Plan
A three day full body split.

## Day 1 - Full Body
| Exercise | Sets | Reps | Rest | Notes |
| --- | --- | --- | --- | --- |
| Goblet Squat | 3 | 8-10 | 90s | Chest tall |

## Day 2 - Full Body
Some content.

## Day 3 - Full Body
Some content.
"""


# --- validation -------------------------------------------------------------

def test_valid_inputs_produce_no_problems():
    assert wp.validate_inputs("Build muscle", "Beginner", 3, "Full gym", None) == []


def test_zero_days_is_rejected():
    problems = wp.validate_inputs("Build muscle", "Beginner", 0, "Full gym")
    assert len(problems) == 1
    assert "between 1 and 7" in problems[0]


def test_too_many_days_is_rejected():
    assert wp.validate_inputs("Build muscle", "Beginner", 9, "Full gym")


def test_missing_dropdowns_are_reported_individually():
    problems = wp.validate_inputs("", "", 3, "")
    assert len(problems) == 3


def test_overlong_limitations_are_rejected():
    problems = wp.validate_inputs(
        "Lose fat", "Advanced", 4, "Full gym", "x" * (wp.MAX_LIMITATION_CHARS + 1)
    )
    assert problems and "characters" in problems[0]


def test_bool_is_not_accepted_as_days():
    assert wp.validate_inputs("Lose fat", "Beginner", True, "Full gym")


# --- parsing helpers --------------------------------------------------------

@pytest.mark.parametrize(
    ("text", "expected"),
    [(THREE_DAY_PLAN, 3), ("no plan here", 0), ("# Day 1\n# Day 2", 2)],
)
def test_count_day_sections(text: str, expected: int):
    assert wp.count_day_sections(text) == expected


def test_extract_content_handles_missing_choices():
    assert wp._extract_content(_FakeResponse("x", choices=False)) == ""


def test_extract_content_handles_none_content():
    assert wp._extract_content(_FakeResponse(None)) == ""


# --- generation -------------------------------------------------------------

def test_successful_generation_returns_plan():
    result = wp.generate_workout_plan(
        wp.FitnessGoal.BUILD_MUSCLE, wp.ExperienceLevel.BEGINNER, 3,
        wp.Equipment.FULL_GYM, client=FakeGroq(THREE_DAY_PLAN),
    )
    assert result.ok
    assert result.warnings == []
    assert "Goblet Squat" in result.plan_markdown


def test_day_count_mismatch_surfaces_a_warning_not_a_failure():
    result = wp.generate_workout_plan(
        wp.FitnessGoal.BUILD_MUSCLE, wp.ExperienceLevel.BEGINNER, 5,
        wp.Equipment.FULL_GYM, client=FakeGroq(THREE_DAY_PLAN),
    )
    assert result.ok
    assert len(result.warnings) == 1


def test_invalid_input_short_circuits_before_the_api_call():
    fake = FakeGroq(THREE_DAY_PLAN)
    result = wp.generate_workout_plan(
        wp.FitnessGoal.BUILD_MUSCLE, wp.ExperienceLevel.BEGINNER, 0,
        wp.Equipment.FULL_GYM, client=fake,
    )
    assert not result.ok
    assert fake.chat.completions.last_kwargs == {}


def test_empty_response_is_a_friendly_failure():
    result = wp.generate_workout_plan(
        wp.FitnessGoal.LOSE_FAT, wp.ExperienceLevel.ADVANCED, 4,
        wp.Equipment.NONE, client=FakeGroq("   "),
    )
    assert not result.ok
    assert "empty" in result.error_message.lower()


def test_malformed_response_is_a_friendly_failure():
    result = wp.generate_workout_plan(
        wp.FitnessGoal.LOSE_FAT, wp.ExperienceLevel.ADVANCED, 4,
        wp.Equipment.NONE, client=FakeGroq("I'm sorry, I can't help with that."),
    )
    assert not result.ok
    assert "doesn't look like a workout plan" in result.error_message


def test_api_exception_never_escapes():
    result = wp.generate_workout_plan(
        wp.FitnessGoal.GENERAL_FITNESS, wp.ExperienceLevel.BEGINNER, 2,
        wp.Equipment.HOME_DUMBBELLS, client=FakeGroq(error=RuntimeError("boom")),
    )
    assert not result.ok
    assert "boom" in result.error_message


def test_missing_api_key_is_reported(monkeypatch):
    monkeypatch.delenv("GROQ_API_KEY", raising=False)
    result = wp.generate_workout_plan(
        wp.FitnessGoal.BUILD_MUSCLE, wp.ExperienceLevel.BEGINNER, 3,
        wp.Equipment.FULL_GYM,
    )
    assert not result.ok
    assert "API key" in result.error_message


def test_regenerate_raises_temperature_and_adds_a_variation_nudge():
    fake = FakeGroq(THREE_DAY_PLAN)
    wp.generate_workout_plan(
        wp.FitnessGoal.BUILD_MUSCLE, wp.ExperienceLevel.BEGINNER, 3,
        wp.Equipment.FULL_GYM, client=fake, variation_seed=1,
    )
    kwargs = fake.chat.completions.last_kwargs
    assert kwargs["temperature"] == wp.REGENERATE_TEMPERATURE
    assert "VARIATION" in kwargs["messages"][0]["content"]


# --- prompt construction ----------------------------------------------------

def test_prompt_carries_every_structured_input():
    fake = FakeGroq(THREE_DAY_PLAN)
    wp.generate_workout_plan(
        wp.FitnessGoal.LOSE_FAT, wp.ExperienceLevel.INTERMEDIATE, 4,
        wp.Equipment.HOME_DUMBBELLS, "bad knees", client=fake,
    )
    user_message = fake.chat.completions.last_kwargs["messages"][1]["content"]
    for expected in ("Lose fat", "Intermediate", "4", "Home dumbbells", "bad knees"):
        assert expected in user_message


def test_equipment_restrictions_are_spelled_out_in_the_prompt():
    fake = FakeGroq(THREE_DAY_PLAN)
    wp.generate_workout_plan(
        wp.FitnessGoal.GENERAL_FITNESS, wp.ExperienceLevel.BEGINNER, 3,
        wp.Equipment.NONE, client=fake,
    )
    user_message = fake.chat.completions.last_kwargs["messages"][1]["content"]
    assert "may NOT use dumbbells" in user_message


def test_disclaimer_rule_only_appears_when_limitations_are_given():
    with_limits = prompts.build_system_prompt(has_limitations=True)
    without = prompts.build_system_prompt(has_limitations=False)
    assert "Disclaimer" in with_limits
    assert "Disclaimer" not in without


def test_swap_requires_an_exercise_name():
    result = wp.swap_exercise("  ", THREE_DAY_PLAN, wp.Equipment.FULL_GYM,
                              client=FakeGroq("x"))
    assert not result.ok


def test_swap_returns_the_suggestion():
    result = wp.swap_exercise(
        "Goblet Squat", THREE_DAY_PLAN, wp.Equipment.NONE, "bad knees",
        client=FakeGroq("**Swap:** Goblet Squat → Glute Bridge"),
    )
    assert result.ok
    assert "Glute Bridge" in result.plan_markdown
