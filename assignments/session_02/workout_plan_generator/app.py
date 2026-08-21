"""Streamlit UI for the workout plan generator.

All LLM logic lives in workout_planner.py - this file only collects inputs,
calls it, and renders the result.
"""

from __future__ import annotations

from typing import Any

import streamlit as st
from dotenv import load_dotenv

from workout_planner import (
    Equipment,
    ExperienceLevel,
    FitnessGoal,
    MAX_DAYS,
    MAX_LIMITATION_CHARS,
    PlanResult,
    generate_workout_plan,
    resolve_api_key,
    swap_exercise,
    validate_inputs,
)

load_dotenv()

st.set_page_config(page_title="Workout Plan Generator", page_icon="🏋️", layout="centered")

# --- session state ----------------------------------------------------------
st.session_state.setdefault("plan", None)          # PlanResult | None
st.session_state.setdefault("plan_inputs", None)   # dict | None
st.session_state.setdefault("variation", 0)        # int
st.session_state.setdefault("swap", None)          # PlanResult | None


def run_generation(inputs: dict[str, Any], variation_seed: int) -> None:
    """Generate a plan and store it in session state."""
    with st.spinner("Building your plan..."):
        result = generate_workout_plan(
            goal=inputs["goal"],
            experience=inputs["experience"],
            days_per_week=inputs["days_per_week"],
            equipment=inputs["equipment"],
            limitations=inputs["limitations"],
            variation_seed=variation_seed,
            api_key=st.session_state.get("api_key_input"),
        )
    st.session_state.plan = result
    st.session_state.plan_inputs = inputs
    st.session_state.variation = variation_seed
    st.session_state.swap = None


# --- sidebar ----------------------------------------------------------------
with st.sidebar:
    st.header("Setup")
    st.text_input(
        "Groq API key",
        type="password",
        key="api_key_input",
        help="Optional. Leave blank to use GROQ_API_KEY from your .env file.",
    )
    if resolve_api_key(st.session_state.get("api_key_input")):
        st.success("API key detected.")
    else:
        st.warning("No API key found. Add one here or in a `.env` file.")
    st.caption("Get a free key at console.groq.com")

# --- inputs -----------------------------------------------------------------
st.title("🏋️ Workout Plan Generator")
st.write(
    "Tell me about your training situation and I'll write you a weekly plan you "
    "can actually follow."
)

with st.form("plan_form"):
    col_left, col_right = st.columns(2)
    with col_left:
        goal = st.selectbox(
            "Fitness goal",
            options=list(FitnessGoal),
            index=None,
            placeholder="Choose a goal...",
        )
        days_per_week = st.number_input(
            "Training days per week",
            min_value=0,
            max_value=MAX_DAYS,
            value=3,
            step=1,
            help="How many days you can realistically train.",
        )
    with col_right:
        experience = st.selectbox(
            "Experience level",
            options=list(ExperienceLevel),
            index=None,
            placeholder="Choose a level...",
        )
        equipment = st.selectbox(
            "Equipment access",
            options=list(Equipment),
            index=None,
            placeholder="Choose your equipment...",
        )

    limitations = st.text_area(
        "Injuries or limitations (optional)",
        placeholder="e.g. bad knees, no overhead pressing, recovering shoulder",
        max_chars=MAX_LIMITATION_CHARS,
        height=80,
    )

    submitted = st.form_submit_button("Generate Plan", type="primary", width="stretch")

if submitted:
    problems = validate_inputs(
        goal=str(goal) if goal else "",
        experience=str(experience) if experience else "",
        days_per_week=int(days_per_week),
        equipment=str(equipment) if equipment else "",
        limitations=limitations,
    )
    if problems:
        st.warning("Before I can build a plan:\n\n" + "\n".join(f"- {p}" for p in problems))
    else:
        run_generation(
            {
                "goal": goal,
                "experience": experience,
                "days_per_week": int(days_per_week),
                "equipment": equipment,
                "limitations": limitations,
            },
            variation_seed=0,
        )

# --- results ----------------------------------------------------------------
plan: PlanResult | None = st.session_state.plan

if plan is not None and not plan.ok:
    st.error(plan.error_message)

if plan is not None and plan.ok:
    saved = st.session_state.plan_inputs or {}
    st.divider()

    for warning in plan.warnings:
        st.warning(warning)

    st.markdown(plan.plan_markdown)
    st.divider()

    action_left, action_right = st.columns(2)
    with action_left:
        if st.button("🔄 Regenerate", width="stretch"):
            run_generation(saved, variation_seed=st.session_state.variation + 1)
            st.rerun()
    with action_right:
        st.download_button(
            "⬇️ Download as .md",
            data=plan.plan_markdown,
            file_name="workout_plan.md",
            mime="text/markdown",
            width="stretch",
        )

    with st.expander("Swap an exercise"):
        st.caption(
            "Not feeling one of these? Name it and I'll suggest a replacement that "
            "still fits your equipment and limitations."
        )
        swap_col, button_col = st.columns([3, 1], vertical_alignment="bottom")
        with swap_col:
            exercise = st.text_input(
                "Exercise to replace",
                placeholder="e.g. Goblet Squat",
                label_visibility="collapsed",
            )
        with button_col:
            swap_clicked = st.button("Swap", width="stretch")

        if swap_clicked:
            with st.spinner("Finding an alternative..."):
                st.session_state.swap = swap_exercise(
                    exercise=exercise,
                    plan_markdown=plan.plan_markdown,
                    equipment=saved.get("equipment", Equipment.FULL_GYM),
                    limitations=saved.get("limitations"),
                    api_key=st.session_state.get("api_key_input"),
                )

        swap_result: PlanResult | None = st.session_state.swap
        if swap_result is not None:
            if swap_result.ok:
                st.success(swap_result.plan_markdown)
            else:
                st.error(swap_result.error_message)

st.divider()
st.caption(
    "General fitness information only - not medical advice. "
    "Built for the codebasics.io AI Engineering Cohort, Session 2."
)
