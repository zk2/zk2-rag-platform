# Deploying to a single host with compose

The Helm chart and the Terraform example describe a cluster. This runbook is
the other deployment this project actually has: one machine, one compose file,
`infra/compose/docker-compose.prod.yml`.

Use it when the target is a VPS or a container on somebody's hypervisor and a
Kubernetes control plane would be more moving parts than the thing it runs.

## What the stack looks like

```
        host proxy (TLS)
               |
        :80  caddy ──────── /            -> web:3000   (Next.js standalone)
                        └── /api/*       -> api:8000   (prefix stripped)
                                            /api/metrics answered 404
  api ─┬─ postgres  (pgvector, named volume)
       ├─ redis     (append-only, named volume)
       └─ uploads   (named volume, shared with the worker)
  worker  same image, `arq zk2.jobs.WorkerSettings`
```

Everything shares one origin, which is why the app's CSP can stay at
`connect-src 'self'` and the chat socket needs no second hostname. Only Caddy
publishes a port. The observability UIs bind to loopback and are reached over
an SSH tunnel; Postgres and Redis publish nothing at all.

TLS is not terminated here - the proxy in front of the host does it. Caddy is
configured with `auto_https off` and never asks for a certificate.

## Prerequisites on the host

Docker Engine with the compose plugin, and git. On AlmaLinux 9 or any other
EL9:

```bash
sudo dnf -y install dnf-plugins-core git
sudo dnf config-manager --add-repo https://download.docker.com/linux/centos/docker-ce.repo
sudo dnf -y install docker-ce docker-ce-cli containerd.io docker-buildx-plugin docker-compose-plugin
sudo systemctl enable --now docker
sudo usermod -aG docker "$USER"   # log out and back in for this to take effect
```

If the host is an LXC or Incus container rather than a VM, Docker needs the
container to allow nesting. That is set on the hypervisor, not inside:

```bash
incus config set <container> security.nesting true
incus restart <container>
```

## First deployment

```bash
git clone <repo> zk2 && cd zk2
cp .env.prod.example .env.prod
chmod 600 .env.prod
```

Fill in every line marked REQUIRED. The ones without a default that matters:

| Variable | Why it cannot be skipped |
|---|---|
| `APP_SECRET_KEY` | Signs tokens and encrypts provider keys. Rotating it later invalidates every stored key - see [key-rotation.md](key-rotation.md) |
| `DB_PASSWORD` | Postgres refuses to initialise without one |
| `APP_BASE_URL` | The CORS allowlist and the base of every magic link. Wrong value means emails whose links do not open |
| `SUPER_ADMIN_EMAIL` / `SUPER_ADMIN_PASSWORD` | Self-signup does not exist ([ADR-0002](../adr/0002-invite-only-access.md)); without the seed there is no way in |
| `EMAIL_PASSWORD` | The Resend API key. Without it invites and magic links are generated and never delivered |
| `GRAFANA_ADMIN_PASSWORD`, `LANGFUSE_*` | Only when the `obs` profile runs, which it does by default |

Then:

```bash
make prod-config     # the compose file resolves and no variable is missing
make prod-build      # api and web images, ~10 min cold
make prod-up         # migrations run first, then the stack comes up
make prod-seed       # super-admin and a workspace to sign into
make prod-ps
```

`make prod-up` is safe to repeat: the `migrate` service runs `alembic upgrade
head` and the API waits for it to exit successfully before starting.

## Verifying it

```bash
curl -fsS localhost/api/health              # {"status":"ok"}
curl -fsS localhost/api/health/db
curl -fsS localhost/api/health/redis
curl -fsSI localhost/ | head -1             # 200 from Next.js
curl -fsS -o /dev/null -w '%{http_code}\n' localhost/api/metrics   # 404, on purpose
```

Then sign in at the public URL with the seeded super-admin, upload a document
and watch the worker pick it up:

```bash
make prod-logs SERVICE=worker
```

## Updating

```bash
git pull
make prod-deploy     # build, then up -d; only changed services restart
```

Migrations are part of `up`, so a release that adds one needs nothing extra.
Rolling back an image is `git checkout <tag> && make prod-deploy`; rolling back
a migration is `docker compose ... run --rm migrate alembic downgrade -1` and
has to happen before the older image starts.

### Reclaiming disk after a few of those

Every rebuild leaves its layers in the BuildKit cache, and with PyTorch in the
image they are not small: eight deploys took the cache past 17 GB, more than
three times the images themselves.

```bash
make prod-clean      # build cache and untagged images, then a size report
```

It is deliberately narrow. `docker system prune -a` would also drop every image
without a running container - postgres, redis, langfuse - and the next deploy
would pull them all again; `--volumes` would drop the uploads, the database and
the baked reranker model. The only cost of `prod-clean` is that the next build
runs without a cache to reuse.

## Observability

Nothing is published to the internet. Tunnel in:

