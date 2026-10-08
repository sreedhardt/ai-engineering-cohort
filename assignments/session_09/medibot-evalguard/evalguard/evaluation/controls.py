"""Evaluator controls: planted bad responses the evaluators must catch.

A judge that agrees with every confident answer, or a heuristic that never
fires, produces a green report about a broken system. Each control is a known
bad response scored by the same code as the real run, with a stated
expectation of who should catch it. If an evaluator misses its control, the
report fails — its other scores cannot be trusted.

Controls never touch the target; they are fed straight to the evaluators.
"""

from __future__ import annotations

from dataclasses import dataclass, field

from evalguard.evaluation import heuristics, judge, ragas_eval
from evalguard.evaluation.dataset import EvalCase, Observation, load_controls
from evalguard.guardrails.output_guard import check_output


@dataclass
class ControlResult:
    id: str
    kind: str
    description: str
    based_on: str
    answer: str
    expectations: list[dict] = field(default_factory=list)  # {evaluator, expected, observed, met}

    @property
    def passed(self) -> bool:
        return all(e["met"] for e in self.expectations)


def _planted_observation(control: dict, case: EvalCase, real: Observation | None) -> Observation:
    def resolve(key: str) -> list:
        value = control[key]
        if value == "from_case":
            return list(getattr(real, key)) if real else []
        return list(value)

    sources = resolve("sources")
    return Observation(
        case_id=control["id"], request_id="control", role=case.role, question=case.question,
        answer=control["answer"], blocked=False, blocked_stage=None,
        retrieval_type=control["route"], sources=sources,
        target_answer=control["answer"], target_sources=sources,
        contexts=resolve("contexts"), latency_seconds=0.0, usage={},
        input_reason="", output_reason="",
    )


def run_controls(
    cases: dict[str, EvalCase],
    observations: dict[str, Observation],
    *,
    use_llm: bool = True,
) -> list[ControlResult]:
    results = []
    for control in load_controls():
        case = cases.get(control["based_on"])
        if case is None:
            continue
        obs = _planted_observation(control, case, observations.get(case.id))
        expect = control["expect"]
        result = ControlResult(
            id=control["id"], kind=control["kind"], description=control["description"],
            based_on=case.id, answer=control["answer"],
        )

        fired = {r.name for r in heuristics.run_heuristics(case, obs) if not r.passed}
        for name in expect.get("heuristics_fail", []):
            result.expectations.append({
                "evaluator": f"heuristic:{name}", "expected": "fail",
                "observed": "fail" if name in fired else "pass", "met": name in fired,
            })

        if "guardrail" in expect:
            decision = check_output(case.role, case.question, obs.answer, obs.sources, obs.contexts)
            observed = "block" if decision.blocked else "allow"
            result.expectations.append({
                "evaluator": "output_guardrail", "expected": expect["guardrail"],
                "observed": f"{observed} ({decision.internal_reason[:120]})",
                "met": observed == expect["guardrail"],
            })

        if use_llm and "judge" in expect:
            graded = judge.grade_case(case, obs)
            observed = "error" if graded.error else ("pass" if graded.passed else "fail")
            result.expectations.append({
                "evaluator": "llm_judge", "expected": expect["judge"],
                "observed": f"{observed} (score {graded.score:.2f})",
                "met": observed == expect["judge"],
                "justification": (graded.grade or {}).get("justification", graded.error),
            })

        if use_llm and "ragas_faithfulness_below" in expect and obs.contexts:
            limit = expect["ragas_faithfulness_below"]
            scored = ragas_eval.score_single(case.question, obs.answer, obs.contexts, case.reference)
            value = scored.scores.get("faithfulness")
            result.expectations.append({
                "evaluator": "ragas:faithfulness", "expected": f"< {limit}",
                "observed": "error" if value is None else f"{value:.2f}",
                "met": value is not None and value < limit,
            })

        results.append(result)
    return results
