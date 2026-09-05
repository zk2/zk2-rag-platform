"""OpenAI LLM + embeddings providers."""

from __future__ import annotations

from collections.abc import AsyncIterator, Sequence
from decimal import Decimal
from typing import Any

from openai import AsyncOpenAI

from zk2.config import get_settings
from zk2.core.errors import ValidationError
from zk2.llm.base import CompletionChunk, EmbeddingProvider, LLMProvider, Message
from zk2.llm.catalog import estimate_cost as catalog_cost
from zk2.llm.catalog import get_catalog
from zk2.llm.errors import provider_call


class OpenAIProvider(LLMProvider):
    name = "openai"

    def __init__(self, api_key: str, base_url: str | None = None) -> None:
        self._client = AsyncOpenAI(api_key=api_key, base_url=base_url)

    async def complete(
        self,
        messages: Sequence[Message],
        *,
        model: str,
        stream: bool = True,
        temperature: float = 0.0,
        max_tokens: int | None = None,
    ) -> AsyncIterator[CompletionChunk]:
        payload_messages = [{"role": m.role, "content": m.content} for m in messages]
        async with provider_call(self.name):
            if not stream:
                resp = await self._client.chat.completions.create(
                    model=model,
                    messages=payload_messages,  # type: ignore[arg-type]
                    temperature=temperature,
                    max_tokens=max_tokens,
                )
                choice = resp.choices[0]
                yield CompletionChunk(
                    delta=choice.message.content or "",
                    tokens_in=resp.usage.prompt_tokens if resp.usage else None,
                    tokens_out=resp.usage.completion_tokens if resp.usage else None,
                    finish_reason=choice.finish_reason,
                )
                return

            async with self._client.chat.completions.stream(
                model=model,
                messages=payload_messages,  # type: ignore[arg-type]
                temperature=temperature,
                max_tokens=max_tokens,
            ) as stream_ctx:
                async for event in stream_ctx:
                    if event.type == "content.delta":
                        yield CompletionChunk(delta=event.delta)
                    elif event.type == "content.done":
                        yield CompletionChunk(finish_reason="stop")
                final = await stream_ctx.get_final_completion()
                if final.usage:
                    yield CompletionChunk(
                        tokens_in=final.usage.prompt_tokens,
                        tokens_out=final.usage.completion_tokens,
                    )

    def estimate_cost(self, model: str, *, tokens_in: int, tokens_out: int) -> Decimal | None:
        return catalog_cost(model, tokens_in=tokens_in, tokens_out=tokens_out)


class OpenAIEmbeddings(EmbeddingProvider):
    name = "openai"

    def __init__(
        self, api_key: str, model: str = "text-embedding-3-small", dimensions: int | None = None
    ) -> None:
        settings = get_settings().ingest
        self.model = model
        self.dimensions = settings.embedding_dimensions if dimensions is None else dimensions
        self._batch_size = settings.embedding_batch_size
        self._client = AsyncOpenAI(api_key=api_key)

        spec = get_catalog().embedding_model(model)
        self._supports_dimensions = spec.supports_dimensions if spec else False
        if (
            spec is not None
            and not spec.supports_dimensions
            and spec.native_dimensions != self.dimensions
        ):
            msg = (
                f"{model} returns {spec.native_dimensions}-dimensional vectors and "
                f"does not support the `dimensions` parameter; configured width is "
                f"{self.dimensions}"
            )
            raise ValidationError(msg)

    async def embed_documents(self, texts: list[str]) -> list[list[float]]:
        """Embed in batches: a single large document can otherwise exceed the
        request limit, and one failure would cost the whole document."""
        if not texts:
            return []
        vectors: list[list[float]] = []
        async with provider_call(self.name):
            for start in range(0, len(texts), self._batch_size):
                batch = texts[start : start + self._batch_size]
                kwargs: dict[str, Any] = {"model": self.model, "input": batch}
                if self._supports_dimensions:
                    kwargs["dimensions"] = self.dimensions
                resp = await self._client.embeddings.create(**kwargs)
                vectors.extend(d.embedding for d in resp.data)
        return vectors

    async def embed_query(self, text: str) -> list[float]:
        result = await self.embed_documents([text])
        return result[0] if result else []
