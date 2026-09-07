# Installing the prerequisites

The project needs six things: **uv**, **Node 22+**, **pnpm 9+**, **Docker**,
**Docker Compose v2** and **make**. Python is not in that list - `uv` downloads
its own 3.12 when the system has none.

Everything below ends at the same place:

```bash
make check
```

which prints a line per tool and exits non-zero if anything is missing. If that
passes, go back to the [quick start](../README.md#quick-start).

> Two of these are worth installing from their own repositories rather than the
> distribution's: **Node**, because every stable distribution ships a version
> too old, and **Docker**, because the distribution packages either lag badly or
> omit the Compose v2 plugin. The rest come from the system package manager.

---

## Debian and Ubuntu (apt)

Tested on Debian 12 and Ubuntu 22.04 / 24.04.

### 1. Build basics

```bash
sudo apt update
sudo apt install -y ca-certificates curl gnupg git make
```

### 2. Docker Engine and Compose v2

Do not use the `docker.io` package: it is usually a release or two behind and
does not bring the Compose v2 plugin, which this project uses as
`docker compose` (with a space, not a hyphen).

```bash
sudo install -m 0755 -d /etc/apt/keyrings
curl -fsSL https://download.docker.com/linux/$(. /etc/os-release && echo "$ID")/gpg \
  | sudo gpg --dearmor -o /etc/apt/keyrings/docker.gpg
sudo chmod a+r /etc/apt/keyrings/docker.gpg

echo "deb [arch=$(dpkg --print-architecture) signed-by=/etc/apt/keyrings/docker.gpg] \
https://download.docker.com/linux/$(. /etc/os-release && echo "$ID") \
$(. /etc/os-release && echo "$VERSION_CODENAME") stable" \
  | sudo tee /etc/apt/sources.list.d/docker.list > /dev/null

sudo apt update
sudo apt install -y docker-ce docker-ce-cli containerd.io \
                    docker-buildx-plugin docker-compose-plugin
```

Then let your user talk to the daemon without `sudo`:

```bash
sudo usermod -aG docker "$USER"
newgrp docker          # or log out and back in
docker compose version # should print v2.x
```

### 3. Node 22 and pnpm

Debian 12 ships Node 18 and Ubuntu 24.04 ships 18 as well - both too old. Use
NodeSource:

```bash
curl -fsSL https://deb.nodesource.com/setup_22.x | sudo -E bash -
sudo apt install -y nodejs
```

pnpm comes with Node through Corepack, so it does not need its own install:

```bash
sudo corepack enable
corepack prepare pnpm@9.12.0 --activate
```

### 4. uv

```bash
curl -LsSf https://astral.sh/uv/install.sh | sh
exec "$SHELL" -l          # picks up ~/.local/bin
```

---

## Fedora, RHEL, AlmaLinux and Rocky (dnf)

Tested on Fedora 41 and AlmaLinux 9.

### 1. Build basics

```bash
sudo dnf install -y ca-certificates curl gnupg2 git make
```

### 2. Docker Engine and Compose v2

Fedora's own `moby-engine` and the RHEL family's `podman-docker` shim both
cause trouble here - the first has no Compose v2 plugin, the second answers
`docker` commands with Podman, which this project's compose files do not
target. Remove them and use Docker's repository:

```bash
sudo dnf remove -y docker docker-common docker-engine \
                   moby-engine podman-docker 2>/dev/null || true

sudo dnf install -y dnf-plugins-core

# Fedora 41+ ships dnf5, where the subcommand was renamed:
sudo dnf config-manager addrepo --from-repofile=https://download.docker.com/linux/fedora/docker-ce.repo
# Fedora 40 and earlier, and the RHEL family, still take the old form:
# sudo dnf config-manager --add-repo https://download.docker.com/linux/fedora/docker-ce.repo
# RHEL family (AlmaLinux, Rocky, RHEL) uses the CentOS repository:
# sudo dnf config-manager --add-repo https://download.docker.com/linux/centos/docker-ce.repo

sudo dnf install -y docker-ce docker-ce-cli containerd.io \
                    docker-buildx-plugin docker-compose-plugin
sudo systemctl enable --now docker
```

Then the same group change as above:

```bash
sudo usermod -aG docker "$USER"
newgrp docker
docker compose version
```

### 3. Node 22 and pnpm

Fedora 41 and later carry Node 22 directly:

```bash
sudo dnf install -y nodejs npm     # check: node --version
```

On AlmaLinux, Rocky and RHEL 9 the default module stream is Node 16 or 18, so
switch it or use NodeSource:

```bash
sudo dnf module reset -y nodejs
sudo dnf module enable -y nodejs:22
sudo dnf install -y nodejs

# or, if the 22 stream is not offered:
# curl -fsSL https://rpm.nodesource.com/setup_22.x | sudo -E bash -
# sudo dnf install -y nodejs
```

Then pnpm. Corepack ships with Node on most builds:

```bash
sudo corepack enable
corepack prepare pnpm@9.12.0 --activate
```

If `corepack` is not found - some RPM builds split it out - install pnpm
directly instead:

```bash
sudo npm install -g pnpm@9.12.0
```

### 4. uv

```bash
curl -LsSf https://astral.sh/uv/install.sh | sh
exec "$SHELL" -l
```

---

## Checking

```bash
git clone <this repository> && cd zk2-rag-platform
cp .env.example .env
make check
```

`make check` verifies versions, tells you whether the Docker daemon is actually
reachable, and reminds you which variables in `.env` still need filling in. A
missing Python 3.12 is only a warning: `uv` supplies its own.

---

## Disk, and the reranker

Measured on the deployment: **5.5 GB of images and 1.4 GB of volumes**, before
build cache. The API image is about **2.1 GB** of that, most of it CPU-only
PyTorch plus the cross-encoder model baked in, so that reranking works on the
first question rather than after a download. Docker's build cache can grow past
the images themselves - `docker builder prune` when disk gets tight.

If that is too much, `RERANK_ENABLED=false` turns reranking off and
`uv sync` without `--extra rerank` leaves torch out of a local environment
entirely. Retrieval then keeps the fused order instead of failing.

---

## When something does not work

**`docker compose` says "is not a docker command".** The Compose v2 plugin is
missing - you have the old standalone `docker-compose`. Install
`docker-compose-plugin` from Docker's repository; the Makefile uses the plugin
form throughout.

**`permission denied` talking to the Docker socket.** The group change has not
reached your shell. `newgrp docker`, or log out and back in.

**Prometheus shows the API target as down.** Expected in development: the API
binds to loopback and Prometheus lives in a container. Start it with
`make dev-api API_HOST=0.0.0.0`, or ignore it - nothing else depends on the
scrape.

**Every container dies immediately, and the daemon looks healthy.** If you are
running inside an LXC or Incus container, this is the host's fault, not
Docker's. Docker 28+ writes `net.ipv4.ip_unprivileged_port_start` for every
container that gets its own network namespace, and an unprivileged container
without `security.nesting` cannot. The tell:

```bash
docker run --rm --network host alpine true   # works
docker run --rm alpine true                  # "permission denied" on the sysctl
```

Set `security.nesting=true` on the instance. `security.privileged=true` also
makes it work and is the wrong answer.

**SELinux (Fedora, RHEL family) blocks a bind mount.** The compose files use
named volumes rather than host paths, so this should not arise; if you add a
bind mount of your own, it needs the `:z` suffix.
