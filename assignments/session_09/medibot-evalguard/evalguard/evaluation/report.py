"""Turn one evaluation run into gates, a verdict, and a Markdown report.

A *gate* is one threshold with an observed value. The verdict is PASS only if
every gate passes, and the report names each one that did not. Thresholds come
from ``config.Settings`` and were fixed before the first full run.
"""

from __future__ import annotations

import statistics
from collections import Counter

from evalguard.config import settings
from evalguard.evaluation.ragas_eval import METRICS
from evalguard.observability.events import read_events

# --- guardrail statistics ---------------------------------------------------


def guardrail_stats(run: dict) -> dict:
    """Block/allow counts for this run, read back from the structured event log."""
    request_ids = {o["request_id"] for o in run["observations"]}
    events = [e for e in read_events("guardrail") if e.get("request_id") in request_ids]

    by_stage: dict[str, Counter] = {"input": Counter(), "output": Counter()}
    triggered: Counter = Counter()
    failed_closed = 0
    for e in events:
        by_stage.setdefault(e["stage"], Counter())[e["decision"]] += 1
        failed_closed += bool(e.get("failed_closed"))
        for check in e.get("checks", []):
            if check.get("verdict") == "block":
                triggered[f"{e['stage']}:{check['name']}"] += 1

    cases = {c["id"]: c for c in run["cases"]}
    should_answer = [o for o in run["observations"] if cases[o["case_id"]]["expected_behavior"] == "answer"]
    false_blocks = [o["case_id"] for o in should_answer if o["blocked"]]
    should_stop = [o for o in run["observations"] if cases[o["case_id"]]["expected_behavior"] != "answer"]
    stopped = [o["case_id"] for o in should_stop if o["blocked"] or o["retrieval_type"] == "rbac_denied"]

    return {
        "events_found": len(events),
        "input": dict(by_stage["input"]),
        "output": dict(by_stage["output"]),
        "triggered": dict(triggered.most_common()),
        "failed_closed": failed_closed,
        "false_blocks": false_blocks,
        "false_block_rate": round(len(false_blocks) / len(should_answer), 4) if should_answer else 0.0,
        "unsafe_stopped": len(stopped),
        "unsafe_total": len(should_stop),
    }


# --- gates --------------------------------------------------------------------


def _gate(name: str, observed, threshold: str, passed: bool, detail: str = "") -> dict:
    return {"name": name, "observed": observed, "threshold": threshold, "passed": bool(passed), "detail": detail}


def compute_gates(run: dict) -> list[dict]:
    gates = []

    if run.get("fail_fast", {}).get("triggered"):
        gates.append(_gate(
            "System answered (fail-fast)", run["fail_fast"]["error_rate"],
            f"<= {settings.fail_fast_error_rate}", False,
            "too many empty or errored answers; LLM stages skipped",
        ))

    all_h = [r for rs in run["heuristics"].values() for r in rs]
    crit_fail = [f"{cid}:{r['name']}" for cid, rs in run["heuristics"].items() for r in rs if r["critical"] and not r["passed"]]
    gates.append(_gate(
        "Heuristics — critical safety checks", f"{len(crit_fail)} failure(s)", "0 failures",
        not crit_fail, ", ".join(crit_fail),
    ))
    rate = sum(r["passed"] for r in all_h) / len(all_h) if all_h else 0.0
    gates.append(_gate(
        "Heuristics — overall pass rate", round(rate, 3), f">= {settings.min_heuristic_pass_rate}",
        rate >= settings.min_heuristic_pass_rate,
    ))

    g = run["guardrails"]
    gates.append(_gate(
        "Guardrails — false-block rate on answerable questions", g["false_block_rate"],
        f"<= {settings.max_false_block_rate}", g["false_block_rate"] <= settings.max_false_block_rate,
        ", ".join(g["false_blocks"]),
    ))

    if run.get("ragas_aggregate") is not None:
        mins = {
            "faithfulness": settings.min_faithfulness,
            "answer_relevancy": settings.min_answer_relevancy,
            "context_precision": settings.min_context_precision,
            "context_recall": settings.min_context_recall,
        }
        for name in METRICS:
            agg = run["ragas_aggregate"][name]
            mean = agg["mean"]
            gates.append(_gate(
                f"RAGAS — {name}", mean, f">= {mins[name]}",
                mean is not None and mean >= mins[name],
                f"{agg['n']} scored, {agg['errors']} error(s)",
            ))

    if run.get("judge"):
        judged = run["judge"]
        mean = statistics.fmean(j["score"] for j in judged)
        pass_rate = sum(j["passed"] for j in judged) / len(judged)
        errors = sum(1 for j in judged if j["error"])
        gates.append(_gate("LLM judge — mean rubric score", round(mean, 3), f">= {settings.min_judge_score}",
                           mean >= settings.min_judge_score, f"{errors} judge error(s), counted as fails"))
        gates.append(_gate("LLM judge — pass rate", round(pass_rate, 3), f">= {settings.min_judge_pass_rate}",
                           pass_rate >= settings.min_judge_pass_rate,
                           ", ".join(j["case_id"] for j in judged if not j["passed"])))

    if run.get("controls"):
        missed = [c["id"] for c in run["controls"] if not all(e["met"] for e in c["expectations"])]
        gates.append(_gate(
            "Evaluator controls caught", f"{len(run['controls']) - len(missed)}/{len(run['controls'])}",
            "all", not missed, ", ".join(missed),
        ))
    return gates


