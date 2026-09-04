"""OpenAI LLM + embeddings providers."""

from __future__ import annotations

from collections.abc import AsyncIterator, Sequence
from decimal import Decimal
from typing import Any, Final

from openai import AsyncOpenAI

from zk2.config import get_settings
from zk2.core.errors import ValidationError
from zk2.llm.base import CompletionChunk, EmbeddingProvider, LLMProvider, Message

# Approximate USD per 1M tokens (kept rough; see https://openai.com/pricing).
_PRICE_PER_1M: Final[dict[str, tuple[Decimal, Decimal]]] = {
    "gpt-4o": (Decimal("2.50"), Decimal("10.00")),
    "gpt-4o-mini": (Decimal("0.15"), Decimal("0.60")),
    "gpt-4.1": (Decimal("3.00"), Decimal("12.00")),
    "gpt-4.1-mini": (Decimal("0.40"), Decimal("1.60")),
    "o3-mini": (Decimal("1.10"), Decimal("4.40")),
    # embeddings (input only, output 0)
    "text-embedding-3-small": (Decimal("0.02"), Decimal("0")),
    "text-embedding-3-large": (Decimal("0.13"), Decimal("0")),
}

# Native width of each embedding model. We do not store vectors at these widths:
# every model is normalised to EMBEDDING_DIMENSIONS (1536) because pgvector caps
# HNSW at 2000 dimensions - see ADR-0004.
_EMB_NATIVE_DIM: Final[dict[str, int]] = {
    "text-embedding-3-small": 1536,
    "text-embedding-3-large": 3072,
}

# Models that accept the `dimensions` request parameter (Matryoshka truncation).
_SUPPORTS_DIMENSIONS: Final[frozenset[str]] = frozenset(
    {"text-embedding-3-small", "text-embedding-3-large"}
)


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

    def estimate_cost(self, model: str, *, tokens_in: int, tokens_out: int) -> Decimal:
        price = _PRICE_PER_1M.get(model)
        if price is None:
            return Decimal("0")
        in_price, out_price = price
        return (
            (Decimal(tokens_in) * in_price + Decimal(tokens_out) * out_price) / Decimal("1000000")
        ).quantize(Decimal("0.000001"))


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

        native = _EMB_NATIVE_DIM.get(model)
        if model not in _SUPPORTS_DIMENSIONS and native is not None and native != self.dimensions:
            msg = (
                f"{model} returns {native}-dimensional vectors and does not support "
                f"the `dimensions` parameter; configured width is {self.dimensions}"
            )
            raise ValidationError(msg)

    async def embed_documents(self, texts: list[str]) -> list[list[float]]:
        """Embed in batches: a single large document can otherwise exceed the
        request limit, and one failure would cost the whole document."""
        if not texts:
            return []
        vectors: list[list[float]] = []
        for start in range(0, len(texts), self._batch_size):
            batch = texts[start : start + self._batch_size]
            kwargs: dict[str, Any] = {"model": self.model, "input": batch}
            if self.model in _SUPPORTS_DIMENSIONS:
                kwargs["dimensions"] = self.dimensions
            resp = await self._client.embeddings.create(**kwargs)
            vectors.extend(d.embedding for d in resp.data)
        return vectors

    async def embed_query(self, text: str) -> list[float]:
        result = await self.embed_documents([text])
        return result[0] if result else []
