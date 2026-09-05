# Architecture Decision Records

Short records of decisions that were not obvious, written when the decision was
made rather than reconstructed later. Format: context, decision, consequences,
and the alternatives that were actually considered.

| # | Decision | Status |
|---|----------|--------|
| [0001](0001-async-python-stack.md) | Async Python stack (FastAPI + asyncpg + SQLAlchemy 2.0) | Accepted |
| [0002](0002-invite-only-access.md) | Invite-only access, no self-signup | Accepted |
| [0003](0003-ltree-source-tree.md) | Postgres `ltree` for the source tree | Accepted |
| [0004](0004-embedding-dimensions.md) | Every embedding model normalised to 1536 dimensions | Accepted |
| [0005](0005-own-auth.md) | Own authentication instead of a hosted identity provider | Accepted |
| [0006](0006-bot-version-snapshots.md) | Bot versions stored as full snapshots | Accepted |
| [0007](0007-encrypted-provider-keys.md) | Provider API keys encrypted with Fernet in the database | Accepted |
| [0008](0008-arq-over-celery.md) | Arq for background jobs instead of Celery | Accepted |
| [0009](0009-process-singletons.md) | Lazily initialised process-wide resource singletons | Accepted |
| [0010](0010-metric-cardinality.md) | Metric labels exclude tenant identity | Accepted |
| [0011](0011-pipeline-runtime.md) | A topological executor rather than LangGraph for pipelines | Accepted |
| [0012](0012-agent-runtime.md) | LangGraph for the agent loop, with accounting kept in-project | Accepted |
