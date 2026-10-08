"""Run the evaluation pipeline end to end.

    uv run python -m evalguard.evaluation.run                 # full run
    uv run python -m evalguard.evaluation.run --ids D01,A01   # a subset
    uv run python -m evalguard.evaluation.run --rescore reports/runs/<id>.json

Stages, cheapest first:

  1. collect     every case through the guarded pipeline (guardrails + target)
  2. heuristics  deterministic checks; if too many answers are empty or
                 errored, stop here — the system is broken and LLM scoring
                 would only measure the breakage
  3. RAGAS       faithfulness, answer relevancy, context precision and recall
  4. judge       rubric scores with written justification
  5. controls    planted bad responses the evaluators must catch
  6. report      gates, verdict, Markdown + JSON

``--rescore`` reuses the answers from an earlier run and repeats stages 2-6, so
two runs over identical answers show how stable the evaluators are on their own.

Exit code 0 on PASS, 1 on FAIL.
"""

from __future__ import annotations

import argparse
import contextlib
import json
import sys
import time
from dataclasses import asdict
from datetime import UTC, datetime
from pathlib import Path

from evalguard.config import REPORTS_DIR, settings
from evalguard import target
from evalguard.evaluation import heuristics, judge, ragas_eval, report
from evalguard.evaluation.controls import run_controls
from evalguard.evaluation.dataset import EvalCase, Observation, load_cases
from evalguard.observability.tracing import configure

RUNS_DIR = REPORTS_DIR / "runs"


def _log(msg: str) -> None:
    print(f"[{datetime.now():%H:%M:%S}] {msg}", flush=True)


def _trace_context(run_id: str, case_id: str):
    """Tag each case's trace with the run and case, so LangSmith can filter by them."""
    if not configure():
        return contextlib.nullcontext()
    from langsmith import tracing_context  # noqa: PLC0415

    return tracing_context(
        metadata={"eval_run_id": run_id, "case_id": case_id}, tags=["evaluation", run_id]
    )


def collect(cases: list[EvalCase], run_id: str, pace: float) -> list[Observation]:
    from evalguard.gateway.pipeline import guarded_chat  # noqa: PLC0415

    observations = []
    last_start = 0.0
    for i, case in enumerate(cases, start=1):
        wait = pace - (time.perf_counter() - last_start)
        if wait > 0 and i > 1:
            time.sleep(wait)
        last_start = time.perf_counter()
        with _trace_context(run_id, case.id):
            resp = guarded_chat(case.question, case.role)
        obs = Observation.from_response(case.id, resp)
        observations.append(obs)
        state = f"blocked@{obs.blocked_stage}" if obs.blocked else obs.retrieval_type
        _log(f"  {i:>2}/{len(cases)} {case.id:<4} {state:<16} {obs.latency_seconds:5.1f}s  {obs.request_id}")
    return observations


