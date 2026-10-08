"""Chat models for every structured LLM verdict in the platform.

LangChain's ``with_structured_output`` on Groq defaults to tool calling, where
the model *writes* a tool call and the server validates it afterwards. Under
load ``gpt-oss-20b`` occasionally emits a malformed one, Groq answers 400
("Tool call validation failed"), and a fail-closed guardrail turns that into a
false block — observed on a document answer during the first full run.

``json_schema`` with ``strict`` instead constrains decoding to the schema, so a
well-formed verdict is guaranteed by construction rather than by retry.
"""

from __future__ import annotations

from langchain_groq import ChatGroq

from evalguard.config import settings


class StructuredChatGroq(ChatGroq):
    def with_structured_output(self, schema, **kwargs):
        kwargs.setdefault("method", "json_schema")
        kwargs.setdefault("strict", True)
        if isinstance(schema, dict):
            # Strict mode requires a closed object; OpenEvals' default schema
            # leaves it open.
            schema = {**schema, "additionalProperties": False}
        return super().with_structured_output(schema, **kwargs)


def structured_chat_model(model: str, max_tokens: int, **kwargs) -> StructuredChatGroq:
    return StructuredChatGroq(
        model=model,
        api_key=settings.groq_api_key,
        temperature=0,
        max_tokens=max_tokens,
        max_retries=settings.llm_max_retries,
        **kwargs,
    )
