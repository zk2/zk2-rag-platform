# Load testing

`locustfile.py` describes two user types: a signed-in user browsing their
workspace, and anonymous traffic hitting health and the access-request form.

```bash
pip install locust
export LOAD_EMAIL=... LOAD_PASSWORD=...
locust -f infra/load/locustfile.py --host http://localhost:8000
# headless, for CI or a quick check:
locust -f infra/load/locustfile.py --host http://localhost:8000 \
       --headless --users 50 --spawn-rate 5 --run-time 2m
```

## What to watch while it runs

Not the Locust numbers alone - the point of having metrics is to read them
under load:

- `zk2_http_request_duration_seconds` p95 by route, in Grafana
- `zk2_ingest_queue_depth`: if it climbs, the workers are the bottleneck
- database connections against `DB_POOL_SIZE` plus `DB_MAX_OVERFLOW`
- `zk2_llm_*` should stay near zero here: this profile does not call providers

## What it does not cover

Chat itself. A turn's latency is dominated by the provider, so loading it
measures the provider's queue rather than this service, and it spends real
money doing so. Load the retrieval path instead by pointing a bot at a local
Ollama model and driving the WebSocket with a tool that speaks it.
