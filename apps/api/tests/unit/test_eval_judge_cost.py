"""The judge's calls belong in the cost of an eval run."""

from __future__ import annotations

from collections.abc import AsyncIterator, Sequence
from decimal import Decimal
from typing import Any

import pytest

from zk2.evals.metrics import EvalSample, Faithfulness
from zk2.evals.runner import _MeteredJudge
from zk2.llm.base import CompletionChunk, LLMProvider
from zk2.llm.base import Message as LLMMessage
from zk2.llm.catalog import estimate_cost

pytestmark = pytest.mark.unit


class PricedJudge(LLMProvider):
    """Replies like a judge and reports usage like a provider."""

    name = "openai"

    def __init__(self, usage: tuple[int, int] | None) -> None:
        self.usage = usage

    async def complete(
        self, messages: Sequence[LLMMessage], **_: Any
    ) -> AsyncIterator[CompletionChunk]:
        del messages
        yield CompletionChunk(
            delta='{"score": 1}',
            tokens_in=self.usage[0] if self.usage else None,
            tokens_out=self.usage[1] if self.usage else None,
        )

    def estimate_cost(self, model: str, *, tokens_in: int, tokens_out: int) -> Decimal | None:
        return estimate_cost(model, tokens_in=tokens_in, tokens_out=tokens_out)


SAMPLE = EvalSample(
    question="How many vacation days?",
    answer="Twenty [1].",
    expected_answer=None,
    expected_sources=[],
    retrieved_sources=[],
    context="[1] Employees accrue 20 days per year.",
)


async def test_judge_calls_add_up() -> None:
    judge = _MeteredJudge(PricedJudge(usage=(1000, 100)))
    for _ in range(2):
        assert await Faithfulness().score(SAMPLE, judge, "gpt-6-luna") == 1.0
    # gpt-6-luna: $0.10 in / $0.50 out per million, twice
    assert judge.cost_usd == Decimal("0.000300")


async def test_an_unpriced_judge_adds_nothing() -> None:
    """Unknown is not zero, but a run total has no slot for 'partly unknown'."""
    judge = _MeteredJudge(PricedJudge(usage=(1000, 100)))
    await Faithfulness().score(SAMPLE, judge, "judge-not-in-catalog")
    assert judge.cost_usd == Decimal(0)


async def test_a_reply_without_usage_adds_nothing() -> None:
    judge = _MeteredJudge(PricedJudge(usage=None))
    await Faithfulness().score(SAMPLE, judge, "gpt-6-luna")
    assert judge.cost_usd == Decimal(0)
