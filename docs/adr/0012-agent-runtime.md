# ADR-0012: LangGraph for the agent loop, with accounting kept in-project

- Status: Accepted
- Date: 2026-09-05

## Context

ADR-0011 deferred the LangGraph question to the agent slice. That slice needs
what LangGraph is actually for: a cycle between a model and its tools, with the
model deciding when to stop.

The objection raised at the time was concrete. LangGraph runs on LangChain
model objects, and this project already has its own provider layer - per-org
encrypted keys, a model catalog with prices and capability flags, cost recorded
per call, Prometheus metrics, Langfuse spans. Adopting LangChain models
wholesale would route agent turns around all of that: an agent conversation
would be the one kind of traffic nobody could bill or measure.

## Decision

LangGraph runs the loop. Everything it does not know about stays in-project:

- the LangChain model is **constructed here** (`agents/chat_models.py`) from the
  organization's stored key and the catalog, which decides the output cap and
  whether the model accepts sampling parameters at all
- token usage is read back off the returned `AIMessage.usage_metadata`, priced
  through the same catalog as a plain completion, and pushed into the same
  `record_llm_call` metrics and the same Langfuse span
- tools are described in project types (`ToolSpec`) and converted to LangChain
  structures only at graph construction, so MCP tools and built-ins are the
  same thing to everything above the adapter

## Consequences

- An agent turn is billed and measured exactly like a non-agent turn; there is
  one cost path, not two
- The framework is confined to `agents/graph.py`; replacing it would not touch
  the tools, the provider layer, or the pipeline node
- Tool calling is wired for OpenAI and Anthropic. Gemini and Ollama support
  tools in their own APIs, but the LangChain adapters for them are not wired
  here yet - the catalog's `supports_tools` and an explicit error say so
- Two dependency trees now describe models: ours and LangChain's. They agree
  because the catalog is consulted before the LangChain object is built, and a
  test asserts the usage/cost path end to end with a fake tool-calling model
- LangGraph deprecated `create_react_agent` in favour of `langchain.agents.create_agent`;
  this code uses the current entry point

## Alternatives considered

- **Own loop over the existing LLMProvider** (recommended at the time): ~80
  lines, no new dependency tree, nothing to keep in sync. Rejected in favour of
  the recognisable framework named in the project's stack
- **LangChain end to end**, replacing the provider layer: would delete the
  catalog's role in cost accounting and the per-org key handling