```bash
make obs-up      # opens all four forwards in the background
make obs-status  # says whether they are open, and on which ports
make obs-down    # closes them
```

Grafana on 3001 (admin plus `GRAFANA_ADMIN_PASSWORD`), Jaeger on 16686,
Prometheus on 9090, Langfuse on 3030. The script keeps one multiplexed
connection behind a control socket, which is what lets `down` close exactly
what `up` opened rather than every ssh you happen to be running.

It talks to the host named `reldava-demo`, so put the address, the user and the key
in `~/.ssh/config` under that name, or point `ZK2_OBS_HOST` at another entry:

```bash
ZK2_OBS_HOST=my-deploy-box make obs-up
```

By hand it is the same four forwards:

```bash
ssh -L 3001:127.0.0.1:3001 \
    -L 16686:127.0.0.1:16686 \
    -L 9090:127.0.0.1:9090 \
    -L 3030:127.0.0.1:3030 <server>
```

Langfuse needs no click-through: `LANGFUSE_INIT_*` in the compose file creates
the organization, the project and the very API keys already sitting in
`.env.prod` - but only against an empty database, on the first start. Sign in
with `LANGFUSE_ADMIN_EMAIL` and `LANGFUSE_ADMIN_PASSWORD`, which are Langfuse's
own credentials and have nothing to do with the application's users.

It is also four containers rather than one - `langfuse-web`, `langfuse-worker`,
ClickHouse, MinIO and its own Redis - because from v3 onwards the SDK ships
traces over OpenTelemetry into ClickHouse. Running the v2 image against a v3+
SDK is the trap worth knowing: the client authenticates, reports no error the
application would notice, and every span is answered with a 404 by an endpoint
that does not exist. `Traces: No results` is the only symptom.

To run without any of it: `make prod-up PROD_PROFILES=`, and empty
`OTEL_EXPORTER_OTLP_ENDPOINT` and `LANGFUSE_HOST` in `.env.prod` - pointed at
containers that are not running, the api retries every export forever and
fills the log with it. For Langfuse alone there is a switch, below.

### Switching Langfuse on and off

Langfuse is the heavy part. Its six containers held about 2.1 GB between them
on this host - `langfuse-web` and `langfuse-worker` more than ClickHouse - twice
what the application itself used, and it is only worth that while somebody is
reading traces. So it has a compose profile of its own, `langfuse`, and a
switch in the admin panel under **Services**.

The API does not start or stop anything: Docker access is root on the host, and
the API is the process facing the internet. It records the switch. An agent on
the host, `scripts/ops-agent.py`, reports every few seconds what is running,
gets the switch back in the same call, and runs `docker compose up -d` or `stop`
for the Langfuse services. It reaches the API through `docker compose exec api`,
so nothing is published for it, and Caddy answers 404 for `/api/ops/agent/*`.

Setting it up, once:

```bash
# in .env.prod
OPS_AGENT_TOKEN=...          # python3 -c "import secrets; print(secrets.token_urlsafe(32))"

make prod-up                 # the api and worker pick up the token
make prod-agent-install      # systemd unit for this user and this checkout
make prod-agent-logs
```

The unit runs as the user who installed it, which needs Docker access, from
this checkout. `make prod-deploy` restarts it, so a pulled change to the script
takes effect.

What to know about the switch:

- While it is off, nothing is recorded. The api and the worker stop exporting
  within 15 seconds, and turns answered in that time never reach Langfuse, even
  once it is back. Traces recorded earlier stay in the volumes.
- Switching on asks for how long, 4 hours unless you pick otherwise. When the
  time runs out the switch flips back by itself.
- `make prod-up` and `make prod-deploy` leave Langfuse as they find it: running
  containers are brought up to date, stopped ones stay stopped.
- With `LANGFUSE_HOSTNAME` set, the public address answers 503 with a line
  saying Langfuse is switched off, rather than a bare 502.
- The page says `no agent` when nothing has reported for a minute. The switch
  still records the choice; nothing happens on the host until the agent runs.
- Every flip is in `audit_log`: `ops.service_switched_on` and
  `ops.service_switched_off` with who and from where, `ops.service_auto_off`
  when the time ran out.

### ClickHouse memory and its own logs

`infra/compose/clickhouse-config.xml` removes ClickHouse's metric, trace and
text logs, puts a 30-day TTL on `query_log` and `part_log`, and caps the server
at 6 GiB. The file says why in detail; the short version is that merges of
`system.metric_log`, a table of about 1550 columns, took over 6 GiB each, many
times a day, on a host with 12. ClickHouse sizes its own limit from the RAM of
the machine, not of the container, so it never stopped itself. Set
`max_server_memory_usage` to what the host actually leaves it.

Changing the file later takes a recreate, not `make prod-up`. The mount spec
stays the same, so `up -d` keeps the container; and a single-file bind mount
is tied to the file's inode, while `git pull` writes a new file under the same
name. The container goes on reading the old content, restart after restart:

