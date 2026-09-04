"""Anthropic (Claude) chat provider.

Two shapes differ from the OpenAI-style API and are handled here rather than
leaking into the RAG code:

* the system prompt is a top-level parameter, not a message with role "system"
* `max_tokens` is required, so a ceiling is always sent

Sampling was removed on the current Claude models: sending `temperature` to
Opus 5, Sonnet 5 or Opus 4.8 is a 400. The catalog says which models still
accept it, and the bot's temperature setting is simply not sent to those that
do not.
"""

from __future__ import annotations

from collections.abc import AsyncIterator, Sequence
from decimal import Decimal
from typing import Any

import structlog
from anthropic import AsyncAnthropic

from zk2.llm.base import CompletionChunk, LLMProvider, Message
from zk2.llm.catalog import estimate_cost as catalog_cost
from zk2.llm.catalog import get_catalog

logger = structlog.get_logger()

DEFAULT_MAX_OUTPUT_TOKENS = 4096


class AnthropicProvider(LLMProvider):
    name = "anthropic"

    def __init__(self, api_key: str, base_url: str | None = None) -> None:
        self._client = AsyncAnthropic(api_key=api_key, base_url=base_url)

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
        turns = [{"role": m.role, "content": m.content} for m in messages if m.role != "system"]

        spec = get_catalog().chat_model(model)
        cap = spec.max_output_tokens if spec and spec.max_output_tokens else None
        budget = max_tokens or DEFAULT_MAX_OUTPUT_TOKENS
        if cap:
            budget = min(budget, cap)

        kwargs: dict[str, Any] = {
            "model": model,
            "max_tokens": budget,
            "messages": turns,
        }
        if system:
            kwargs["system"] = system
        # Sampling is rejected by the current models; the catalog knows which
        if temperature and (spec is None or spec.supports_temperature):
            kwargs["temperature"] = temperature

        if not stream:
            message = await self._client.messages.create(**kwargs)
            text = "".join(b.text for b in message.content if b.type == "text")
            yield CompletionChunk(
                delta=text,
                tokens_in=message.usage.input_tokens,
                tokens_out=message.usage.output_tokens,
                finish_reason=message.stop_reason,
            )
            return

        async with self._client.messages.stream(**kwargs) as stream_ctx:
            async for text in stream_ctx.text_stream:
                yield CompletionChunk(delta=text)
            final = await stream_ctx.get_final_message()

        if final.stop_reason == "refusal":
            logger.warning("anthropic.refusal", model=model, details=final.stop_details)
        yield CompletionChunk(
            tokens_in=final.usage.input_tokens,
            tokens_out=final.usage.output_tokens,
            finish_reason=final.stop_reason,
        )

    def estimate_cost(self, model: str, *, tokens_in: int, tokens_out: int) -> Decimal | None:
        return catalog_cost(model, tokens_in=tokens_in, tokens_out=tokens_out)
