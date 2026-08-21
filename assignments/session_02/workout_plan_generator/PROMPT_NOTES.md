# Prompt Iteration Log

The assignment's real exercise: *"iterate on your system prompt a few times with
different inputs and see where the model ignores your constraints."*

So I measured it rather than eyeballing it. I ran the same three constraint-heavy
scenarios through a **naive prompt** (the one-sentence concatenation the brief
warns against) and through the **designed prompt** in [`prompts.py`](prompts.py),
then auto-audited both outputs for: number of parseable `Day N` sections,
mentions of equipment the client doesn't own, exercises the stated injury rules
out, and presence of the disclaimer.

Model: `openai/gpt-oss-120b` on Groq, `temperature=0.4`, identical for both arms.

The naive prompt, for reference:

> Create a {days} day workout plan for a {experience} who wants to {goal}.
> They have {equipment}. They have {limitations}.

---

## Measured results

| Scenario | Naive prompt | Designed prompt |
| --- | --- | --- |
| No equipment · 6 days · Beginner · Build muscle | **0** parseable day sections; prescribed **dumbbells, resistance bands, a machine**; 9,907 chars | **6/6** days; **zero** unavailable equipment; 4,799 chars |
| Full gym · 4 days · Advanced · *bad knees* | **0** parseable day sections; prescribed **unqualified squats and leg press** | **4/4** days; knee work substituted with limited-depth variants, each annotated |
| Home dumbbells · 1 day · Intermediate · *no overhead pressing* | **0** parseable day sections; prescribed **overhead press and snatch**; **no disclaimer** | **1/1** day; clean; disclaimer present |

Two extra stress cases, designed prompt only, chosen where drift seemed most likely:

| Stress case | Result |
| --- | --- |
| No equipment · 7 days · **Advanced** · Build muscle — maximum pressure to reach for gear | **7/7** days, **zero** equipment leaks |
| Full gym · *no overhead pressing* — the whole gym is full of the banned category | **4/4** days; "overhead" appears only as annotated substitutions (`Incline Dumbbell Press — Substituted for overhead press`); disclaimer present |

---

## What the model actually got wrong, and the rule that fixed it

**1. It ignored the day count — and the structure entirely.**
The naive prompt never produced a parseable plan. It wrote `**Day 1**` as bold
text inside a summary table and then rambled in prose, so nothing could be
extracted or checked programmatically.
→ Fixed with a literal output skeleton (`## Day 1 - <Focus>`, a fixed table
header) plus a hard rule that rest days are *never* their own `Day` heading.
This is also what made [`count_day_sections()`](workout_planner.py) possible —
the app can now verify the model obeyed and warn the user if it didn't.

**2. It invented equipment the client doesn't have.**
"No equipment" still produced dumbbells and resistance bands, plus hedges like
"if you have access to a pull-up bar."
→ Fixed with `EQUIPMENT_RULES`, which spells out what *is* allowed **and an
explicit NOT list**, injected into the user turn. Naming the allowed set alone
wasn't enough — the banned list is what stopped the leakage. Paired with a
standing rule: never write "if you have access to…", because the client doesn't.

**3. It trained straight through injuries.**
"Bad knees" got unqualified squats and leg press; "no overhead pressing" got
overhead press *and* snatch.
→ Fixed with a substitute-don't-omit rule (a client with bad knees still trains
legs), an explicit requirement that **warm-ups count too** — that's where the
first version still slipped a banned movement in — and a requirement to name the
substitution in the Notes column, which makes violations visible to the user.

**4. It dropped the disclaimer.**
The naive prompt omitted it even when given an injury.
→ Fixed by appending the disclaimer rule *conditionally*, only when limitations
are present, so uninjured users don't get boilerplate. A unit test asserts it's
absent otherwise.

**5. It buried the plan in prose.**
Naive output averaged ~8,100 characters, much of it preamble and caveats.
Designed output averages ~3,900 with the same content in tables.
→ Fixed by the output contract, "no preamble / no closing chat", and a 12-word
cap on the Notes column.

**6. The self-check block does real work.**
The user turn ends with a numbered "BEFORE YOU ANSWER, VERIFY" list that restates
the day count, the equipment string, and the limitation verbatim. Adding it is
what took the harder cases from mostly-right to exactly-right in my runs — it
seems to give the model a chance to catch its own drift before committing.

---

## Honest caveats

- **The two arms differ by many rules at once.** This is a naive-vs-designed
  comparison plus stress tests, not an ablation. I can't claim a specific
  percentage for any single rule — only that the designed prompt holds on cases
  where the naive one reliably fails.
- **My auditor produced false positives.** It flagged `lunge` and `leg press` in
  the designed bad-knees plan. Reading the actual rows, both were
  `(partial depth)` / `(limited depth)` variants with `Knee-friendly
  substitution` in the Notes column — correct behaviour that a substring match
  can't distinguish from a violation. Keyword auditing catches gross failures;
  it doesn't replace reading the output.
- **n=1 per cell.** These are single samples at a fixed temperature, not a
  statistical evaluation. Treat the direction as reliable and the specific
  numbers as illustrative.
- **The day-count check is defence in depth.** Because the model *can* still
  drift, the app treats a count mismatch as a visible warning above a rendered
  plan rather than silently trusting it.
