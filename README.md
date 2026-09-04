# zk2-chatbot

Production-grade multi-tenant RAG platform with agents, visual pipeline builder, evals and full observability.

> **Status:** in active development.

## What this demonstrates

- **Production-grade async Python** — FastAPI, asyncpg, SQLAlchemy 2.0 async, OpenTelemetry, testcontainers
- **Modern Next.js 15** — App Router, RSC, streaming, type-safe API client (OpenAPI codegen)
- **Security expertise** — own JWT+OAuth+TOTP auth, RBAC, audit log, encrypted secrets, rate limiting, brute-force protection, invite-only access control
- **RAG mastery** — hybrid retrieval (BM25 + dense + RRF), BGE cross-encoder reranking, multi-provider abstraction
- **LLM agents** — LangGraph orchestration, tool use, MCP protocol, multimodal (vision/voice)
- **Visual pipeline builder** — drag-n-drop DAG editor, versioning, diff, A/B experimentation
- **Observability** — OpenTelemetry, Langfuse, Prometheus, Grafana, Sentry
- **DevOps** — multistage Docker, GitHub Actions CI/CD, Helm chart, k8s manifests, Terraform IaC

## Tech stack

| Layer | Tech |
|-------|------|
| Backend | Python 3.12, FastAPI, SQLAlchemy 2.0 async, asyncpg, Alembic, Arq, Pydantic v2 |
| Frontend | Next.js 15 (App Router), TypeScript, Tailwind v4, shadcn/ui, TanStack Query, React Flow |
| Data | PostgreSQL 16 + pgvector + tsvector, Redis 7 |
| LLM | OpenAI, Anthropic Claude, Google Gemini, Ollama / vLLM |
| Embeddings | OpenAI, Voyage, BGE-M3, Nomic |
| Reranker | BGE-reranker-v2-m3 (local) |
| Orchestration | LangGraph + MCP |
| Observability | OpenTelemetry, Langfuse, Prometheus, Grafana, Sentry |
| Infra | Docker, docker-compose, Helm, k8s, Terraform |
| CI/CD | GitHub Actions |

## Quick start

Prerequisites: Docker + Compose v2, `make`, Node 22+, `pnpm` 9+, `uv` (uv pulls Python 3.12 itself).

```bash
cp .env.example .env
# fill in: APP_SECRET_KEY, DB_PASSWORD, SUPER_ADMIN_EMAIL, SUPER_ADMIN_PASSWORD
# for local dev, point SMTP at the bundled MailHog:
#   SMTP_SERVER=localhost  SMTP_PORT=1025  SMTP_USE_SSL=false  EMAIL_PASSWORD=

make check            # pre-flight: verify uv, pnpm, docker, ... are installed
make install          # install api (uv) and web (pnpm) deps
make up               # start postgres, redis, mailhog, grafana, jaeger, prometheus
make migrate          # apply database migrations
make seed             # create super-admin from .env
make dev-api          # run API (terminal 1) — http://localhost:8000
make dev-web          # run web (terminal 2) — http://localhost:3000
```

Observability is wired for local use: metrics at
<http://localhost:8000/metrics>, Grafana with a provisioned dashboard at
<http://localhost:3001>, traces in Jaeger at <http://localhost:16686>, LLM
traces in Langfuse at <http://localhost:3030>. Prometheus runs in Docker and
reaches the host through `host-gateway`, so start the API with
`make dev-api API_HOST=0.0.0.0` when you want it scraped - the default binds to
loopback only. If the Prometheus target still shows down, the host firewall is
blocking the Docker bridge: on a stock Debian/nftables host, containers cannot
open connections back to host ports until a rule allows it. Everything else in
the stack works regardless; only the scrape needs that hole.

Open <http://localhost:3000>, sign in as super-admin from `.env`.
Outgoing emails are caught by MailHog at <http://localhost:8025>.

## Project structure

```
zk2-chatbot/
├── apps/
│   ├── api/             # FastAPI backend
│   └── web/             # Next.js frontend
├── packages/
│   ├── shared-types/    # OpenAPI → TS codegen
│   └── ui-config/       # Tailwind preset
├── infra/
│   ├── docker/          # Dockerfiles
│   ├── compose/         # docker-compose stacks
│   ├── helm/            # Helm chart
│   ├── k8s/             # raw manifests
│   └── terraform/       # AWS EKS example
├── docs/
│   ├── architecture.md  # C4 diagrams
│   └── adr/             # Architecture Decision Records
└── .github/workflows/   # CI/CD
```

## Documentation

- [`docs/architecture.md`](docs/architecture.md) — C4 architecture diagrams (TBD)
- [`docs/adr/`](docs/adr/) — Architecture Decision Records (TBD)

## License

MIT (TBD).