def _previous_run(exclude: str) -> dict | None:
    if not RUNS_DIR.exists():
        return None
    runs = sorted(p for p in RUNS_DIR.glob("*.json") if p.stem != exclude)
    return json.loads(runs[-1].read_text()) if runs else None


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument("--ids", help="comma-separated case IDs to run (default: all)")
    parser.add_argument("--rescore", type=Path, help="reuse the answers in this run JSON instead of calling the target")
    parser.add_argument("--skip-llm", action="store_true", help="heuristics and guardrail stats only")
    parser.add_argument("--pace", type=float, default=settings.eval_pace_seconds,
                        help="minimum seconds between requests to the target (default: %(default)s)")
    args = parser.parse_args(argv)

    run_id = datetime.now(UTC).strftime("%Y%m%dT%H%M%SZ")
    started = time.perf_counter()
    ids = set(args.ids.split(",")) if args.ids else None
    cases = load_cases(ids=ids)
    case_by_id = {c.id: c for c in cases}

    if args.rescore:
        prior = json.loads(args.rescore.read_text())
        observations = [Observation(**o) for o in prior["observations"] if o["case_id"] in case_by_id]
        cases = [c for c in cases if c.id in {o.case_id for o in observations}]
        _log(f"rescoring {len(observations)} answers from {args.rescore}")
    else:
        _log(f"collecting {len(cases)} cases through the guarded pipeline")
        observations = collect(cases, run_id, args.pace)
    obs_by_id = {o.case_id: o for o in observations}

    run: dict = {
        "run_id": run_id,
        "mode": "rescore" if args.rescore else "full",
        "rescored_from": str(args.rescore) if args.rescore else None,
        "pace_seconds": None if args.rescore else args.pace,
        "started_at": datetime.now(UTC).isoformat(),
        "config": {
            "generator_model": target.generator_model(),
            "judge_model": settings.judge_model,
            "ragas_model": settings.ragas_model,
            "openevals_guard_model": settings.openevals_guard_model,
            "input_guard_model": settings.input_guard_model,
            "output_policy_model": settings.output_policy_model,
            "langsmith_project": settings.langsmith_project,
        },
        "cases": [asdict(c) for c in cases],
        "observations": [o.to_dict() for o in observations],
    }

    # --- heuristics first: free, and they decide whether to continue
    _log("heuristics")
    run["heuristics"] = {
        c.id: [asdict(r) for r in heuristics.run_heuristics(c, obs_by_id[c.id])] for c in cases
    }
    broken = [
        cid for cid, rs in run["heuristics"].items()
        if any(r["name"] == "answer_present" and not r["passed"] for r in rs)
    ]
    error_rate = len(broken) / len(cases) if cases else 1.0
    run["fail_fast"] = {
        "error_rate": round(error_rate, 3),
        "triggered": error_rate > settings.fail_fast_error_rate,
        "cases": broken,
    }
    skip_llm = args.skip_llm or run["fail_fast"]["triggered"]
    if run["fail_fast"]["triggered"]:
        _log(f"FAIL FAST: {len(broken)}/{len(cases)} answers empty or errored — skipping LLM stages")

    pairs = [(c, obs_by_id[c.id]) for c in cases]
    if not skip_llm:
        _log(f"RAGAS on {sum(ragas_eval.eligible(c, o) for c, o in pairs)} eligible cases ({settings.ragas_model})")
        ragas_results = ragas_eval.score(pairs)
        run["ragas"] = [asdict(r) for r in ragas_results]
        run["ragas_aggregate"] = ragas_eval.aggregate(ragas_results)

        _log(f"LLM judge on {len(pairs)} cases ({settings.judge_model})")
        run["judge"] = []
        for c, o in pairs:
            result = judge.grade_case(c, o)
            run["judge"].append(asdict(result))
            _log(f"  {c.id:<4} {'pass' if result.passed else 'FAIL'} {result.score:.2f}"
                 + (f"  error: {result.error}" if result.error else ""))

    _log("evaluator controls")
    run["controls"] = [
        {**asdict(c), "passed": c.passed}
        for c in run_controls(case_by_id, obs_by_id, use_llm=not skip_llm)
    ]
    for c in run["controls"]:
        _log(f"  {c['id']} {c['kind']:<22} {'caught' if c['passed'] else 'MISSED'}")

    run["guardrails"] = report.guardrail_stats(run)
    run["gates"] = report.compute_gates(run)
    run["verdict"] = "PASS" if all(g["passed"] for g in run["gates"]) else "FAIL"
    run["duration_seconds"] = round(time.perf_counter() - started, 1)

    RUNS_DIR.mkdir(parents=True, exist_ok=True)
    (RUNS_DIR / f"{run_id}.json").write_text(json.dumps(run, indent=2, default=str))
    markdown = report.render_markdown(run, previous=_previous_run(exclude=run_id))
    (REPORTS_DIR / f"report_{run_id}.md").write_text(markdown)
    (REPORTS_DIR / "latest.md").write_text(markdown)

    _log(f"verdict {run['verdict']} in {run['duration_seconds']}s")
    for g in run["gates"]:
        _log(f"  {'pass' if g['passed'] else 'FAIL'}  {g['name']}: {g['observed']} (needs {g['threshold']})")
    _log(f"report: {REPORTS_DIR / 'latest.md'}")
    return 0 if run["verdict"] == "PASS" else 1


if __name__ == "__main__":
    sys.exit(main())
