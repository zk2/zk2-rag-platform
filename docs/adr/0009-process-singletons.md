# ADR-0009: Lazily initialised process-wide resource singletons

- Status: Accepted
- Date: 2026-09-04

## Context

The database engine, the Redis client, the Arq pool and the storage backend are
process-wide resources with pools of their own. They must be created once, after
settings are known, and shared by request handlers, WebSocket sessions and
worker jobs alike.

## Decision

Each lives in a module-level variable behind a `get_*()` accessor that creates it
on first use, plus a `dispose/close` used by the application lifespan and by
tests. The `global` statement is the mechanism, and ruff's PLW0603 is silenced
for exactly these four modules with a per-file ignore rather than scattered
`noqa` comments.

## Consequences

- One pool per process; nothing is created at import time, so settings and test
  containers can be configured first
- Tests must dispose them between cases: an asyncpg pool is bound to the event
  loop that created it, and pytest-asyncio gives every test a fresh loop. The
  autouse fixture that disposes them exists because of this
- The accessors are the seam a dependency-injection container would replace if
  the codebase ever needs multiple configurations in one process

## Alternatives considered

- **Passing resources explicitly everywhere.** Purer, but noisy across every
  worker entry point and WebSocket handler for no practical gain
- **A DI container (dependency-injector, svcs).** Real benefits at a larger
  size; today it would add a layer to hide four variables
