#!/usr/bin/env bash
# Pre-flight check: verify all required tools are installed.
# Prints OK / MISSING per tool and exits non-zero if anything is missing.

set -u

C_GREEN=$'\033[32m'
C_RED=$'\033[31m'
C_YELLOW=$'\033[33m'
C_DIM=$'\033[2m'
C_RESET=$'\033[0m'

missing=0
warn=0

ok() {
    printf "  %s✓%s  %-22s %s%s%s\n" "$C_GREEN" "$C_RESET" "$1" "$C_DIM" "$2" "$C_RESET"
}

fail() {
    printf "  %s✗%s  %-22s %sMISSING%s\n     %s↳%s install: %s\n" \
        "$C_RED" "$C_RESET" "$1" "$C_RED" "$C_RESET" "$C_DIM" "$C_RESET" "$2"
    missing=$((missing + 1))
}

warning() {
    printf "  %s!%s  %-22s %s%s%s\n     %s↳%s %s\n" \
        "$C_YELLOW" "$C_RESET" "$1" "$C_YELLOW" "$2" "$C_RESET" "$C_DIM" "$C_RESET" "$3"
    warn=$((warn + 1))
}

# Compare two semver-ish versions (returns 0 if $1 >= $2)
version_ge() {
    [ "$1" = "$(printf '%s\n%s\n' "$1" "$2" | sort -V | tail -1)" ]
}

echo "Checking required tools..."
echo

# ─── uv (Python package manager) ──────────────────────────────
if uv_ver=$(uv --version 2>/dev/null); then
    ok "uv" "$uv_ver"
else
    fail "uv" "curl -LsSf https://astral.sh/uv/install.sh | sh"
fi

# ─── Python 3.12 (uv can install if missing; just warn) ──────
py_target="3.12"
py_found=""
for py in python3.12 python3; do
    if v=$(command -v "$py" 2>/dev/null) && ver=$("$py" --version 2>&1 | awk '{print $2}'); then
        if [[ "$ver" == 3.12.* ]]; then
            py_found="$ver"
            break
        fi
    fi
done
if [ -n "$py_found" ]; then
    ok "python 3.12" "$py_found"
else
    warning "python 3.12" "not found in PATH" \
        "uv will download and use its own Python 3.12 (no action needed)"
fi

# ─── Node 22+ ─────────────────────────────────────────────────
node_min="22.0.0"
if node_ver=$(node --version 2>/dev/null | sed 's/^v//'); then
    if version_ge "$node_ver" "$node_min"; then
        ok "node $node_min+" "v$node_ver"
    else
        fail "node $node_min+" "have v$node_ver — upgrade Node (https://nodejs.org or nvm)"
    fi
else
    fail "node $node_min+" "install Node 22+: https://nodejs.org or 'nvm install 22'"
fi

# ─── pnpm 9+ (Corepack will fetch on first use) ──────────────
pnpm_min="9.0.0"
if pnpm_ver=$(pnpm --version 2>/dev/null); then
    if version_ge "$pnpm_ver" "$pnpm_min"; then
        ok "pnpm $pnpm_min+" "$pnpm_ver"
    else
        fail "pnpm $pnpm_min+" "have $pnpm_ver — run 'corepack prepare pnpm@9.12.0 --activate'"
    fi
else
    fail "pnpm $pnpm_min+" "corepack enable && corepack prepare pnpm@9.12.0 --activate"
fi

# ─── Docker ──────────────────────────────────────────────────
if docker_ver=$(docker --version 2>/dev/null); then
    ok "docker" "$docker_ver"
    if ! docker info >/dev/null 2>&1; then
        warning "docker daemon" "not reachable" "start docker daemon, or check group membership (sudo usermod -aG docker \$USER)"
    fi
else
    fail "docker" "https://docs.docker.com/engine/install/"
fi

# ─── docker compose v2 ───────────────────────────────────────
if compose_ver=$(docker compose version --short 2>/dev/null); then
    ok "docker compose" "v$compose_ver"
elif command -v docker-compose >/dev/null 2>&1; then
    fail "docker compose" "found old standalone 'docker-compose' — install Compose v2 plugin"
else
    fail "docker compose" "https://docs.docker.com/compose/install/"
fi

# ─── make ────────────────────────────────────────────────────
if make_ver=$(make --version 2>/dev/null | head -1); then
    ok "make" "$make_ver"
fi

# ─── git (optional) ──────────────────────────────────────────
if git_ver=$(git --version 2>/dev/null); then
    ok "git" "$git_ver"
else
    warning "git" "missing" "needed only if you plan to commit"
fi

# ─── .env presence ───────────────────────────────────────────
echo
if [ -f ".env" ]; then
    ok ".env" "present"
else
    warning ".env" "missing" "run: cp .env.example .env  and fill in APP_SECRET_KEY, DB_PASSWORD, SUPER_ADMIN_*"
fi

echo
if [ "$missing" -gt 0 ]; then
    printf "%s✗ %d required tool(s) missing — see above.%s\n" "$C_RED" "$missing" "$C_RESET"
    exit 1
fi
if [ "$warn" -gt 0 ]; then
    printf "%s! %d warning(s) — see above.%s\n" "$C_YELLOW" "$warn" "$C_RESET"
fi
printf "%s✓ All required tools installed.%s\n" "$C_GREEN" "$C_RESET"
