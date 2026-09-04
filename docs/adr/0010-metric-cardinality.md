# ADR-0010: Metric labels exclude tenant identity

- Status: Accepted
- Date: 2026-09-04

## Context

The metric list drafted early in the project included
`zk2_llm_cost_usd_total{provider,model,org}`. Per-organization spend is a real
question and someone will ask it on day one of billing.

But a Prometheus label creates one time series per distinct value, and that
series is stored, indexed and kept until retention expires - including for
organizations that ran a single query a year ago and never came back. Multiply
by provider and model and the series count grows with the customer list rather
than with the workload.

## Decision

Metric labels stay bounded by things the code enumerates, not by things the
customer list enumerates:

- HTTP metrics carry the **route template** (`/sources/{source_id}`), never the
  resolved path, and unmatched requests collapse into `route="unmatched"`
- LLM metrics carry provider and model only
- retrieval metrics carry the stage name
- ingest metrics carry the terminal status

Per-organization accounting lives in `usage_events`: one row per call, with
org, bot, conversation, tokens and cost. A SQL query groups it by anything,
retains as long as the database does, and is the same data billing will invoice
from. `/observability/usage` serves exactly that.

## Consequences

- Cardinality is a function of the code, so a Grafana dashboard cannot be
  broken by signing up a customer
- "Cost by organization" is a database question, not a PromQL one - the answer
  is more accurate there anyway, because it is the billing source of truth
- A per-tenant alert (one org burning budget) needs a job over `usage_events`
  rather than a Prometheus rule; that is on the billing slice

## Alternatives considered

- **Include `org` and rely on retention.** Series count still tracks signups,
  and Prometheus does not drop labels on old data any faster than the rest
- **Recording rules that aggregate org away.** The raw series still has to be
  ingested and stored first, which is the expensive part
