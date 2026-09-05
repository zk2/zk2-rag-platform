"""Google Gemini chat provider.

Gemini keeps the system prompt in the request config and calls assistant turns
"model", so both are translated here. Usage arrives on the last streamed chunk.
"""

from __future__ import annotations

from collections.abc import AsyncIterator, Sequence
from decimal import Decimal

from google import genai
from google.genai import types as genai_types

from zk2.llm.base import CompletionChunk, LLMProvider, Message
from zk2.llm.catalog import estimate_cost as catalog_cost
from zk2.llm.errors import provider_call

_ROLE_MAP = {"assistant": "model", "user": "user"}


class GeminiProvider(LLMProvider):
    name = "gemini"

    def __init__(self, api_key: str) -> None:
        self._client = genai.Client(api_key=api_key)

    async def complete(
        self,
        messages: Sequence[Message],
        *,
        model: str,
        stream: bool = True,
        temperature: float = 0.0,
        max_tokens: int | None = None,
    ) -> AsyncIterator[CompletionChunk]:
        system = "\n\n".join(m.content for m in messages if m.role == "system")
        contents = [
            genai_types.Content(
                role=_ROLE_MAP.get(m.role, "user"),
                parts=[genai_types.Part(text=m.content)],
            )
            for m in messages
            if m.role != "system"
        ]
        config = genai_types.GenerateContentConfig(
            system_instruction=system or None,
            temperature=temperature,
            max_output_tokens=max_tokens,
        )

        async with provider_call(self.name):
            if not stream:
                response = await self._client.aio.models.generate_content(
                    model=model, contents=contents, config=config
                )
                usage = response.usage_metadata
                yield CompletionChunk(
                    delta=response.text or "",
                    tokens_in=usage.prompt_token_count if usage else None,
                    tokens_out=usage.candidates_token_count if usage else None,
                    finish_reason="stop",
                )
                return

            tokens_in = tokens_out = None
            async for chunk in await self._client.aio.models.generate_content_stream(
                model=model, contents=contents, config=config
            ):
                if chunk.text:
                    yield CompletionChunk(delta=chunk.text)
                if chunk.usage_metadata:
                    tokens_in = chunk.usage_metadata.prompt_token_count
                    tokens_out = chunk.usage_metadata.candidates_token_count
        yield CompletionChunk(tokens_in=tokens_in, tokens_out=tokens_out, finish_reason="stop")

    def estimate_cost(self, model: str, *, tokens_in: int, tokens_out: int) -> Decimal | None:
        return catalog_cost(model, tokens_in=tokens_in, tokens_out=tokens_out)
