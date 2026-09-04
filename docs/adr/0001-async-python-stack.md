# ADR-0001: Async Python stack

- Status: Accepted
- Date: 2026-09-04

## Context

The predecessor (`dpb-chatbot`) ran on synchronous psycopg2 with no connection
pool. Under concurrent chat sessions, every LLM call - hundreds of milliseconds
to several seconds of pure waiting - blocked a worker thread, and each request
opened its own database connection. Streaming responses to several users at once
was the workload the design was worst at.

## Decision

FastAPI on uvicorn, SQLAlchemy 2.0 in async mode over asyncpg, one shared
`AsyncEngine` with a bounded pool, Alembic for migrations, Arq for background
work. Every I/O path in the application is async: database, HTTP clients, SMTP,
Redis.

## Consequences

- A single worker process handles many concurrent streaming sessions, because
  waiting on an LLM costs a coroutine, not a thread
- Connections are pooled and pre-pinged instead of opened per request
- Blocking libraries must be wrapped in `asyncio.to_thread` (`usp` for sitemaps,
  sentence-transformers later); forgetting to do so stalls the whole loop
- Tests need an async harness: pytest-asyncio, and process-wide singletons must
  be disposed between tests because they bind to the loop that created them
  (see ADR-0009)

## Alternatives considered

- **Sync FastAPI with a thread pool.** Simpler, but the thread count becomes the
  concurrency limit for exactly the workload this project is about
- **Django + Channels.** More batteries, but the ORM's async story is partial
  and the project needs raw SQL for pgvector and ltree anyway
