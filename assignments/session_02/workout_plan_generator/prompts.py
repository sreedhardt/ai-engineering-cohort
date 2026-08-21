"""Prompt construction for the workout plan generator.

Kept in its own module so the prompt can be iterated on (and diffed) without
touching the API-calling or UI code. See PROMPT_NOTES.md for the iteration log.
"""

from __future__ import annotations

EQUIPMENT_RULES: dict[str, str] = {
    "No equipment": (
        "Bodyweight only. You may use a wall, a chair, a step, a towel, a backpack "
        "loaded with books, or the floor. You may NOT use dumbbells, barbells, "
        "kettlebells, resistance bands, cable machines, pull-up bars, benches, or "
        "any gym machine."
    ),
    "Home dumbbells": (
        "One adjustable pair (or a small set) of dumbbells, plus bodyweight, a "
        "floor, and a chair or bench-height surface. You may NOT use barbells, "
        "cable machines, leg press, smith machines, or any other gym machine."
    ),
    "Full gym": (
        "Barbells, dumbbells, kettlebells, cable machines, resistance machines, "
        "benches, racks, pull-up bars, and cardio machines are all available."
    ),
}

EXPERIENCE_RULES: dict[str, str] = {
    "Beginner": (
        "Use 3-5 exercises per session and compound movements with simple, stable "
        "technique. Prescribe effort as plain language ('stop 3-4 reps before "
        "failure'). Include one short form cue in the Notes column for every "
        "exercise. Avoid barbell snatch, clean, muscle-up, or plyometric depth work."
    ),
    "Intermediate": (
        "Use 4-6 exercises per session. You may use RPE (e.g. 'RPE 7-8'), supersets, "
        "and a mix of compound and isolation work. Form cues are optional."
    ),
    "Advanced": (
        "Use 5-7 exercises per session. You may prescribe percentage-based loading, "
        "tempo notation (e.g. 3-1-1), intensity techniques such as drop sets or "
        "cluster sets, and a deliberate weekly intensity/volume distribution."
    ),
}

GOAL_RULES: dict[str, str] = {
    "Build muscle": (
        "Prioritise hypertrophy: 6-12 rep range on most work, 10-20 hard sets per "
        "muscle group per week, 60-120s rest. Progression is load or reps."
    ),
    "Lose fat": (
        "Prioritise retaining muscle while raising energy expenditure: keep "
        "resistance training as the base (6-15 reps), add conditioning finishers or "
        "circuits, shorter rest (45-75s). Do NOT prescribe calories, macros, diets, "
        "or a target rate of weight loss - that is outside your scope."
    ),
    "General fitness": (
        "Balance strength, conditioning, and mobility across the week. 8-15 rep "
        "range, full-body coverage, at least one mobility or core block per session."
    ),
    "Improve endurance": (
        "Prioritise aerobic and muscular endurance: intervals and steady-state work "
        "scaled to the equipment available, higher-rep resistance work (12-20), "
        "short rest (30-60s). Vary intensity across the week rather than making "
        "every session hard."
    ),
}

SYSTEM_PROMPT = """\
You are an experienced strength and conditioning coach writing a weekly training \
plan for one specific client. You write plans people actually follow: specific \
exercises, specific numbers, no filler.

# HARD CONSTRAINTS
These are not preferences. A plan that breaks any of them is a failed response.

1. DAYS: Produce exactly the number of training days requested - no more, no \
fewer. If the client asked for 3 days, output Day 1, Day 2, Day 3 and stop. Do not \
add "optional" or "bonus" days. Rest days are described in one line in the weekly \
overview, never as their own Day heading.
2. EQUIPMENT: Use only equipment from the client's allowed list, reproduced below. \
If an exercise you want needs unavailable equipment, substitute a different \
exercise. Never write "if you have access to..." - the client does not.
3. LIMITATIONS: If the client lists an injury or limitation, work around it by \
SUBSTITUTING exercises, not by deleting a body part from the plan. A client with \
bad knees still trains legs, using hip-hinge and limited-range options. Never \
prescribe an exercise the limitation rules out, including in warm-ups.
4. SCOPE: You are a coach, not a clinician. Do not diagnose, name a condition, \
suggest it will heal, or give rehab protocols, medication, or nutrition advice. If \
a limitation sounds like it needs professional care, say so in one sentence and \
move on.

# OUTPUT FORMAT
Return GitHub-flavoured Markdown, in exactly this order and nothing else. No \
preamble, no "Here is your plan", no closing chat.

## Your Plan
One or two sentences naming the split and why it fits this client's goal, days, \
and equipment.

## Weekly Overview
A markdown table with columns: Day | Focus | Est. Time. One row per training day, \
plus one final row summarising rest days.

## Day 1 - <Focus>
A markdown table with columns: Exercise | Sets | Reps | Rest | Notes.
Then a line `**Warm-up:**` with 2-3 specific movements, and a line \
`**Cool-down:**` with 2-3 specific stretches or easy work.
Repeat this whole section for each remaining day.

## Progressing Over 4 Weeks
3-5 bullets on how to add load, reps, or intensity week to week, and when to \
deload or repeat a week.

# STYLE
- Every exercise needs concrete numbers. "3 x 8-10" not "a few sets".
- Name exercises specifically: "Goblet Squat", not "squat variation".
- Keep the Notes column under 12 words.
- Do not invent details about the client that were not provided.\
"""

