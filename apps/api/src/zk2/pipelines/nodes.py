"""The node registry: every step a pipeline can contain.

A node is a small, typed unit - a config model, a name, and an async generator
that mutates the shared state and may emit protocol events. The registry is
what the editor palette is built from and what validates a saved DAG, so a node
type exists in exactly one place.
"""

from __future__ import annotations

import builtins
import time
from abc import ABC, abstractmethod
from collections.abc import AsyncIterator
from dataclasses import dataclass
from typing import Any, ClassVar

import structlog
from pydantic import BaseModel, Field

from zk2.agents.chat_models import build_chat_model
from zk2.chat.events import StreamEvent
from zk2.core.metrics import llm_errors_total, record_llm_call, record_retrieval
from zk2.llm.base import LLMProvider
from zk2.llm.base import Message as LLMMessage
from zk2.llm.registry import get_embedding_provider, get_llm_provider
from zk2.pipelines.state import NodeContext, PipelineState
from zk2.retrieval.bm25 import bm25_search
from zk2.retrieval.dense import dense_search
from zk2.retrieval.fusion import reciprocal_rank_fusion
from zk2.retrieval.rerank import rerank
from zk2.sources.chunking import count_tokens

logger = structlog.get_logger()

CITATION_INSTRUCTION = (
    "Cite the passages you actually used by their number in square brackets, "
    "like [1] or [2][3], right after the statement they support. Do not cite a "
    "passage you did not use."
)


class Node(ABC):
    """One step of a pipeline."""

    # `type` is the key the saved DAG uses, so the attribute keeps that name -
    # which is why annotations below spell out builtins.type
    type: ClassVar[str]
    title: ClassVar[str]
    description: ClassVar[str]
    config_model: ClassVar[builtins.type[BaseModel]]

    @abstractmethod
    def run(
        self, state: PipelineState, ctx: NodeContext, config: Any
    ) -> AsyncIterator[StreamEvent]:
        """Mutate state; yield protocol events when there is something to stream."""
        ...


# ─── Retrieval ────────────────────────────────────────────────────────


class DenseConfig(BaseModel):
    k: int = Field(20, ge=1, le=200, description="Candidates to fetch")


class DenseRetriever(Node):
    type = "retriever_dense"
    title = "Dense retrieval"
    description = "Vector similarity over pgvector, expanded across folders."
    config_model = DenseConfig

    async def run(
        self, state: PipelineState, ctx: NodeContext, config: DenseConfig
    ) -> AsyncIterator[StreamEvent]:
        if not state.source_ids:
            state.candidates[self.type] = []
            return
        started = time.perf_counter()
        embeddings = await get_embedding_provider(ctx.db, org_id=ctx.org_id)
        vector = await embeddings.embed_query(state.query)
        hits = await dense_search(
            ctx.db,
            org_id=ctx.org_id,
            source_ids=state.source_ids,
            query_embedding=vector,
            embedding_model=embeddings.model,
            k=config.k,
        )
        record_retrieval("dense", duration_seconds=time.perf_counter() - started, results=len(hits))
        state.candidates[self.type] = hits
        return
        yield  # pragma: no cover - makes this an async generator


class Bm25Config(BaseModel):
    k: int = Field(20, ge=1, le=200, description="Candidates to fetch")


class Bm25Retriever(Node):
    type = "retriever_bm25"
    title = "Lexical retrieval"
    description = "Postgres full-text search in the document's own language."
    config_model = Bm25Config

    async def run(
        self, state: PipelineState, ctx: NodeContext, config: Bm25Config
    ) -> AsyncIterator[StreamEvent]:
        if not state.source_ids:
            state.candidates[self.type] = []
            return
        started = time.perf_counter()
        hits = await bm25_search(
            ctx.db,
            org_id=ctx.org_id,
            source_ids=state.source_ids,
            query=state.query,
            k=config.k,
        )
        record_retrieval("bm25", duration_seconds=time.perf_counter() - started, results=len(hits))
        state.candidates[self.type] = hits
        return
        yield  # pragma: no cover


class FusionConfig(BaseModel):
    method: str = Field("rrf", pattern="^rrf$", description="Fusion method")
    k: int = Field(60, ge=1, le=1000, description="RRF smoothing constant")
    limit: int = Field(30, ge=1, le=200, description="Candidates kept after fusion")


class Fusion(Node):
    type = "fusion"
    title = "Fusion (RRF)"
    description = "Merges retriever outputs on rank, not on incomparable scores."
    config_model = FusionConfig

    async def run(
        self, state: PipelineState, ctx: NodeContext, config: FusionConfig
    ) -> AsyncIterator[StreamEvent]:
        del ctx
        lists = list(state.candidates.values())
        state.selected = reciprocal_rank_fusion(lists, k=config.k, limit=config.limit)
        return
        yield  # pragma: no cover