# --- rendering ------------------------------------------------------------


def _cell(value, limit: int = 0) -> str:
    text = "" if value is None else str(value)
    text = text.replace("|", "\\|").replace("\n", " ").strip()
    if limit and len(text) > limit:
        text = text[: limit - 1] + "…"
    return text


def _pct(values: list[float], q: float) -> float:
    if not values:
        return 0.0
    ordered = sorted(values)
    return ordered[min(len(ordered) - 1, int(round(q * (len(ordered) - 1))))]


def _mark(ok: bool) -> str:
    return "✅" if ok else "❌"


def render_markdown(run: dict, previous: dict | None = None) -> str:
    cases = {c["id"]: c for c in run["cases"]}
    obs = {o["case_id"]: o for o in run["observations"]}
    judge_by = {j["case_id"]: j for j in run.get("judge") or []}
    ragas_by = {r["case_id"]: r for r in run.get("ragas") or []}
    gates = run["gates"]
    failed = [g for g in gates if not g["passed"]]
    L: list[str] = []
    add = L.append

    add(f"# MediBot evaluation report — `{run['run_id']}`\n")
    verdict = "PASS" if not failed else "FAIL"
    add(f"## Verdict: **{verdict}**\n")
    if failed:
        add("Failing gates:\n")
        for g in failed:
            add(f"- **{g['name']}** — observed `{g['observed']}`, required `{g['threshold']}`"
                + (f" ({_cell(g['detail'], 200)})" if g["detail"] else ""))
        add("")
    else:
        add("Every gate below met its threshold.\n")

    cfg = run["config"]
    add(f"Mode: `{run['mode']}`"
        + (f" (answers reused from `{run['rescored_from']}`)" if run.get("rescored_from") else "")
        + f" · {len(run['cases'])} labelled cases · {len(run.get('controls') or [])} evaluator controls  ")
    add(f"Target generator `{cfg['generator_model']}` · judge `{cfg['judge_model']}` · "
        f"RAGAS evaluator `{cfg['ragas_model']}` · OpenEvals guardrails `{cfg['openevals_guard_model']}`\n")

    add("## Gates\n")
    add("| Gate | Observed | Threshold | Result | Detail |")
    add("|---|---|---|---|---|")
    for g in gates:
        add(f"| {g['name']} | {_cell(g['observed'])} | {_cell(g['threshold'])} | {_mark(g['passed'])} | {_cell(g['detail'], 120)} |")
    add("")

    # --- guardrails
    gs = run["guardrails"]
    add("## Guardrails\n")
    add("Counts are read back from `logs/events.jsonl` for this run's request IDs, "
        f"not from in-memory results ({gs['events_found']} guardrail events found).\n")
    add("| Layer | Allowed | Blocked |")
    add("|---|---|---|")
    for stage in ("input", "output"):
        add(f"| {stage} | {gs[stage].get('allow', 0)} | {gs[stage].get('block', 0)} |")
    add("")
    add(f"- Unsafe requests stopped (blocked or refused): **{gs['unsafe_stopped']}/{gs['unsafe_total']}**")
    add(f"- Answerable questions wrongly blocked: **{len(gs['false_blocks'])}** {gs['false_blocks'] or ''}")
    add(f"- Fail-closed decisions: **{gs['failed_closed']}**")
    if gs["triggered"]:
        add("- Checks that fired: " + ", ".join(f"`{k}` ×{v}" for k, v in gs["triggered"].items()))
    add("")

    example = next((o for o in run["observations"]
                    if o["blocked"] and cases[o["case_id"]]["category"] == "adversarial"), None)
    if example:
        reason = example["input_reason"] if example["blocked_stage"] == "input" else example["output_reason"]
        add("### Example: guardrail correctly blocking an unsafe request\n")
        add(f"- Case **{example['case_id']}** (role `{example['role']}`): _{_cell(example['question'])}_")
        add(f"- Blocked at the **{example['blocked_stage']}** guardrail. Internal reason (logged, never shown): `{_cell(reason, 300)}`")
        add(f"- What the user saw: \"{_cell(example['answer'])}\"")
        add(f"- Reconstruct it: `jq 'select(.request_id==\"{example['request_id']}\")' logs/events.jsonl`\n")

    # --- heuristics
    add("## Heuristic evals (deterministic, no LLM)\n")
    from evalguard.evaluation.heuristics import HEURISTICS  # noqa: PLC0415

    add("| Check | Critical | Pass | Fail | N/A | What it verifies |")
    add("|---|---|---|---|---|---|")
    for h in HEURISTICS:
        rs = [r for rs in run["heuristics"].values() for r in rs if r["name"] == h.name]
        p = sum(r["passed"] for r in rs)
        add(f"| `{h.name}` | {'yes' if h.critical else ''} | {p} | {len(rs) - p} | {len(run['cases']) - len(rs)} | {h.description} |")
    add("")
    fails = [(cid, r) for cid, rs in run["heuristics"].items() for r in rs if not r["passed"]]
    if fails:
        add("Failures on the real run:\n")
        for cid, r in fails:
            add(f"- **{cid}** `{r['name']}` — {_cell(r['detail'], 200)}")
        add("")

    # --- RAGAS
    if run.get("ragas_aggregate") is not None:
        add("## RAGAS\n")
        add("Scored on document-RAG answers to answerable questions "
            f"({len(run['ragas'])} cases). SQL answers and refusals have no passages to score.\n")
        add("| Metric | Mean | Threshold | Scored | Errors |")
        add("|---|---|---|---|---|")
        for g in gates:
            if g["name"].startswith("RAGAS — "):
                m = g["name"].removeprefix("RAGAS — ")
                agg = run["ragas_aggregate"][m]
                add(f"| {m} | {_cell(agg['mean'])} | {g['threshold']} | {agg['n']} | {agg['errors']} |")
        add("")
        add("| Case | " + " | ".join(METRICS) + " |")
        add("|---|" + "---|" * len(METRICS))
        for cid, r in ragas_by.items():
            add(f"| {cid} | " + " | ".join(_cell(r['scores'].get(m)) or "err" for m in METRICS) + " |")
        add("")
        errs = [(cid, m, e) for cid, r in ragas_by.items() for m, e in r["errors"].items()]
        if errs:
            add("RAGAS errors (excluded from means, not scored as zero):\n")
            for cid, m, e in errs:
                add(f"- {cid} `{m}`: {_cell(e, 160)}")
            add("")

    # --- judge
    if run.get("judge"):
        add("## LLM-as-a-judge\n")
        add(f"Judge `{cfg['judge_model']}`, a different model family from the generator "
            f"`{cfg['generator_model']}`. Each criterion is 1–5 or n/a; the case score is the mean of "
            "applicable criteria rescaled to 0–1.\n")
        add("| Case | Verdict | Score | Acc | Comp | Refusal | Cite | Justification |")
        add("|---|---|---|---|---|---|---|---|")
        for cid, j in judge_by.items():
            gr = j["grade"] or {}
            just = gr.get("justification") or j["error"]
            add(f"| {cid} | {'pass' if j['passed'] else '**fail**'} | {j['score']:.2f} | "
                f"{_cell(gr.get('accuracy')) or '–'} | {_cell(gr.get('completeness')) or '–'} | "
                f"{_cell(gr.get('refusal_behavior')) or '–'} | {_cell(gr.get('citation_correctness')) or '–'} | "
                f"{_cell(just, 260)} |")
        add("")

    # --- controls
    if run.get("controls"):
        add("## Evaluator controls\n")
        add("Planted bad responses, fed straight to the evaluators. Each must be caught by the "
            "evaluators listed, or the evaluators themselves are not trustworthy.\n")
        add("| Control | Kind | Evaluator | Expected | Observed | Caught |")
        add("|---|---|---|---|---|---|")
        for c in run["controls"]:
            for e in c["expectations"]:
                add(f"| {c['id']} | {c['kind']} | `{e['evaluator']}` | {_cell(e['expected'])} | "
                    f"{_cell(e['observed'], 140)} | {_mark(e['met'])} |")
        add("")
        h_example = next(((c, e) for c in run["controls"] for e in c["expectations"]
                          if e["evaluator"].startswith("heuristic:") and e["met"]), None)
        if h_example:
            c, e = h_example
            add("### Example: heuristic check correctly failing a bad response\n")
            add(f"- Control **{c['id']}** ({c['description']})")
            add(f"- Response: \"{_cell(c['answer']) or '(empty)'}\"")
            add(f"- `{e['evaluator'].removeprefix('heuristic:')}` → **fail**, as intended.\n")
        wrong = next((c for c in run["controls"] if c["kind"] == "wrong_but_confident"), None)
        if wrong:
            add("### Example: a wrong-but-confident answer\n")
            add(f"- Response: \"{_cell(wrong['answer'])}\"")
            for e in wrong["expectations"]:
                line = f"- `{e['evaluator']}` → {_cell(e['observed'], 160)} {_mark(e['met'])}"
                if e.get("justification"):
                    line += f" — _{_cell(e['justification'], 300)}_"
                add(line)
            add("")

    # --- per-case
    add("## Per-case summary\n")
    add("| Case | Category | Role | Route | Blocked | Latency (s) | Tokens | Heuristics | Judge | Request ID |")
    add("|---|---|---|---|---|---|---|---|---|---|")
    for cid, c in cases.items():
        o = obs[cid]
        hs = run["heuristics"].get(cid, [])
        j = judge_by.get(cid)
        add(f"| {cid} | {c['category']} | {c['role']} | {o['retrieval_type']} | "
            f"{o['blocked_stage'] or ''} | {o['latency_seconds']:.1f} | {o['usage'].get('total_tokens', 0)} | "
            f"{sum(r['passed'] for r in hs)}/{len(hs)} | {('pass' if j['passed'] else 'fail') if j else '–'} | "
            f"`{o['request_id']}` |")
    add("")

    lat = [o["latency_seconds"] for o in run["observations"]]
    by_model: Counter = Counter()
    for o in run["observations"]:
        by_model.update(o["usage"].get("by_model", {}))
    add("## Latency and tokens (guarded request path)\n")
    add(f"- Latency p50 **{_pct(lat, 0.5):.1f}s**, p95 **{_pct(lat, 0.95):.1f}s**, max {max(lat or [0]):.1f}s "
        f"(budget {settings.latency_budget_seconds:.0f}s)"
        + (f"; requests paced {run['pace_seconds']:.0f}s apart to stay under the policy model's "
           "3 requests/minute free-tier limit" if run.get("pace_seconds") else ""))
    add(f"- Tokens (target + guardrail models): **{sum(by_model.values())}** total — "
        + ", ".join(f"`{m}` {n}" for m, n in by_model.items()))
    add("- Per-request figures are in `logs/events.jsonl` (`event == \"response\"`) and on each LangSmith trace's metadata.\n")

    if previous:
        add("## Repeatability\n")
        add(f"Compared with run `{previous['run_id']}` ({previous['mode']}). "
            "Re-scoring the same answers isolates evaluator variance; a full run also includes the "
            "target's own sampling variance.\n")
        add("| Signal | Previous | This run | Δ |")
        add("|---|---|---|---|")
        for m in METRICS:
            a = (previous.get("ragas_aggregate") or {}).get(m, {}).get("mean")
            b = (run.get("ragas_aggregate") or {}).get(m, {}).get("mean")
            d = f"{b - a:+.3f}" if a is not None and b is not None else "–"
            add(f"| RAGAS {m} | {_cell(a)} | {_cell(b)} | {d} |")
        pj = {j["case_id"]: j for j in previous.get("judge") or []}
        if pj and judge_by:
            common = [cid for cid in judge_by if cid in pj]
            agree = sum(judge_by[c]["passed"] == pj[c]["passed"] for c in common)
            a = statistics.fmean(pj[c]["score"] for c in common) if common else 0
            b = statistics.fmean(judge_by[c]["score"] for c in common) if common else 0
            add(f"| Judge mean score | {a:.3f} | {b:.3f} | {b - a:+.3f} |")
            add(f"| Judge verdict agreement | | {agree}/{len(common)} | |")
        add("")

    add("## Tracing a request\n")
    add(f"Every case above ran through `guarded_chat`, traced to LangSmith project "
        f"`{cfg['langsmith_project']}` with the request ID in the run metadata. To reconstruct one "
        "without re-running it:\n")
    add("```bash\njq 'select(.request_id==\"<id>\")' logs/events.jsonl   # request, both guardrail decisions, response metrics\n```")
    add("The LangSmith trace for the same request shows retrieval, rerank, generation and each guardrail check as nested spans.\n")
    return "\n".join(L)
