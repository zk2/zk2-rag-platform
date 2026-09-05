"""Metric behaviour, including what a judge reply may look like."""

from __future__ import annotations

from collections.abc import AsyncIterator, Sequence
from decimal import Decimal
from typing import Any

import pytest

from zk2.evals.metrics import (
    AnswerPresence,
    AnswerRelevancy,
    CitationRate,
    Correctness,
    EvalSample,
    Faithfulness,
    RetrievalRecall,
    _parse_score,
)
from zk2.llm.base import CompletionChunk
from zk2.llm.base import Message as LLMMessage

pytestmark = pytest.mark.unit


def sample(**overrides: Any) -> EvalSample:
    base = {
        "question": "How many vacation days?",
        "answer": "Twenty days a year [1].",
        "expected_answer": "20 days per year",
        "expected_sources": ["handbook.md"],
        "retrieved_sources": ["handbook.md"],
        "context": "[1] Employees accrue 20 days per year.",
    }
    return EvalSample(**{**base, **overrides})


class JudgeStub:
    """Returns a canned reply, standing in for a judge model."""

    name = "stub"

    def __init__(self, reply: str) -> None:
        self.reply = reply
        self.prompts: list[str] = []

    def complete(self, messages: Sequence[LLMMessage], **_: Any) -> AsyncIterator[CompletionChunk]:
        self.prompts.append(messages[-1].content)

        async def _stream() -> AsyncIterator[CompletionChunk]:
            yield CompletionChunk(delta=self.reply)

        return _stream()

    def estimate_cost(self, *_: Any, **__: Any) -> Decimal:
        return Decimal(0)


async def test_retrieval_recall_counts_expected_sources() -> None:
    assert await RetrievalRecall().score(sample(), None, "m") == 1.0
    partial = sample(expected_sources=["a.md", "b.md"], retrieved_sources=["a.md"])
    assert await RetrievalRecall().score(partial, None, "m") == 0.5
    missed = sample(retrieved_sources=["other.md"])
    assert await RetrievalRecall().score(missed, None, "m") == 0.0


async def test_retrieval_recall_is_one_when_nothing_is_expected() -> None:
    """An item with no expected sources cannot fail retrieval."""
    assert await RetrievalRecall().score(sample(expected_sources=[]), None, "m") == 1.0


async def test_citation_rate_looks_for_markers() -> None:
    assert await CitationRate().score(sample(), None, "m") == 1.0
    assert await CitationRate().score(sample(answer="Twenty days."), None, "m") == 0.0


async def test_citation_rate_is_zero_without_context() -> None:
    """Nothing to cite means no credit, not free credit."""
    assert await CitationRate().score(sample(context=""), None, "m") == 0.0


@pytest.mark.parametrize(
    ("answer", "expected"),
    [
        ("Twenty days.", 1.0),
        ("", 0.0),
        ("   ", 0.0),
        ("I don't know based on the context.", 0.0),
        # Cyrillic on purpose: the product answers in Russian too
        ("Не знаю", 0.0),  # noqa: RUF001
    ],
)
async def test_answer_presence(answer: str, expected: float) -> None:
    assert await AnswerPresence().score(sample(answer=answer), None, "m") == expected


@pytest.mark.parametrize(
    ("reply", "expected"),
    [
        ('{"score": 0.8, "reason": "mostly"}', 0.8),
        ('Sure! {"score": 1, "reason": "yes"} hope that helps', 1.0),
        ('{"score": 5}', 1.0),
        ('{"score": -3}', 0.0),
        ("not json at all", 0.0),
        ('{"reason": "forgot the score"}', 0.0),
    ],
)
def test_judge_replies_are_parsed_defensively(reply: str, expected: float) -> None:
    """A judge is a model: it will eventually reply with something unexpected."""
    assert _parse_score(reply) == expected


async def test_faithfulness_sends_context_and_answer() -> None:
    judge = JudgeStub('{"score": 0.9}')
    score = await Faithfulness().score(sample(), judge, "gpt-4.1-mini")  # type: ignore[arg-type]
    assert score == 0.9
    assert "CONTEXT" in judge.prompts[0] and "ANSWER" in judge.prompts[0]


async def test_relevancy_asks_about_the_question() -> None:
    judge = JudgeStub('{"score": 0.5}')
    await AnswerRelevancy().score(sample(), judge, "m")  # type: ignore[arg-type]
    assert "QUESTION" in judge.prompts[0]


async def test_correctness_needs_a_reference_answer() -> None:
    judge = JudgeStub('{"score": 1.0}')
    without = await Correctness().score(sample(expected_answer=None), judge, "m")  # type: ignore[arg-type]
    assert without == 0.0
    assert judge.prompts == []


async def test_judge_metrics_score_zero_without_a_judge() -> None:
    for metric in (Faithfulness(), AnswerRelevancy(), Correctness()):
        assert await metric.score(sample(), None, "m") == 0.0