class RerankConfig(BaseModel):
    top_k: int = Field(5, ge=1, le=50, description="Passages kept after reranking")


class Rerank(Node):
    type = "rerank"
    title = "Rerank"
    description = "Cross-encoder scoring of query and passage together."
    config_model = RerankConfig

    async def run(
        self, state: PipelineState, ctx: NodeContext, config: RerankConfig
    ) -> AsyncIterator[StreamEvent]:
        del ctx
        candidates = state.selected or [c for hits in state.candidates.values() for c in hits]
        started = time.perf_counter()
        state.selected = await rerank(state.query, candidates, top_k=config.top_k)
        record_retrieval(
            "rerank",
            duration_seconds=time.perf_counter() - started,
            results=len(state.selected),
        )
        return
        yield  # pragma: no cover


# ─── Context ──────────────────────────────────────────────────────────


class ContextConfig(BaseModel):
    token_budget: int = Field(6000, ge=200, le=200_000, description="Context token budget")
    emit_sources: bool = Field(True, description="Send the sources event to the client")


class ContextBuilder(Node):
    type = "context_builder"
    title = "Context builder"
    description = "Numbers the passages and packs them into the token budget."
    config_model = ContextConfig

    async def run(
        self, state: PipelineState, ctx: NodeContext, config: ContextConfig
    ) -> AsyncIterator[StreamEvent]:
        del ctx
        used_tokens = 0
        for chunk in state.selected:
            marker = len(state.context_chunks) + 1
            snippet = (
                f"[{marker}] source: {chunk.source_name} (chunk {chunk.ordinal})\n{chunk.text}"
            )
            tokens = count_tokens(snippet)
            if used_tokens + tokens > config.token_budget:
                break
            used_tokens += tokens
            state.context_parts.append(snippet)
            state.context_chunks.append(
                {
                    "marker": marker,
                    "chunk_id": chunk.chunk_id,
                    "source_id": chunk.source_id,
                    "name": chunk.source_name,
                    "ordinal": chunk.ordinal,
                    "cited": False,
                }
            )
        if config.emit_sources and state.selected:
            yield StreamEvent(
                "sources",
                {
                    "items": [
                        {
                            "chunk_id": c.chunk_id,
                            "source_id": c.source_id,
                            "name": c.source_name,
                            "ordinal": c.ordinal,
                            "score": round(c.score, 6),
                            "matched_by": list(c.matched_by) or [c.retriever],
                        }
                        for c in state.selected
                    ]
                },
            )


# ─── Generation ───────────────────────────────────────────────────────


class GenerateConfig(BaseModel):
    provider: str | None = Field(None, description="Overrides the bot's provider")
    model: str | None = Field(None, description="Overrides the bot's model")
    temperature: float | None = Field(None, ge=0, le=2)
    max_tokens: int | None = Field(None, ge=1, le=128_000)
    system_prompt: str | None = Field(None, max_length=20_000, description="Overrides the bot's")
    cite_sources: bool = Field(True, description="Ask the model to cite passage numbers")


class Generate(Node):
    type = "generate"
    title = "Generate"
    description = "Streams the answer from the configured provider."
    config_model = GenerateConfig

    async def run(
        self, state: PipelineState, ctx: NodeContext, config: GenerateConfig
    ) -> AsyncIterator[StreamEvent]:
        provider_name = config.provider or ctx.version.llm_provider
        model = config.model or ctx.version.llm_model
        temperature = (
            config.temperature if config.temperature is not None else ctx.version.temperature
        )

        base_prompt = (
            config.system_prompt or ctx.version.system_prompt or "You are a helpful assistant."
        ).strip()
        if state.context_parts:
            base_prompt += (
                "\n\nAnswer using only the numbered passages below. "
                "If they do not contain the answer, say so honestly.\n"
            )
            if config.cite_sources:
                base_prompt += CITATION_INSTRUCTION + "\n"
            base_prompt += "\n----- CONTEXT -----\n" + "\n\n".join(state.context_parts)

        messages = [
            LLMMessage(role="system", content=base_prompt),
            LLMMessage(role="user", content=state.query),
        ]

        llm: LLMProvider = await get_llm_provider(ctx.db, org_id=ctx.org_id, provider=provider_name)
        generation = ctx.trace.step(
            "generation",
            kind="generation",
            input_data=[{"role": m.role, "content": m.content} for m in messages],
            model=model,
            metadata={"provider": provider_name, "temperature": temperature},
        )

        started = time.perf_counter()
        parts: list[str] = []
        try:
            async for chunk in llm.complete(
                messages,
                model=model,
                temperature=temperature,
                max_tokens=config.max_tokens,
                stream=True,
            ):
                if chunk.delta:
                    parts.append(chunk.delta)
                    yield StreamEvent("token", {"delta": chunk.delta})
                if chunk.tokens_in is not None:
                    state.tokens_in = chunk.tokens_in
                if chunk.tokens_out is not None:
                    state.tokens_out = chunk.tokens_out
        except Exception as exc:
            llm_errors_total.labels(provider=provider_name, model=model).inc()
            generation.end(level="ERROR", status_message=str(exc)[:500])
            state.failed = True
            logger.exception("pipeline.generate_failed")
            yield StreamEvent("error", {"message": str(exc)})
            return

        state.latency_ms = int((time.perf_counter() - started) * 1000)
        state.answer = "".join(parts)
        state.cost_usd = llm.estimate_cost(
            model, tokens_in=state.tokens_in, tokens_out=state.tokens_out
        )
        record_llm_call(
            provider=provider_name,
            model=model,
            duration_seconds=state.latency_ms / 1000,
            tokens_in=state.tokens_in,
            tokens_out=state.tokens_out,
            cost_usd=float(state.cost_usd) if state.cost_usd is not None else None,
        )
        generation.end(
            output=state.answer,
            usage_details={"input": state.tokens_in, "output": state.tokens_out},
            cost_details={"total": float(state.cost_usd)} if state.cost_usd is not None else None,
        )


