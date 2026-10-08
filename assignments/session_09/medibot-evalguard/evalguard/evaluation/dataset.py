"""The labelled question set, and what one run of it observed.

``EvalCase`` is the label. ``Observation`` is everything the guarded system did
with that case, flattened to plain data so a run can be saved to JSON and
re-scored later without calling the target again (``run.py --rescore``).
"""

from __future__ import annotations

import json
from dataclasses import asdict, dataclass, field
from pathlib import Path

EVAL_DIR = Path(__file__).resolve().parent
DATASET_PATH = EVAL_DIR / "dataset.jsonl"
CONTROLS_PATH = EVAL_DIR / "controls.jsonl"


@dataclass
class EvalCase:
    id: str
    category: str  # normal | hard | sql | rbac | adversarial
    role: str
    question: str
    reference: str
    expected_behavior: str  # answer | refuse | block
    # One route, or several acceptable ones (e.g. a probe the input guardrail
    # may block or the target's own RBAC may refuse — either is correct).
    expected_route: str | list[str]
    expected_sources: list[str] = field(default_factory=list)

    @property
    def acceptable_routes(self) -> list[str]:
        r = self.expected_route
        return list(r) if isinstance(r, list) else [r]


@dataclass
class Observation:
    case_id: str
    request_id: str
    role: str
    question: str
    # What the user was shown.
    answer: str
    blocked: bool
    blocked_stage: str | None
    retrieval_type: str
    sources: list[dict]
    # What the target produced before the output guardrail — kept so the
    # evaluation can tell "the target leaked and the guardrail caught it" apart
    # from "the target never leaked".
    target_answer: str
    target_sources: list[dict]
    contexts: list[str]
    latency_seconds: float
    usage: dict
    input_reason: str
    output_reason: str
    target_error: str | None = None

    @classmethod
    def from_response(cls, case_id: str, resp) -> "Observation":
        target = resp.target
        return cls(
            case_id=case_id,
            request_id=resp.request_id,
            role=resp.role,
            question=resp.question,
            answer=resp.answer,
            blocked=resp.blocked,
            blocked_stage=resp.blocked_stage,
            retrieval_type=resp.retrieval_type,
            sources=list(resp.sources),
            target_answer=target.answer if target else "",
            target_sources=list(target.sources) if target else [],
            contexts=list(resp.contexts),
            latency_seconds=round(resp.latency_seconds, 3),
            usage=dict(resp.usage),
            input_reason=resp.input_decision.internal_reason if resp.input_decision else "",
            output_reason=resp.output_decision.internal_reason if resp.output_decision else "",
            target_error=target.error if target else None,
        )

    def to_dict(self) -> dict:
        return asdict(self)


def _read_jsonl(path: Path) -> list[dict]:
    rows = []
    for line in path.read_text(encoding="utf-8").splitlines():
        if line.strip():
            rows.append(json.loads(line))
    return rows


def load_cases(path: Path = DATASET_PATH, ids: set[str] | None = None) -> list[EvalCase]:
    cases = [EvalCase(**row) for row in _read_jsonl(path)]
    if ids:
        cases = [c for c in cases if c.id in ids]
    return cases


def load_controls(path: Path = CONTROLS_PATH) -> list[dict]:
    return _read_jsonl(path)
