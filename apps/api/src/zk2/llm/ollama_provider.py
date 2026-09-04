"""Local models through Ollama.

No SDK: Ollama's chat endpoint is a small NDJSON stream over HTTP, and httpx is
already a dependency. The base URL is per-organization, which is what makes a
self-hosted or on-premise deployment possible without touching this code.

Token counts come back as prompt_eval_count / eval_count. Local inference has
no per-token price, so cost is zero rather than unknown.
"""

from __future__ import annotations

import json
from collections.abc import AsyncIterator, Sequence
from decimal import Decimal
from typing import Any

import httpx
import structlog

from zk2.llm.base import CompletionChunk, LLMProvider, Message

logger = structlog.get_logger()

DEFAULT_TIMEOUT = httpx.Timeout(connect=5.0, read=300.0, write=30.0, pool=5.0)


class OllamaProvider(LLMProvider):
    name = "ollama"

    def __init__(self, base_url: str) -> None:
        self._base_url = base_url.rstrip("/")

    async def complete(
        self,
        messages: Sequence[Message],
        *,
        model: str,
        stream: bool = True,
        temperature: float = 0.0,
        max_tokens: int | None = None,
    ) -> AsyncIterator[CompletionChunk]:
        payload: dict[str, Any] = {
            "model": model,
            "messages": [{"role": m.role, "content": m.content} for m in messages],
            "stream": stream,
            "options": {"temperature": temperature},
        }
        if max_tokens:
            payload["options"]["num_predict"] = max_tokens

        async with httpx.AsyncClient(timeout=DEFAULT_TIMEOUT) as client:
            if not stream:
                response = await client.post(f"{self._base_url}/api/chat", json=payload)
                response.raise_for_status()
                body = response.json()
                yield CompletionChunk(
                    delta=body.get("message", {}).get("content", ""),
                    tokens_in=body.get("prompt_eval_count"),
                    tokens_out=body.get("eval_count"),
                    finish_reason=body.get("done_reason", "stop"),
                )
                return

            async with client.stream(
                "POST", f"{self._base_url}/api/chat", json=payload
            ) as response:
                response.raise_for_status()
                async for line in response.aiter_lines():
                    if not line.strip():
                        continue
                    try:
                        event = json.loads(line)
                    except json.JSONDecodeError:
                        logger.warning("ollama.bad_line", line=line[:200])
                        continue
                    delta = event.get("message", {}).get("content", "")
                    if delta:
                        yield CompletionChunk(delta=delta)
                    if event.get("done"):
                        yield CompletionChunk(
                            tokens_in=event.get("prompt_eval_count"),
                            tokens_out=event.get("eval_count"),
                            finish_reason=event.get("done_reason", "stop"),
                        )

    def estimate_cost(self, model: str, *, tokens_in: int, tokens_out: int) -> Decimal | None:
        # Local inference: the hardware is the cost, not the tokens
        del model, tokens_in, tokens_out
        return Decimal("0.000000")
