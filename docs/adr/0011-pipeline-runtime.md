# ADR-0011: A topological executor rather than LangGraph for pipelines

- Status: Accepted
- Date: 2026-09-04

## Context

The plan pinned "DAG in JSON -> LangGraph" for the pipeline constructor. When
the slice arrived, the graph the editor actually produces turned out to be
narrow: six to ten nodes, no cycles, no model-decided branching, one terminal
node that streams. Retrieval, fusion, reranking, context assembly, generation.

LangGraph exists for the shape that this is not: loops between an agent and its
tools, conditional edges the model chooses at runtime, checkpointed state across
interruptions.

## Decision

Pipelines execute on a small in-project runtime: nodes are typed objects with a
pydantic config model and an async generator `run`, the DAG is validated and
walked in topological order, and each node mutates a shared `PipelineState`
while optionally streaming protocol events.

LangGraph is not adopted here. The agent slice, which needs agent<->tools loops,
is where it gets evaluated on its own merits.

## Consequences

- Roughly 150 lines of runtime, fully typed, tested directly - no translation
  layer between a saved graph and a foreign state model
- Node configs are pydantic models, so the JSON Schema the editor's inspector
  renders comes from the same definition the runtime validates against. A node
  type exists in exactly one place
- The default RAG path is expressed as a DAG in this vocabulary, so a bot with
  a custom pipeline and a bot without run the same code. The default cannot
  drift away from what the editor produces
- Parallel branches execute sequentially. They share one AsyncSession, which
  cannot serve two queries at once anyway
- Anything needing cycles or runtime branching does not fit this runtime, and
  should not be forced into it

## Alternatives considered

- **LangGraph now**, as planned. Matches the README's stack claim, but adds
  langgraph and langchain-core plus adapter code, to run an acyclic graph
- **Own runtime with a LangGraph-shaped node interface**, to swap executors
  later. An abstraction for a future that may not arrive; the node interface is
  small enough to port if it does
