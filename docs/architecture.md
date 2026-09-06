# Architecture

Three C4 levels, then the two paths worth reading in detail: what happens when
someone asks a question, and what happens when they add a document.

## Context

Who talks to the system, and what it talks to.

```mermaid
C4Context
  title System context

  Person(member, "Organization member", "Uploads documents, builds bots, asks questions")
  Person(admin, "Super-admin", "Approves access requests, watches spend")

  System(zk2, "ZK2 RAG Platform", "Multi-tenant RAG platform: sources, pipelines, agents, evals")

  System_Ext(providers, "LLM providers", "OpenAI, Anthropic, Gemini, local Ollama")
  System_Ext(mcp, "MCP servers", "Tools an organization connected")
  System_Ext(smtp, "Mailgun SMTP", "Magic links, invites, admin notifications")
  System_Ext(obs, "Observability stack", "Prometheus, Grafana, Jaeger, Langfuse, Sentry")

  Rel(member, zk2, "Uses", "HTTPS, WebSocket")
  Rel(admin, zk2, "Administers", "HTTPS")
  Rel(zk2, providers, "Completions, embeddings", "HTTPS")
  Rel(zk2, mcp, "Lists and calls tools", "HTTPS")
  Rel(zk2, smtp, "Sends mail", "SMTP/TLS")
  Rel(zk2, obs, "Metrics, traces", "HTTP/OTLP")
```

Access is invite-only by design: there is no self-signup endpoint, so the only
way in is an approved access request (ADR-0002).

## Containers

```mermaid
C4Container
  title Containers

  Person(user, "Member")

  Container_Boundary(zk2, "ZK2 RAG Platform") {
    Container(web, "Web app", "Next.js 15, TypeScript", "Workspace, pipeline editor, evals, A/B console")
    Container(api, "API", "FastAPI, Python 3.12", "Auth, sources, chat over WebSocket, pipelines, evals")
    Container(worker, "Worker", "Arq", "Ingestion, eval runs, MCP health checks")
    ContainerDb(pg, "PostgreSQL 16", "pgvector, tsvector, ltree", "Everything: documents, vectors, versions, usage")
    ContainerDb(redis, "Redis 7", "", "Sessions, rate limits, job queue, quota counters")
    ContainerDb(objects, "Object storage", "S3 or local disk", "Uploaded files")
  }

  System_Ext(providers, "LLM providers")

  Rel(user, web, "Uses", "HTTPS")
  Rel(web, api, "REST and WebSocket", "HTTPS")
  Rel(api, pg, "Reads and writes", "asyncpg")
  Rel(api, redis, "Sessions, limits, enqueue", "RESP")
  Rel(api, objects, "Stores uploads")
  Rel(api, providers, "Streams completions")
  Rel(worker, pg, "Writes chunks and vectors")
  Rel(worker, redis, "Takes jobs")
  Rel(worker, providers, "Embeddings")
```

One database holds documents, vectors, full-text indexes, versions and usage.
pgvector and tsvector next to the rows they describe means retrieval is a join,
not a distributed transaction across two systems (ADR-0003 covers the tree).

## Components inside the API

```mermaid
C4Component
  title API components

  Container_Boundary(api, "API") {
    Component(auth, "auth", "JWT, magic links, RBAC", "Invite-only access, sessions, lockout")
    Component(sources, "sources", "Loaders, chunking, ingest", "Documents in, chunks and vectors out")
    Component(retrieval, "retrieval", "dense, bm25, fusion, rerank", "Finds passages")
    Component(pipelines, "pipelines", "DAG runtime", "Executes the graph a bot runs")
    Component(agents, "agents", "LangGraph, tools, MCP", "The agent node's loop")
    Component(llm, "llm", "Catalog, provider adapters", "One interface over four providers")
    Component(evals, "evals", "Metrics, runner", "Scores a golden set")
    Component(ab, "ab", "Assignment, analytics", "Splits traffic between versions")
    Component(core, "core", "db, quota, metrics, tracing, net", "Cross-cutting machinery")
  }

  Rel(pipelines, retrieval, "Retriever nodes call")
  Rel(pipelines, agents, "Agent node calls")
  Rel(pipelines, llm, "Generate node calls")
  Rel(agents, llm, "Builds a chat model from")
  Rel(evals, pipelines, "Runs the real graph")
  Rel(ab, pipelines, "Picks the version to run")
  Rel(sources, llm, "Embeddings")
```

## A question, end to end

```mermaid
sequenceDiagram
  autonumber
  participant U as Browser
  participant A as API (WebSocket)
  participant AB as A/B
  participant P as Pipeline runtime
  participant DB as Postgres
  participant L as Provider

  U->>A: {type: auth, token, org_id}
  A->>DB: verify user and membership
  A-->>U: {type: ready}
  U->>A: {type: user_message}
  A->>AB: running experiment for this bot?
  AB-->>A: variant + pipeline version (or none)
  A->>P: execute DAG
  P->>DB: dense search (pgvector)
  P->>DB: lexical search (tsvector, source language)
  P->>P: RRF fusion, then rerank
  P-->>U: {type: sources}
  P->>L: stream completion with numbered passages
  L-->>P: tokens
  P-->>U: {type: token} ...
  A->>A: parse [n] citations out of the answer
  A-->>U: {type: citations}
  A->>DB: message, usage_event (tokens, cost, key source)
  A-->>U: {type: done, cost, trace_url}
```

The token expiry is re-checked before every turn, because a socket outlives the
15-minute access token that opened it.

## A document, end to end

```mermaid
sequenceDiagram
  autonumber
  participant U as Browser
  participant A as API
  participant S as Object storage
  participant Q as Redis queue
  participant W as Worker
  participant DB as Postgres

  U->>A: upload file (size-capped, type-checked)
  A->>S: store bytes
  A->>DB: source row, status=pending
  A->>Q: enqueue ingest
  A-->>U: 201, the tree polls for status
  W->>Q: take job
  W->>S: read bytes
  W->>W: extract text, detect language
  W->>W: parse into sections and pages, pack to the chunk budget
  W->>DB: chunks
  W->>W: embeddings in batches
  W->>DB: vectors + tsvector in the document's language
  W->>DB: usage_event (embedding tokens, key source)
  W->>DB: status=ready
```

A URL source takes the same path, with an SSRF-guarded fetch in place of the
storage read: DNS is resolved and checked before the request, and every
redirect hop is checked again.

## Where the numbers live

| Question | Answer comes from |
|---|---|
| Is the service healthy, how slow is it? | Prometheus, `/metrics`, Grafana dashboard |
| Where did this request's latency go? | OpenTelemetry spans in Jaeger, correlated by `request_id` |
| What did the model see and produce? | Langfuse trace, linked from the chat message |
| What did this organization spend? | `usage_events` in Postgres, `/observability/usage` |
| Did this pipeline change make answers worse? | Eval runs and their comparison |

Metric labels never carry tenant identity - per-organization accounting is a
database question, not a PromQL one (ADR-0010).
