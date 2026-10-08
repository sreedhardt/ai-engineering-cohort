"""RAGAS: faithfulness, answer relevancy, context precision, context recall.

Scored only on document-RAG answers to questions that should be answered. A
refusal has no retrieved contexts to be faithful to, and the SQL path returns
rows rather than passages, so those cases are covered by the heuristics and the
judge instead.

Two details matter for repeatability:

  * the evaluator LLM runs at temperature 0, so re-scoring identical answers
    gives near-identical numbers (``run.py --rescore`` measures exactly this);
  * a metric that errors is recorded as ``None`` with the error, never as 0.0 —
    a rate-limit failure must not be mistaken for an unfaithful answer.
"""

from __future__ import annotations

import asyncio
import functools
import math
from dataclasses import dataclass, field

from evalguard.config import settings
from evalguard.evaluation.dataset import EvalCase, Observation

METRICS = ("faithfulness", "answer_relevancy", "context_precision", "context_recall")


@dataclass
class RagasResult:
    case_id: str
    scores: dict[str, float | None] = field(default_factory=dict)
    errors: dict[str, str] = field(default_factory=dict)


def eligible(case: EvalCase, obs: Observation) -> bool:
    return (
        case.expected_behavior == "answer"
        and not obs.blocked
        and obs.retrieval_type == "hybrid_rag"
        and bool(obs.contexts)
    )


@functools.lru_cache(maxsize=1)
def _fastembed_embeddings():
    """RAGAS embeddings backed by fastembed — Groq serves no embedding models,
    and this is the same local model the target retrieves with."""
    from fastembed import TextEmbedding  # noqa: PLC0415
    from ragas.embeddings.base import BaseRagasEmbedding  # noqa: PLC0415

    class FastEmbedEmbeddings(BaseRagasEmbedding):
        def __init__(self, model_name: str):
            super().__init__()
            self._model = TextEmbedding(model_name)

        def embed_text(self, text: str, **kwargs) -> list[float]:
            return next(iter(self._model.embed([text]))).tolist()

        async def aembed_text(self, text: str, **kwargs) -> list[float]:
            return await asyncio.to_thread(self.embed_text, text)

        def embed_texts(self, texts: list[str], **kwargs) -> list[list[float]]:
            return [v.tolist() for v in self._model.embed(list(texts))]

        async def aembed_texts(self, texts: list[str], **kwargs) -> list[list[float]]:
            return await asyncio.to_thread(self.embed_texts, texts)

    return FastEmbedEmbeddings(settings.ragas_embedding_model)


def _metrics():
    """Built per scoring pass: the async client belongs to one event loop."""
    from openai import AsyncOpenAI  # noqa: PLC0415
    from ragas.llms import llm_factory  # noqa: PLC0415
    from ragas.metrics.collections import (  # noqa: PLC0415
        AnswerRelevancy,
        ContextPrecision,
        ContextRecall,
        Faithfulness,
    )

    # Groq's OpenAI-compatible endpoint, so RAGAS uses its JSON-mode path.
    client = AsyncOpenAI(
        api_key=settings.groq_api_key,
        base_url="https://api.groq.com/openai/v1",
        max_retries=settings.llm_max_retries,
    )
    llm = llm_factory(
        settings.ragas_model,
        client=client,
        temperature=0.0,
        max_tokens=settings.ragas_max_tokens,
        reasoning_effort=settings.ragas_reasoning_effort,
    )
    return {
        "faithfulness": Faithfulness(llm=llm),
        "answer_relevancy": AnswerRelevancy(llm=llm, embeddings=_fastembed_embeddings()),
        "context_precision": ContextPrecision(llm=llm),
        "context_recall": ContextRecall(llm=llm),
    }


def _metric_kwargs(name: str, case: EvalCase, obs: Observation) -> dict:
    if name == "faithfulness":
        return dict(user_input=case.question, response=obs.answer, retrieved_contexts=obs.contexts)
    if name == "answer_relevancy":
        return dict(user_input=case.question, response=obs.answer)
    if name == "context_precision":
        return dict(user_input=case.question, reference=case.reference, retrieved_contexts=obs.contexts)
    return dict(user_input=case.question, retrieved_contexts=obs.contexts, reference=case.reference)


async def _score_one(case: EvalCase, obs: Observation, metrics: dict, sem: asyncio.Semaphore) -> RagasResult:
    result = RagasResult(case_id=case.id)
    for name in METRICS:
        async with sem:
            try:
                value = float((await metrics[name].ascore(**_metric_kwargs(name, case, obs))).value)
                if math.isnan(value):
                    raise ValueError("metric returned NaN")
                result.scores[name] = round(value, 4)
            except Exception as exc:  # noqa: BLE001 - recorded, never scored as 0
                result.scores[name] = None
                result.errors[name] = f"{type(exc).__name__}: {str(exc)[:160]}"
    return result


async def _score_all(pairs: list[tuple[EvalCase, Observation]]) -> list[RagasResult]:
    sem = asyncio.Semaphore(settings.ragas_concurrency)
    metrics = _metrics()
    return await asyncio.gather(*(_score_one(c, o, metrics, sem) for c, o in pairs))


def score(pairs: list[tuple[EvalCase, Observation]]) -> list[RagasResult]:
    """Score every eligible (case, observation) pair."""
    todo = [(c, o) for c, o in pairs if eligible(c, o)]
    if not todo:
        return []
    return asyncio.run(_score_all(todo))


def score_single(question: str, answer: str, contexts: list[str], reference: str) -> RagasResult:
    """Score an arbitrary answer — used by the evaluator controls."""
    case = EvalCase("control", "control", "doctor", question, reference, "answer", "hybrid_rag")
    obs_like = type("O", (), {"answer": answer, "contexts": contexts})()
    return asyncio.run(_score_all([(case, obs_like)]))[0]


def aggregate(results: list[RagasResult]) -> dict[str, dict]:
    out = {}
    for name in METRICS:
        values = [r.scores[name] for r in results if r.scores.get(name) is not None]
        out[name] = {
            "mean": round(sum(values) / len(values), 4) if values else None,
            "n": len(values),
            "errors": sum(1 for r in results if name in r.errors),
        }
    return out