_DISCLAIMER_RULE = """\

# ADDITIONAL REQUIREMENT FOR THIS CLIENT
This client reported a limitation. After the final section, add a horizontal rule \
and then one short italic paragraph beginning "*Disclaimer:*" noting that this is \
general fitness information, not medical advice, and that they should clear the \
plan with a qualified healthcare professional before starting. Do not expand it \
beyond two sentences.\
"""

VARIATION_NUDGES: tuple[str, ...] = (
    "",
    "This client has seen a previous version of this plan. Build a genuinely "
    "different one: change the split structure and choose different primary "
    "exercises, while respecting every constraint above.",
    "This client has seen two previous versions. Produce a third distinct plan - "
    "vary the training split, the exercise selection, and the set/rep scheme, "
    "while respecting every constraint above.",
)


def build_system_prompt(has_limitations: bool, variation_seed: int = 0) -> str:
    """Assemble the system prompt, adding conditional blocks as needed."""
    prompt = SYSTEM_PROMPT
    if has_limitations:
        prompt += _DISCLAIMER_RULE
    nudge = VARIATION_NUDGES[variation_seed % len(VARIATION_NUDGES)]
    if nudge:
        prompt += f"\n\n# VARIATION\n{nudge}"
    return prompt


def build_user_message(
    goal: str,
    experience: str,
    days_per_week: int,
    equipment: str,
    limitations: str | None = None,
) -> str:
    """Build the user turn as labelled fields plus an explicit self-check.

    Structured `key: value` lines beat a prose sentence here - the model treats
    them as a spec rather than as context it can paraphrase away. The closing
    checklist is what actually stops constraint drift; see PROMPT_NOTES.md.
    """
    limitation_text = (limitations or "").strip()

    lines = [
        "# CLIENT BRIEF",
        f"- Primary goal: {goal}",
        f"- Experience level: {experience}",
        f"- Training days per week: {days_per_week}",
        f"- Equipment access: {equipment}",
        f"- Injuries / limitations: {limitation_text or 'None reported'}",
        "",
        "# APPLY THESE RULES",
        f"- Goal ({goal}): {GOAL_RULES.get(goal, 'Train sensibly for this goal.')}",
        f"- Experience ({experience}): "
        f"{EXPERIENCE_RULES.get(experience, 'Scale complexity sensibly.')}",
        f"- Equipment ({equipment}): "
        f"{EQUIPMENT_RULES.get(equipment, 'Use only what the client listed.')}",
    ]

    if limitation_text:
        lines.append(
            f'- Limitation ("{limitation_text}"): Every exercise in this plan, '
            "warm-ups included, must be safe to perform with this limitation. "
            "Substitute rather than omit. Where a substitution is the direct "
            "result of this limitation, say so in the Notes column."
        )

    lines += [
        "",
        "# BEFORE YOU ANSWER, VERIFY",
        f"1. Does the plan contain exactly {days_per_week} 'Day N' section(s)?",
        f"2. Does every single exercise work with '{equipment}' and nothing else?",
    ]
    if limitation_text:
        lines.append(
            f"3. Is every exercise, including warm-ups, safe for \"{limitation_text}\"?"
        )
        lines.append("4. Is the disclaimer paragraph present at the end?")
        lines.append("5. Does every exercise have sets, reps, and rest?")
    else:
        lines.append("3. Does every exercise have sets, reps, and rest?")
    lines.append(
        "Fix any answer that is 'no' before responding. Output only the plan."
    )

    return "\n".join(lines)


def build_swap_message(
    exercise: str,
    plan_markdown: str,
    equipment: str,
    limitations: str | None = None,
) -> str:
    """Build the user turn for the 'swap one exercise' feature."""
    limitation_text = (limitations or "").strip()
    return "\n".join(
        [
            f'The client wants to replace one exercise: "{exercise}".',
            "",
            "Suggest exactly ONE replacement that trains the same movement pattern "
            "and muscles, at a comparable difficulty.",
            "",
            "# CONSTRAINTS",
            f"- Equipment ({equipment}): "
            f"{EQUIPMENT_RULES.get(equipment, 'Use only what the client listed.')}",
            f"- Limitations: {limitation_text or 'None reported'}",
            "- The replacement must not already appear in the plan below.",
            "",
            "# RESPOND IN EXACTLY THIS FORMAT",
            "**Swap:** <original> → <replacement>",
            "",
            "**Sets/Reps:** <e.g. 3 x 8-10, 90s rest>",
            "",
            "**Why:** <one sentence>",
            "",
            "No other text.",
            "",
            "# THE CLIENT'S CURRENT PLAN",
            plan_markdown,
        ]
    )
