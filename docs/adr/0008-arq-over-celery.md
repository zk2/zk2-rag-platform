# ADR-0008: Arq for background jobs instead of Celery

- Status: Accepted
- Date: 2026-09-04

## Context

Ingestion - download, extract, chunk, embed - takes seconds to minutes and must
not run inside a request. The application is fully async (ADR-0001) and already
depends on Redis for sessions and rate limiting.

## Decision

Arq. Jobs are plain coroutines, the worker is an asyncio loop, and Redis is the
only broker. Job enqueueing shares the application's Redis pool.

## Consequences

- Job code is the same async code as the request path: the ingest pipeline is
  called directly by tests without a broker
- No extra infrastructure beyond the Redis we already run
- A smaller ecosystem than Celery: no built-in chords or complex canvases, and
  scheduling is basic. Nothing on the roadmap needs them
- Retries and backoff are configured per job rather than inherited from a large
  default policy

## Alternatives considered

- **Celery.** The mature option, but its async support is bolted on, it wants
  its own worker model, and every ingest coroutine would need a sync shim
- **Dramatiq.** Similar trade-offs to Celery, same sync-first assumption
- **FastAPI BackgroundTasks.** Same process as the API, no retries, no
  visibility, lost on deploy
