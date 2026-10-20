"""The LangChain model behind agents follows the same rules as plain completions."""

from __future__ import annotations

from typing import Any

import pytest

from zk2.agents import chat_models
from zk2.agents.chat_models import build_chat_model

pytestmark = pytest.mark.unit


@pytest.fixture
def org_endpoint(monkeypatch: pytest.MonkeyPatch) -> dict[str, str | None]:
    endpoint: dict[str, str | None] = {"url": None}

    async def fake_key(*_: Any, **__: Any) -> str:
        return "sk-test"

    async def fake_base_url(*_: Any, **__: Any) -> str | None:
        return endpoint["url"]

    monkeypatch.setattr(chat_models, "require_api_key", fake_key)
    monkeypatch.setattr(chat_models, "custom_base_url", fake_base_url)
    return endpoint


async def test_luna_gets_its_reasoning_turned_off(org_endpoint: dict[str, str | None]) -> None:
    """With reasoning on, Chat Completions refuses Luna any function tool."""
    model = await build_chat_model(
        None,  # type: ignore[arg-type]
        org_id=1,
        provider="openai",
        model="gpt-6-luna",
    )
    assert model.reasoning_effort == "none"  # type: ignore[attr-defined]
    assert model.temperature == 0.0  # type: ignore[attr-defined]


async def test_older_models_get_no_reasoning_setting(org_endpoint: dict[str, str | None]) -> None:
    model = await build_chat_model(
        None,  # type: ignore[arg-type]
        org_id=1,
        provider="openai",
        model="gpt-4.1-mini",
    )
    assert model.reasoning_effort is None  # type: ignore[attr-defined]


async def test_the_org_endpoint_reaches_the_agent(org_endpoint: dict[str, str | None]) -> None:
    """It used to reach plain completions only; agents went to api.openai.com."""
    org_endpoint["url"] = "https://llm.example.test/v1"
    model = await build_chat_model(
        None,  # type: ignore[arg-type]
        org_id=1,
        provider="openai",
        model="gpt-6-luna",
    )
    assert model.openai_api_base == "https://llm.example.test/v1"  # type: ignore[attr-defined]
