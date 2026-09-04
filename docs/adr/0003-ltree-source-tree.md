# ADR-0003: Postgres `ltree` for the source tree

- Status: Accepted
- Date: 2026-09-04

## Context

Sources form folders and documents, and retrieval needs "everything under this
folder" on the hot path: a bot points at a directory, and the query must reach
every descendant. The tree is deep-ish, read constantly and written rarely.

## Decision

`sources.path` is an `ltree` whose labels are the ids of the ancestors
(`12.34.56`), with a GiST index. Subtrees come from the `<@` operator, level
filters from `nlevel()`, patterns from `lquery`.

## Consequences

- Subtree retrieval is one indexed predicate instead of a recursive CTE
- Building the nested response is a single ordered scan: rows arrive parents
  first, so children attach as they are read
- `ltree` has no SQLAlchemy type, so the project ships its own (`core/types.py`)
- lquery has sharp edges - `12.*` matches the node itself, strict descendants
  need `12.*{1,}` - which cost us a bug that a test now pins down
- Moving a subtree means rewriting the paths below it; rare enough to accept

## Alternatives considered

- **Adjacency list plus recursive CTE.** Portable, but every retrieval query
  pays for the recursion, and the SQL is harder to read
- **Nested sets.** Fast reads, painful writes
- **Materialised path in a text column.** What ltree already is, minus the
  operators and the index support
