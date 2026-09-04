"""LLM and Embeddings provider abstractions."""

from __future__ import annotations

from abc import ABC, abstractmethod
from collections.abc import AsyncIterator, Sequence
from dataclasses import dataclass
from decimal import Decimal


@dataclass(slots=True)
class Message:
    role: str  # system | user | assistant | tool
    content: str


@dataclass(slots=True)
class CompletionChunk:
    delta: str = ""
    tokens_in: int | None = None
    tokens_out: int | None = None
    finish_reason: str | None = None


class LLMProvider(ABC):
    name: str

    # NOT `async def`: the method returns an async iterator, not a coroutine.
    # Declaring it async would type it as Coroutine[..., AsyncIterator[...]] and
    # break every implementation under mypy --strict.
    @abstractmethod
    def complete(
        self,
        messages: Sequence[Message],
        *,
        model: str,
        stream: bool = True,
        temperature: float = 0.0,
        max_tokens: int | None = None,
    ) -> AsyncIterator[CompletionChunk]: ...

    @abstractmethod
    def estimate_cost(self, model: str, *, tokens_in: int, tokens_out: int) -> Decimal | None:
        """USD for one call, or None when the model is not in the catalog."""
        ...


class EmbeddingProvider(ABC):
    name: str
    model: str
    dimensions: int

    @abstractmethod
    async def embed_documents(self, texts: list[str]) -> list[list[float]]: ...

    @abstractmethod
    async def embed_query(self, text: str) -> list[float]: ...
