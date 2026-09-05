"""Metrics for one answer.

Two kinds, deliberately kept apart:

* deterministic metrics compute from what the pipeline already produced -
  whether the expected sources were retrieved, whether the answer cites
  anything, how long it took. They are free, stable, and never disagree with
  themselves between runs.
* judge metrics ask a model. They are the only way to score faithfulness or
  correctness against a reference answer, and they cost money and vary, so they
  are opt-in per run and use a separate, cheaper model by default.

Every metric returns a float in [0, 1] so a run summary is comparable across
metrics and a regression check is one subtraction.
"""

from __future__ import annotations

import json
import re
from dataclasses import dataclass
from typing import Any, Protocol

import structlog

from zk2.llm.base import LLMProvider
from zk2.llm.base import Message as LLMMessage

logger = structlog.get_logger()

JUDGE_MAX_TOKENS = 300
_CITATION_PATTERN = re.compile(r"\[(\d{1,2})\]")


@dataclass(slots=True)
class EvalSample:
    """One evaluated question: what was asked, retrieved, and answered."""

    question: str
    answer: str
    expected_answer: str | None
    expected_sources: list[str]
    retrieved_sources: list[str]
    context: str


class Metric(Protocol):
    name: str
    needs_judge: bool

    async def score(self, sample: EvalSample, judge: LLMProvider | None, model: str) -> float: ...


# ─── deterministic ────────────────────────────────────────────────────


class RetrievalRecall:
    """Share of the expected sources that retrieval actually surfaced."""

    name = "retrieval_recall"
    needs_judge = False

    async def score(self, sample: EvalSample, judge: LLMProvider | None, model: str) -> float:
        del judge, model
        if not sample.expected_sources:
            return 1.0
        retrieved = {name.lower() for name in sample.retrieved_sources}
        hits = sum(1 for name in sample.expected_sources if name.lower() in retrieved)
        return hits / len(sample.expected_sources)


class CitationRate:
    """Did the answer point at the passages it was given?"""

    name = "citation_rate"
    needs_judge = False

    async def score(self, sample: EvalSample, judge: LLMProvider | None, model: str) -> float:
        del judge, model
        if not sample.context:
            return 0.0
        return 1.0 if _CITATION_PATTERN.search(sample.answer) else 0.0


class AnswerPresence:
    """A non-empty answer that is not a refusal. The floor, not a quality bar."""

    name = "answered"
    needs_judge = False
    _REFUSALS = ("i don't know", "i do not know", "no information", "не знаю", "cannot answer")

    async def score(self, sample: EvalSample, judge: LLMProvider | None, model: str) -> float:
        del judge, model
        text = sample.answer.strip().lower()
        if not text:
            return 0.0
        return 0.0 if any(phrase in text for phrase in self._REFUSALS) else 1.0


# ─── judge ────────────────────────────────────────────────────────────

_JUDGE_SYSTEM = (
    "You are a strict evaluator. Reply with JSON only, in the form "
    '{"score": <0.0-1.0>, "reason": "<one sentence>"}. No other text.'
)


async def _judge_score(judge: LLMProvider, model: str, prompt: str) -> float:
    """Ask the judge and read a score out of its reply."""
    chunks = []
    async for chunk in judge.complete(
        [LLMMessage(role="system", content=_JUDGE_SYSTEM), LLMMessage(role="user", content=prompt)],
        model=model,
        temperature=0.0,
        max_tokens=JUDGE_MAX_TOKENS,
        stream=False,
    ):
        if chunk.delta:
            chunks.append(chunk.delta)
    raw = "".join(chunks).strip()
    return _parse_score(raw)


def _parse_score(raw: str) -> float:
    """Read the score out of a judge reply, tolerating chatty models."""
    candidate = raw
    if "{" in raw:
        candidate = raw[raw.index("{") : raw.rindex("}") + 1] if "}" in raw else raw
    try:
        payload: dict[str, Any] = json.loads(candidate)
        value = float(payload["score"])
    except (json.JSONDecodeError, KeyError, TypeError, ValueError):
        logger.warning("evals.judge_unparsable", reply=raw[:200])
        return 0.0
    return max(0.0, min(1.0, value))


class Faithfulness:
    """Is the answer supported by the passages it was given?"""

    name = "faithfulness"
    needs_judge = True

    async def score(self, sample: EvalSample, judge: LLMProvider | None, model: str) -> float:
        if judge is None or not sample.context:
            return 0.0
        prompt = (
            "Score how well the ANSWER is supported by the CONTEXT. "
            "1.0 means every claim is supported; 0.0 means the answer contradicts "
            "the context or invents facts absent from it.\n\n"
            f"CONTEXT:\n{sample.context}\n\nANSWER:\n{sample.answer}"
        )
        return await _judge_score(judge, model, prompt)


class AnswerRelevancy:
    """Does the answer address the question that was asked?"""

    name = "answer_relevancy"
    needs_judge = True

    async def score(self, sample: EvalSample, judge: LLMProvider | None, model: str) -> float:
        if judge is None:
            return 0.0
        prompt = (
            "Score how directly the ANSWER addresses the QUESTION. Ignore whether "
            "it is factually correct - only whether it answers what was asked.\n\n"
            f"QUESTION:\n{sample.question}\n\nANSWER:\n{sample.answer}"
        )
        return await _judge_score(judge, model, prompt)


class Correctness:
    """Does the answer agree with the reference answer?"""

    name = "correctness"
    needs_judge = True

    async def score(self, sample: EvalSample, judge: LLMProvider | None, model: str) -> float:
        if judge is None or not sample.expected_answer:
            return 0.0
        prompt = (
            "Score how well the ANSWER matches the REFERENCE. Wording may differ; "
            "score the facts. 1.0 means equivalent, 0.0 means contradictory or missing.\n\n"
            f"QUESTION:\n{sample.question}\n\nREFERENCE:\n{sample.expected_answer}\n\n"
            f"ANSWER:\n{sample.answer}"
        )
        return await _judge_score(judge, model, prompt)


ALL_METRICS: tuple[Metric, ...] = (
    RetrievalRecall(),
    CitationRate(),
    AnswerPresence(),
    Faithfulness(),
    AnswerRelevancy(),
    Correctness(),
)

METRICS_BY_NAME: dict[str, Metric] = {metric.name: metric for metric in ALL_METRICS}
DETERMINISTIC_METRICS: tuple[str, ...] = tuple(m.name for m in ALL_METRICS if not m.needs_judge)
JUDGE_METRICS: tuple[str, ...] = tuple(m.name for m in ALL_METRICS if m.needs_judge)
