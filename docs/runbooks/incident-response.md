# Incident response

## First five minutes

1. **What is the symptom?** Chat failing, uploads stuck, everything down, or
   one organization affected. The answer changes everything below.
2. **Look at the dashboard** (Grafana, "zk2 API overview"): request rate, error
   rate by route, p95 latency, provider errors, queue depth.
3. **Check the obvious dependencies:**
   ```bash
   curl -sf https://<host>/api/health        # process
   curl -sf https://<host>/api/health/db     # Postgres
   curl -sf https://<host>/api/health/redis  # Redis
   ```
4. **Find the requests**, not the guesses: every log line carries `request_id`
   and, when tracing is on, `trace_id`. Pull one failing request's id from the
   response header `X-Request-Id` and search the logs for it.

## Symptom: chat answers fail, everything else works

Likely a provider, not us. Check in this order:

- `zk2_llm_errors_total` by provider and model on the dashboard
- the provider's own status page
- **the shared-key allowance**: an organization that ran out gets a 403 with
  code `system_key_limit_reached`. That is the system working as designed - the
  answer is for them to add their own key, or for you to raise
  `SYSTEM_KEY_TOKEN_LIMIT`
- a model removed from the catalog: unknown models still run, but their cost is
  recorded as unknown, and `catalog.unknown_model` shows up in the logs

## Symptom: uploads stay "pending"

The worker is not consuming. In order:

```bash
kubectl get pods -l app.kubernetes.io/component=worker
kubectl logs -l app.kubernetes.io/component=worker --tail=100
```

- `zk2_ingest_queue_depth` climbing with no `zk2_ingest_documents_total`
  movement means no worker is running or all of them are stuck on one document
- an individual document that fails is not an incident: its row carries the
  error, visible in the tree and in `sources.error`
- if the queue is deep but healthy, scale the worker deployment; ingest is
  embarrassingly parallel

## Symptom: everything is slow

- p95 by route tells you whether it is one endpoint or all of them
- `zk2_retrieval_duration_seconds` by stage separates the retrieval half from
  the provider half. `rerank` slow means the cross-encoder is loaded on a pod
  that should not have it
- database connections against `DB_POOL_SIZE + DB_MAX_OVERFLOW`: exhaustion
  looks like latency everywhere at once
- Jaeger for one slow request end to end

## Symptom: a flood of 429s

Either the global ceiling (`RATE_LIMIT_GLOBAL_PER_MIN`) or a lockout. Check
whether one address is responsible - if it is a legitimate integration, raise
the ceiling for the deployment rather than removing it.

`TRUST_PROXY_HEADERS` must be true behind a proxy and false without one. Wrong
either way, per-IP limits either apply to the proxy or can be spoofed away.

## Rolling back

The Helm release is the unit of rollback, and the migration hook is the thing
to think about:

```bash
helm history zk2
helm rollback zk2 <revision>
```

A rollback does **not** undo a migration. If the previous version cannot read
the new schema, restore from a snapshot instead - see
[backup-restore.md](backup-restore.md). Migrations in this project are additive
by habit for exactly this reason.

## After

Write down what the signal was and whether the dashboard showed it. A metric
that would have caught the incident and did not exist is the most valuable
thing an incident produces.
