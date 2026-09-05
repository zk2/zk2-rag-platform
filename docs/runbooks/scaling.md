# Scaling

## What actually saturates

The API is latency-bound, not CPU-bound: a chat turn spends most of its life
waiting for a provider. That shapes everything here.

| Symptom | Bottleneck | Action |
|---|---|---|
| p95 up across all routes | Database connections | Raise `DB_POOL_SIZE`, or add read capacity |
| p95 up on chat only | Provider | Nothing to scale here - check the provider's status |
| `zk2_ingest_queue_depth` climbing | Workers | Scale the worker deployment |
| CPU high on the API | Reranking on the API pod | Move it to the worker, or turn it off |
| Memory climbing on the worker | Document parsing, model loading | Raise the limit, or split large uploads |

## The API

HPA on CPU, with a low target because CPU is not the constraint - it is a proxy
for "this pod is holding many connections":

```yaml
api:
  autoscaling:
    enabled: true
    minReplicas: 3
    maxReplicas: 20
    targetCPUUtilizationPercentage: 60
```

Scale-down is deliberately slow (a five-minute stabilization window). A pod
removed mid-turn cuts off a streaming answer that a user is reading.

## Workers

Ingest is embarrassingly parallel - one document, one job, no coordination. If
the queue is deep, add workers:

```bash
kubectl scale deploy zk2-worker --replicas=6
```

Watch two things: the embedding provider's rate limit (the point where more
workers stop helping) and `DB_POOL_SIZE`, since every worker holds connections.

## The database

Postgres is the first thing to run out. In order of cheapness:

1. **Indexes**: retrieval is a partial HNSW index on 1536-dimension vectors and
   a GIN index on tsvector. Check they are being used (`EXPLAIN ANALYZE`) before
   buying hardware
2. **Instance size**: vector search is memory-hungry; an index that fits in RAM
   behaves entirely differently from one that does not
3. **Read replicas** for analytics: `/observability/usage` and A/B stats are
   aggregations over `usage_events` and belong on a replica once that table
   grows
4. **Partitioning `usage_events`** by month, when it gets large. It is
   append-only and always queried by time window, which is the easy case

## Reranking

The cross-encoder is the one CPU-heavy thing here. It is off by default. When
it is on, keep it where the CPU is: `RERANK_ENABLED=true` on workers doing
evals, off on the API pods serving chat - unless answer quality is worth the
latency there too.

## Load testing before you need to know

`infra/load/locustfile.py` drives the HTTP paths. Run it against staging with
production-shaped data - vector search over a hundred chunks tells you nothing
about vector search over a million.
