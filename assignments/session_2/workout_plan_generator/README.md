# 🏋️ Workout Plan Generator

A single-page Streamlit app that turns structured fitness inputs into a weekly
training plan you could actually follow, generated with an LLM via the Groq API.

Built for the **codebasics.io AI Engineering Cohort — Session 2**.

---

## What it does

You tell it your goal, experience, available days, equipment, and any injuries.
It returns a day-by-day plan with specific exercises, sets, reps, rest periods,
warm-ups, cool-downs, and a four-week progression — constrained to the equipment
you actually own and worked around any limitation you reported.

| Input | Type | Values |
| --- | --- | --- |
| Fitness goal | dropdown | Build muscle / Lose fat / General fitness / Improve endurance |
| Experience level | dropdown | Beginner / Intermediate / Advanced |
| Days per week | number input | 1–7 |
| Equipment access | dropdown | No equipment / Home dumbbells / Full gym |
| Injuries / limitations | free text | optional, e.g. "bad knees" |

**Stretch features implemented:** Regenerate for a different variation ·
`st.session_state` persistence across reruns · Download the plan as `.md` ·
Swap a single exercise for a constraint-respecting alternative.

---

## Setup

```bash
# 1. Install dependencies (uv)
uv sync

# 2. Add your Groq API key — get a free one at https://console.groq.com/keys
cp .env.example .env
#   then edit .env and set GROQ_API_KEY=gsk_...

# 3. Run
uv run streamlit run app.py
```

<details>
<summary>Using pip instead of uv</summary>

```bash
python -m venv .venv && source .venv/bin/activate
pip install streamlit groq python-dotenv
streamlit run app.py
```
</details>

No `.env`? You can paste a key straight into the sidebar instead.

---

## Project layout

| File | Responsibility |
| --- | --- |
| [`app.py`](app.py) | Streamlit UI only — collects inputs, renders results. No LLM logic. |
| [`workout_planner.py`](workout_planner.py) | Input types, validation, the Groq call, response parsing, error mapping. |
| [`prompts.py`](prompts.py) | System prompt, user-message builder, constraint rule tables. |
| [`PROMPT_NOTES.md`](PROMPT_NOTES.md) | The prompt iteration log — measured failures and the fixes for each. |
| [`tests/`](tests/) | Unit tests with a faked Groq client. No network required. |
| [`samples/`](samples/) | Three real generated plans, kept as evidence of the constraint handling. |

The core function, per the assignment spec:

```python
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
) -> PlanResult: ...
```

It never raises. Every failure path — invalid input, auth, rate limit, network,
empty or malformed response — comes back as a `PlanResult` with `ok=False` and a
message written for the user rather than a stack trace.

**Model:** `openai/gpt-oss-120b` via Groq (131k context). Set in `MODEL` at the
top of [`workout_planner.py`](workout_planner.py) — change it there if your key
has access to something else. If the model is unavailable, the app says so
instead of failing silently.

---

## Error handling

Failures are caught in three layers, so the app never shows a traceback:

1. **Before the call** — `validate_inputs()` checks days are 1–7, all three
   dropdowns are set, and the limitations text is within length. Problems are
   listed back to the user and no API call is made.
2. **During the call** — each Groq exception maps to specific advice:
   bad key → check your key; rate limit → wait a moment; connection error →
   check your network; unknown model, bad request, and any other status error
   each get their own message.
3. **After the call** — an empty response, or one containing no `Day N` section,
   becomes a friendly retry prompt. If the model returns the wrong *number* of
   days, the plan still renders with a warning above it rather than being thrown
   away.

## Tests

```bash
uv run pytest
```

24 tests covering validation edges (0 days, 8 days, unset dropdowns, overlong
text), response parsing (empty choices, `None` content, malformed output), the
error paths, and that every structured input actually reaches the prompt.

---

*General fitness information only — not medical advice.*
