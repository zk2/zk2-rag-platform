"""Executing a saved DAG.

Nodes run in topological order, each one mutating the shared state and
optionally streaming events to the client. Timings are collected per node, so
a slow pipeline can be blamed on the node that deserves it rather than on
"retrieval" as a whole.

Deliberately not LangGraph: a RAG pipeline is an acyclic graph of a handful of
steps with no model-decided branching, and a topological walk is the whole of
what it needs. LangGraph earns its place where agent loops do - see ADR-0011.
"""

from __future__ import annotations

import time
from collections.abc import AsyncIterator
from dataclasses import dataclass, field

import structlog

from zk2.chat.events import StreamEvent
from zk2.core.errors import AppError, ValidationError
from zk2.pipelines.dag import DagSpec, NodeSpec, topological_order
from zk2.pipelines.nodes import REGISTRY
from zk2.pipelines.state import NodeContext, PipelineState

logger = structlog.get_logger()


@dataclass(slots=True)
class NodeTiming:
    node_id: str
    node_type: str
    duration_ms: int


@dataclass(slots=True)
class RunReport:
    timings: list[NodeTiming] = field(default_factory=list)

    def as_dict(self) -> list[dict[str, object]]:
        return [
            {"node_id": t.node_id, "type": t.node_type, "duration_ms": t.duration_ms}
            for t in self.timings
        ]


def validate_dag(dag: DagSpec) -> list[str]:
    """Return the execution order, raising ValidationError on anything wrong."""
    order = topological_order(dag)
    if not order:
        raise ValidationError("Pipeline has no nodes")

    for spec in order:
        node = REGISTRY.get(spec.type)
        if node is None:
            raise ValidationError(f"Unknown node type: {spec.type}")
        try:
            node.config_model.model_validate(spec.config)
        except Exception as exc:
            raise ValidationError(f"Invalid config for node {spec.id}: {exc}") from exc

    if not any(spec.type == "generate" for spec in order):
        raise ValidationError("Pipeline must end with a generate node")
    # Before the position check: a graph with a loose end usually fails that
    # one too, and "the generate node must be last" is a puzzling way to be
    # told that an edge is missing.
    _check_connected(dag, order)
    if order[-1].type != "generate":
        raise ValidationError("The generate node must be last: nothing may run after the answer")
    return [spec.id for spec in order]


def _check_connected(dag: DagSpec, order: list[NodeSpec]) -> None:
    """Refuse a graph whose pieces are not joined up.

    Removing a node in the editor takes its edges with it, and the result saves
    without complaint: a context builder with nothing feeding it produces an
    empty context, the generate node answers from the model alone, and the run
    completes with metrics that look like an answer. A quiet wrong result is
    worse than a loud refusal, so both loose ends are named here.
    """
    has_incoming = {target for _, target in dag.edges}
    has_outgoing = {source for source, _ in dag.edges}

    for spec in order:
        if REGISTRY[spec.type].terminal and spec.id in has_outgoing:
            raise ValidationError(
                "The generate node must be last: nothing may run after the answer"
            )

    for spec in order:
        node = REGISTRY[spec.type]
        if node.reads_upstream and spec.id not in has_incoming:
            raise ValidationError(
                f"Nothing feeds {spec.id}: a {node.title.lower()} node has no results to work on"
            )
        if not node.terminal and spec.id not in has_outgoing:
            raise ValidationError(f"Nothing reads {spec.id}: its output goes nowhere")


async def execute(
    dag: DagSpec, state: PipelineState, ctx: NodeContext, *, report: RunReport | None = None
) -> AsyncIterator[StreamEvent]:
    """Run a pipeline, streaming whatever its nodes emit."""
    order = topological_order(dag)
    for spec in order:
        node = REGISTRY.get(spec.type)
        if node is None:
            raise ValidationError(f"Unknown node type: {spec.type}")
        config = node.config_model.model_validate(spec.config)

        step = ctx.trace.step(spec.id, kind="span", metadata={"node_type": spec.type})
        ctx.step = step
        started = time.perf_counter()
        # Any node can fail on something outside the pipeline's control - a
        # provider with no credits left, a database hiccup. That is a failed
        # run with a reason attached, the way the generate node has always
        # reported its own failures, and never a 500 for the whole request.
        failure: str | None = None
        try:
            async for event in node.run(state, ctx, config):
                yield event
        except AppError as exc:
            failure = exc.message
        except Exception as exc:  # the reason is reported to the caller, not swallowed
            failure = str(exc) or exc.__class__.__name__

        duration_ms = int((time.perf_counter() - started) * 1000)
        if failure is None:
            step.end(node_type=spec.type, duration_ms=duration_ms)
        else:
            step.end(level="ERROR", status_message=failure[:500])

        if report is not None:
            report.timings.append(NodeTiming(spec.id, spec.type, duration_ms))
        logger.debug("pipeline.node", node=spec.id, type=spec.type, ms=duration_ms)

        if failure is not None:
            state.failed = True
            logger.warning("pipeline.node_failed", node=spec.id, type=spec.type, error=failure)
            yield StreamEvent("error", {"message": failure})
            return

        if state.failed:
            logger.warning("pipeline.aborted", node=spec.id)
            return