class AgentConfig(BaseModel):
    provider: str | None = Field(None, description="Overrides the bot's provider")
    model: str | None = Field(None, description="Overrides the bot's model")
    temperature: float | None = Field(None, ge=0, le=2)
    max_tokens: int | None = Field(None, ge=1, le=128_000)
    system_prompt: str | None = Field(None, max_length=20_000)
    tools: list[str] = Field(
        default_factory=list, description="Tool names to offer; empty means all available"
    )
    max_steps: int = Field(8, ge=1, le=30, description="Tool-call rounds before giving up")
    use_context: bool = Field(True, description="Include retrieved passages in the prompt")


class Agent(Node):
    type = "agent"
    title = "Agent"
    description = "Model with tools: it decides what to call and when to answer."
    config_model = AgentConfig

    async def run(
        self, state: PipelineState, ctx: NodeContext, config: AgentConfig
    ) -> AsyncIterator[StreamEvent]:
        from zk2.agents.graph import AgentOutcome, stream_agent  # noqa: PLC0415  (heavy import)
        from zk2.agents.registry import tools_for_org  # noqa: PLC0415

        provider = config.provider or ctx.version.llm_provider
        model = config.model or ctx.version.llm_model
        temperature = (
            config.temperature if config.temperature is not None else ctx.version.temperature
        )

        prompt = (
            config.system_prompt or ctx.version.system_prompt or "You are a helpful assistant."
        ).strip()
        if config.use_context and state.context_parts:
            prompt += (
                "\n\nRetrieved passages are below. Prefer them over your own knowledge, "
                "and use tools when they cannot answer the question.\n"
                + CITATION_INSTRUCTION
                + "\n\n----- CONTEXT -----\n"
                + "\n\n".join(state.context_parts)
            )

        tools = await tools_for_org(ctx.db, org_id=ctx.org_id, names=config.tools or None)
        chat_model = await build_chat_model(
            ctx.db,
            org_id=ctx.org_id,
            provider=provider,
            model=model,
            temperature=temperature,
            max_tokens=config.max_tokens,
        )

        async for item in stream_agent(
            chat_model=chat_model,
            tools=tools,
            system_prompt=prompt,
            question=state.query,
            provider=provider,
            model=model,
            max_steps=config.max_steps,
            trace=ctx.trace,
        ):
            if isinstance(item, AgentOutcome):
                state.answer = item.answer
                state.tokens_in = item.tokens_in
                state.tokens_out = item.tokens_out
                state.cost_usd = item.cost_usd
                state.failed = item.failed
                continue
            yield item


@dataclass(frozen=True, slots=True)
class NodeType:
    """What the editor palette needs to render a node type."""

    type: str
    title: str
    description: str
    config_schema: dict[str, Any]


_NODES: tuple[Node, ...] = (
    DenseRetriever(),
    Bm25Retriever(),
    Fusion(),
    Rerank(),
    ContextBuilder(),
    Generate(),
    Agent(),
)

REGISTRY: dict[str, Node] = {node.type: node for node in _NODES}


def node_types() -> list[NodeType]:
    """Palette contents, with a JSON Schema per node for the inspector form."""
    return [
        NodeType(
            type=node.type,
            title=node.title,
            description=node.description,
            config_schema=node.config_model.model_json_schema(),
        )
        for node in _NODES
    ]