```bash
docker compose --env-file .env.prod -f infra/compose/docker-compose.prod.yml --profile langfuse \
  up -d --force-recreate langfuse-clickhouse
```

The config only stops writing. Tables already on disk stay, and the first start
with it may also keep the old `query_log` and `part_log` under a numeric suffix,
because their definition gained a TTL. List what is there, then drop what the
config removed:

```bash
docker compose --env-file .env.prod -f infra/compose/docker-compose.prod.yml --profile langfuse \
  exec -T langfuse-clickhouse sh -c 'clickhouse-client --user "$CLICKHOUSE_USER" --password "$CLICKHOUSE_PASSWORD" --multiquery' <<'SQL'
SELECT name, formatReadableSize(total_bytes) AS size FROM system.tables WHERE database = 'system' AND name LIKE '%log%' ORDER BY name;
SQL
```

```sql
DROP TABLE IF EXISTS system.metric_log SYNC;
DROP TABLE IF EXISTS system.asynchronous_metric_log SYNC;
DROP TABLE IF EXISTS system.trace_log SYNC;
DROP TABLE IF EXISTS system.text_log SYNC;
DROP TABLE IF EXISTS system.opentelemetry_span_log SYNC;
DROP TABLE IF EXISTS system.latency_log SYNC;
-- only if the listing showed them:
DROP TABLE IF EXISTS system.query_log_0 SYNC;
DROP TABLE IF EXISTS system.part_log_0 SYNC;
```

Checking that it took, right away and a day later:

```sql
SELECT name, value FROM system.server_settings WHERE name = 'max_server_memory_usage';
-- query_log and part_log carry the TTL once they have been recreated:
SELECT name, position(engine_full, 'TTL') > 0 AS has_ttl FROM system.tables
WHERE database = 'system' AND name IN ('query_log', 'part_log');
-- a day later, expected 0:
SELECT count() FROM system.part_log
WHERE event_type = 'MergeParts' AND peak_memory_usage > 1073741824 AND event_time > now() - INTERVAL 1 DAY;
```

### Publishing Langfuse under its own name

A tunnel is fine for an incident and tiresome for daily use. Setting
`LANGFUSE_HOSTNAME` makes Caddy match that Host header and route it to
`langfuse-web`, so the proxy in front sends both names to the same port and
nothing new is published:

```
LANGFUSE_HOSTNAME=langfuse.example.com
LANGFUSE_PUBLIC_URL=https://langfuse.example.com
```

`LANGFUSE_PUBLIC_URL` is what NextAuth signs cookies against; leave it pointing
at localhost and the login form will accept the password and bounce straight
back to itself.

Two things worth being deliberate about before pointing DNS at it. Traces carry
the prompts and the retrieved chunks - that is the text of the indexed
documents, not metadata about it. And `AUTH_DISABLE_SIGNUP` defaults to true
here for a reason: self-hosted Langfuse otherwise offers a registration form to
anybody who finds the address. The account created by `LANGFUSE_INIT_*` on the
first start is meant to be the only one.

## Backups

There is no RDS here, so [backup-restore.md](backup-restore.md) applies only in
its reasoning. On this host the whole of the state is two named volumes and the
uploads:

```bash
docker compose --env-file .env.prod -f infra/compose/docker-compose.prod.yml \
  exec -T postgres pg_dump -U zk2 zk2 | gzip > zk2-$(date +%F).sql.gz

docker run --rm -v zk2_uploads:/data -v "$PWD:/out" alpine \
  tar czf /out/uploads-$(date +%F).tar.gz -C /data .
```

Redis is deliberately not backed up: sessions are re-created by signing in and
a lost queue costs a re-index.

## When it does not come up

| Symptom | Cause worth checking first |
|---|---|
| `permission denied` from the daemon | The `docker` group membership has not been picked up yet - log out and back in |
| Build dies with no message | Out of memory or disk. `df -h` and `docker system df`; `docker builder prune` frees the most |
| api restarts, logs mention `DB_PASSWORD` | `.env.prod` is missing a REQUIRED value; `make prod-config` names it |
| Caddy answers 502 for `/api/*` | The api container is unhealthy, not the proxy. `make prod-logs SERVICE=api` |
| Bind mount reads as empty, host has SELinux | The mount lost its label. The compose file already asks for `z`; a manual `docker run` needs it too |
| Chat connects then closes immediately | The proxy in front is not forwarding the WebSocket upgrade for `/api/ws/*` |
| Invites never arrive | `EMAIL_PASSWORD` empty, or the sending domain's DNS is not verified at the provider |
| Langfuse shows no traces | The SDK major must match the server major. Check `docker compose ... logs api | grep langfuse`: a `ValidationError` from `auth_check` or `Failed to export span batch code: 404` both mean the versions disagree |
| Services switch flips, Langfuse stays as it was | `make prod-agent-logs`: the agent logs the compose error, and the Services page shows its last line. `no agent` there means the unit is not running or `OPS_AGENT_TOKEN` is empty |
