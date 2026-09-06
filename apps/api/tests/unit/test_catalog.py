"""The model catalog: lookups, pricing, and the unknown-model contract."""

from __future__ import annotations

from decimal import Decimal

import pytest

from zk2.llm.catalog import EmbeddingModel, embedding_cost, estimate_cost, get_catalog
from zk2.llm.registry import embedding_model_support

pytestmark = pytest.mark.unit


def test_catalog_loads_and_ids_are_unique() -> None:
    catalog = get_catalog()
    assert catalog.chat and catalog.embedding
    chat_ids = [m.id for m in catalog.chat]
    embedding_ids = [m.id for m in catalog.embedding]
    assert len(chat_ids) == len(set(chat_ids))
    assert len(embedding_ids) == len(set(embedding_ids))


def test_every_chat_model_has_a_positive_context_window() -> None:
    assert all(m.context_window > 0 for m in get_catalog().chat)


def test_lookup_by_id() -> None:
    model = get_catalog().chat_model("claude-opus-5")
    assert model is not None
    assert model.provider == "anthropic"
    assert model.supports_tools is True


def test_lookup_of_unknown_model_returns_none() -> None:
    assert get_catalog().chat_model("gpt-42-imaginary") is None


def test_for_provider_filters() -> None:
    openai_models = get_catalog().for_provider("openai")
    assert openai_models
    assert {m.provider for m in openai_models} == {"openai"}


def test_cost_is_computed_from_the_catalog_prices() -> None:
    # gpt-4.1-mini: $0.40 in / $1.60 out per million tokens
    cost = estimate_cost("gpt-4.1-mini", tokens_in=1_000_000, tokens_out=1_000_000)
    assert cost == Decimal("2.000000")


def test_cost_of_a_small_call_keeps_six_decimals() -> None:
    cost = estimate_cost("gpt-4o-mini", tokens_in=1000, tokens_out=500)
    assert cost == Decimal("0.000450")


def test_unknown_model_cost_is_none_not_zero() -> None:
    """Zero would silently under-report spend; None says 'we do not know'."""
    assert estimate_cost("some-new-model", tokens_in=1000, tokens_out=1000) is None


def test_local_models_are_free() -> None:
    assert estimate_cost("llama3.1:8b", tokens_in=10_000, tokens_out=10_000) == Decimal("0.000000")


def test_embedding_cost_and_dimensions() -> None:
    model = get_catalog().embedding_model("text-embedding-3-large")
    assert model is not None
    assert model.native_dimensions == 3072
    assert model.supports_dimensions is True
    assert embedding_cost("text-embedding-3-large", tokens=1_000_000) == Decimal("0.130000")


def test_unknown_embedding_cost_is_none() -> None:
    assert embedding_cost("mystery-embed", tokens=100) is None


def test_a_truncating_model_is_usable_whatever_its_native_width() -> None:
    """3072 native, 1536 stored: the dimensions parameter closes the gap."""
    model = get_catalog().embedding_model("text-embedding-3-large")
    assert model is not None
    assert embedding_model_support(model) is None


def test_a_fixed_width_model_that_does_not_match_the_index_is_refused() -> None:
    """Not in the yaml today - the check is what keeps it out of the picker."""
    model = EmbeddingModel(
        id="fixed-1024",
        provider="openai",
        display_name="Fixed 1024",
        native_dimensions=1024,
        supports_dimensions=False,
    )
    reason = embedding_model_support(model)
    assert reason is not None
    assert "1024" in reason and "1536" in reason


def test_a_provider_without_an_embedding_adapter_is_refused() -> None:
    """nomic and bge-m3 can be priced, but nothing here can call them."""
    model = get_catalog().embedding_model("nomic-embed-text-v1.5")
    assert model is not None
    assert embedding_model_support(model) == "no embedding adapter for provider 'local' yet"
