"""Model catalog: ids, pricing, context windows and capabilities.

Provider adapters ask the catalog what a model costs and what it can do; they
never carry that knowledge themselves. A model missing from the catalog is
still usable - the cost of a call is reported as unknown rather than silently
counted as zero, which is the failure mode worth avoiding in a billing path.
"""

from __future__ import annotations

from decimal import Decimal
from functools import lru_cache
from pathlib import Path
from typing import Literal

import structlog
import yaml
from pydantic import BaseModel, ConfigDict, Field

logger = structlog.get_logger()

CATALOG_PATH = Path(__file__).with_name("catalog.yaml")
TOKENS_PER_PRICE_UNIT = Decimal(1_000_000)


class ChatModel(BaseModel):
    model_config = ConfigDict(frozen=True)

    id: str
    provider: str
    display_name: str
    context_window: int
    max_output_tokens: int | None = None
    supports_tools: bool = False
    supports_vision: bool = False
    # Sampling parameters were removed on the newest Claude models: sending
    # temperature to them is a 400, so the adapter has to know.
    supports_temperature: bool = True
    price_in_per_mtok: Decimal
    price_out_per_mtok: Decimal
    kind: Literal["chat"] = "chat"


class EmbeddingModel(BaseModel):
    model_config = ConfigDict(frozen=True)

    id: str
    provider: str
    display_name: str
    native_dimensions: int
    supports_dimensions: bool = False
    price_in_per_mtok: Decimal = Field(default=Decimal(0))
    kind: Literal["embedding"] = "embedding"
    # Filled in when the catalog is served: a model in the yaml is not
    # necessarily a model this deployment can run. See embedding_model_support.
    usable: bool = True
    unusable_reason: str | None = None


class Catalog(BaseModel):
    chat: list[ChatModel]
    embedding: list[EmbeddingModel]
    # Width every vector is stored at, whatever the model's native size. Set
    # when the catalog is served; 0 in the raw yaml. See ADR-0004.
    embedding_dimensions: int = 0

    def chat_model(self, model_id: str) -> ChatModel | None:
        return next((m for m in self.chat if m.id == model_id), None)

    def embedding_model(self, model_id: str) -> EmbeddingModel | None:
        return next((m for m in self.embedding if m.id == model_id), None)

    def for_provider(self, provider: str) -> list[ChatModel]:
        return [m for m in self.chat if m.provider == provider]


@lru_cache(maxsize=1)
def get_catalog() -> Catalog:
    raw = yaml.safe_load(CATALOG_PATH.read_text(encoding="utf-8"))
    return Catalog.model_validate(raw)


def estimate_cost(model_id: str, *, tokens_in: int, tokens_out: int) -> Decimal | None:
    """USD for one call, or None when the model is not in the catalog.

    None means "we do not know", and callers must record it as such: a missing
    price silently rounded to zero is an under-billed customer.
    """
    model = get_catalog().chat_model(model_id)
    if model is None:
        logger.warning("catalog.unknown_model", model=model_id)
        return None
    cost = (
        model.price_in_per_mtok * Decimal(tokens_in)
        + model.price_out_per_mtok * Decimal(tokens_out)
    ) / TOKENS_PER_PRICE_UNIT
    return cost.quantize(Decimal("0.000001"))


def embedding_cost(model_id: str, *, tokens: int) -> Decimal | None:
    model = get_catalog().embedding_model(model_id)
    if model is None:
        logger.warning("catalog.unknown_embedding_model", model=model_id)
        return None
    cost = model.price_in_per_mtok * Decimal(tokens) / TOKENS_PER_PRICE_UNIT
    return cost.quantize(Decimal("0.000001"))


def context_window(model_id: str) -> int | None:
    model = get_catalog().chat_model(model_id)
    return model.context_window if model else None
