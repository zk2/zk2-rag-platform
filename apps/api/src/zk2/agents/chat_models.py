"""LangChain chat models built from this project's own configuration.

The agent loop runs on LangGraph, which wants a LangChain model object. That
object is constructed here from the organization's stored key and the model
catalog, so the provider abstraction, the per-org keys and the catalog's
knowledge (which models reject sampling parameters, what a model costs) still
govern what happens - the framework supplies the loop, not the policy.

Tool calling is currently wired for OpenAI and Anthropic. The catalog says
which models support tools at all; a provider without an adapter here fails
with a message that names the limitation instead of a stack trace.
"""

from __future__ import annotations

import structlog
from langchain_core.language_models import BaseChatModel
from sqlalchemy.ext.asyncio import AsyncSession

from zk2.core.errors import ValidationError
from zk2.llm.catalog import get_catalog
from zk2.llm.registry import require_api_key

logger = structlog.get_logger()

TOOL_CAPABLE_PROVIDERS = ("openai", "anthropic")
DEFAULT_MAX_OUTPUT_TOKENS = 4096


async def build_chat_model(
    db: AsyncSession,
    *,
    org_id: int,
    provider: str,
    model: str,
    temperature: float = 0.0,
    max_tokens: int | None = None,
) -> BaseChatModel:
    """A LangChain model for the agent loop, wired to this org's credentials."""
    if provider not in TOOL_CAPABLE_PROVIDERS:
        msg = (
            f"Agents do not support the {provider} provider yet - "
            f"use one of: {', '.join(TOOL_CAPABLE_PROVIDERS)}"
        )
        raise ValidationError(msg)

    spec = get_catalog().chat_model(model)
    if spec is not None and not spec.supports_tools:
        raise ValidationError(f"{model} does not support tool use")

    api_key = await require_api_key(db, org_id=org_id, provider=provider)
    supports_temperature = spec.supports_temperature if spec else True
    cap = spec.max_output_tokens if spec and spec.max_output_tokens else None
    budget = min(max_tokens or DEFAULT_MAX_OUTPUT_TOKENS, cap or DEFAULT_MAX_OUTPUT_TOKENS)

    if provider == "openai":
        from langchain_openai import ChatOpenAI  # noqa: PLC0415  (heavy import)

        return ChatOpenAI(
            model=model,
            api_key=api_key,
            temperature=temperature if supports_temperature else None,
            max_completion_tokens=budget,
        )

    from langchain_anthropic import ChatAnthropic  # noqa: PLC0415  (heavy import)

    kwargs: dict[str, object] = {"model": model, "api_key": api_key, "max_tokens": budget}
    # Sampling was removed on the current Claude models: sending it is a 400
    if supports_temperature:
        kwargs["temperature"] = temperature
    return ChatAnthropic(**kwargs)
