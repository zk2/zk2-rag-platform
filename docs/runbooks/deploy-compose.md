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

## Observability

Nothing is published to the internet. Tunnel in:

```bash
ssh -L 3001:127.0.0.1:3001 \
    -L 16686:127.0.0.1:16686 \
    -L 9090:127.0.0.1:9090 \
    -L 3030:127.0.0.1:3030 <server>
```

Grafana on 3001 (admin plus `GRAFANA_ADMIN_PASSWORD`), Jaeger on 16686,
Prometheus on 9090, Langfuse on 3030. Langfuse needs a project created in its
UI once; put the resulting keys into `LANGFUSE_PUBLIC_KEY` and
`LANGFUSE_SECRET_KEY` and restart the api and worker.

To run without any of it: `make prod-up PROD_PROFILES=`, and empty
`OTEL_EXPORTER_OTLP_ENDPOINT` and `LANGFUSE_HOST` in `.env.prod` - pointed at
containers that are not running, the api retries every export forever and
fills the log with it.

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
