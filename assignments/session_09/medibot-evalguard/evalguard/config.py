"""Settings for the evaluation and guardrail platform.

Deliberately separate from the target system's own configuration: this platform
is graded as its own system and must be able to point at a different target
without editing the target.
"""

from __future__ import annotations

from pathlib import Path

from pydantic_settings import BaseSettings, SettingsConfigDict

PROJECT_ROOT = Path(__file__).resolve().parents[1]
REPORTS_DIR = PROJECT_ROOT / "reports"
LOGS_DIR = PROJECT_ROOT / "logs"


class Settings(BaseSettings):
    model_config = SettingsConfigDict(
        env_file=PROJECT_ROOT / ".env", extra="ignore"
    )

    groq_api_key: str

    # Input guardrail: a dedicated prompt-injection classifier that returns a
    # probability, so the decision is a threshold rather than prose parsing.
    input_guard_model: str = "meta-llama/llama-prompt-guard-2-86m"
    input_guard_threshold: float = 0.5

    # Output guardrail: a policy-classification model. Corroborating signal
    # only — the deterministic RBAC-leak check is the real boundary.
    output_policy_model: str = "openai/gpt-oss-safeguard-20b"
    # This model reasons before answering and shares one token budget across
    # reasoning and content; too small a cap yields an empty verdict, which we
    # treat as a block. 1500 was measured as the point it reliably completes.
    output_policy_max_tokens: int = 1500
    # Low effort: same verdicts on our probes at ~92 output tokens instead of
    # ~357. On Groq's on-demand tier this model gets 2000 tokens a minute, and
    # at medium effort every request waited ~20s for budget.
    output_policy_reasoning_effort: str = "low"

    # OpenEvals-backed guardrail checks (topic/abuse on input, groundedness on
    # output). A small, fast model: these run on every request.
    openevals_guard_model: str = "openai/gpt-oss-20b"
    openevals_guard_max_tokens: int = 1024
    openevals_guard_reasoning_effort: str = "low"

    # Judge: a different model family from the generator, so an answer cannot
    # be graded by the same weights that produced it. Reasoning is switched off
    # because Groq's on-demand tier caps this model at 1000 output tokens per
    # minute; a reasoning trace alone would exceed it.
    judge_model: str = "qwen/qwen3.8-27b"
    judge_max_tokens: int = 800

    # RAGAS decomposes answers into claims and checks each one, so it makes
    # several calls per question. It runs on its own model so its token budget
    # does not compete with the judge's.
    ragas_model: str = "openai/gpt-oss-20b"
    ragas_max_tokens: int = 4000
    # "low" for gpt-oss models, "none" for qwen. RAGAS asks for extraction and
    # yes/no verdicts; long reasoning traces add cost, not accuracy.
    ragas_reasoning_effort: str = "low"
    ragas_embedding_model: str = "BAAI/bge-small-en-v1.5"
    ragas_concurrency: int = 2

    # Retries on rate limits. A guardrail that hits a 429 fails closed, so
    # without retries a busy minute would show up as false blocks.
    llm_max_retries: int = 6

    langsmith_api_key: str | None = None
    langsmith_project: str = "medibot-evalguard"
    langsmith_tracing: bool = True

    # Where the target system lives, relative to this project.
    medibot_root: str = "../../session_05/medibot"

    # Heuristic thresholds
    latency_budget_seconds: float = 20.0

    # Pass thresholds for the evaluation report. Set before the first full run,
    # not tuned to it.
    min_faithfulness: float = 0.80
    min_answer_relevancy: float = 0.70
    min_context_precision: float = 0.60
    min_context_recall: float = 0.70
    min_judge_score: float = 0.75
    min_judge_pass_rate: float = 0.80
    min_heuristic_pass_rate: float = 0.90
    max_false_block_rate: float = 0.10
    # Minimum gap between evaluation requests. The output policy model is
    # limited to 3 requests a minute on Groq's free tier; firing cases back to
    # back made every request queue ~20s for it, so the latency heuristic
    # measured the eval's own load rather than the system.
    eval_pace_seconds: float = 20.0
    # Above this share of empty/errored answers the system is treated as broken
    # and the LLM-backed stages are skipped.
    fail_fast_error_rate: float = 0.20

    @property
    def target_root(self) -> Path:
        return (PROJECT_ROOT / self.medibot_root).resolve()


settings = Settings()
