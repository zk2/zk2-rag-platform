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

    @abstractmethod
    async def complete(
        self,
        messages: Sequence[Message],
        *,
        model: str,
        stream: bool = True,
        temperature: float = 0.0,
        max_tokens: int | None = None,
    ) -> AsyncIterator[CompletionChunk]: ...

    @abstractmethod
    def estimate_cost(
        self, model: str, *, tokens_in: int, tokens_out: int
    ) -> Decimal: ...


class EmbeddingProvider(ABC):
    name: str
    model: str
    dimensions: int

    @abstractmethod
    async def embed_documents(self, texts: list[str]) -> list[list[float]]: ...

    @abstractmethod
    async def embed_query(self, text: str) -> list[float]: ...
