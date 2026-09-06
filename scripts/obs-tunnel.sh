#!/usr/bin/env bash
# Open, close or inspect the SSH tunnels to the observability UIs.
#
# Grafana, Jaeger and Prometheus bind to 127.0.0.1 on the deployment host and
# are not published: unreachable is their only authentication, so a tunnel is
# the way in. Langfuse is here too, which matters when it has no public
# hostname of its own; when it does, its own name works without this.
#
#   scripts/obs-tunnel.sh up | down | status
#
# The host comes from ZK2_OBS_HOST, so an entry in ~/.ssh/config with a key and
# a user is the intended way to say where and as whom.
set -euo pipefail

HOST="${ZK2_OBS_HOST:-zk-demo}"
SOCKET="${ZK2_OBS_SOCKET:-${TMPDIR:-/tmp}/zk2-obs-tunnel-$(id -u)}"

# Local port, remote port, name. Same number on both ends keeps the links in
# the app - which print the local address - true for the tunnelled case.
TOOLS=(
    "3001 3001 Grafana"
    "16686 16686 Jaeger"
    "9090 9090 Prometheus"
    "3030 3030 Langfuse"
)

C_GREEN=$'\033[32m'
C_RED=$'\033[31m'
C_DIM=$'\033[2m'
C_RESET=$'\033[0m'

is_up() {
    [ -S "$SOCKET" ] && ssh -S "$SOCKET" -O check "$HOST" >/dev/null 2>&1
}

print_links() {
    local local_port remote_port name
    for tool in "${TOOLS[@]}"; do
        read -r local_port remote_port name <<<"$tool"
        printf "  %-12s %shttp://127.0.0.1:%s%s\n" "$name" "$C_DIM" "$local_port" "$C_RESET"
    done
}

up() {
    if is_up; then
        printf "%s[up]%s   already open to %s\n" "$C_GREEN" "$C_RESET" "$HOST"
        print_links
        return 0
    fi
    # A socket left behind by a killed ssh would make the next -O check lie
    rm -f "$SOCKET"

    local forwards=()
    local local_port remote_port name
    for tool in "${TOOLS[@]}"; do
        read -r local_port remote_port name <<<"$tool"
        forwards+=(-L "${local_port}:127.0.0.1:${remote_port}")
    done

    # ExitOnForwardFailure: a port already taken locally has to be an error,
    # not a tunnel that is up and missing half its forwards.
    if ssh -f -N -M -S "$SOCKET" \
        -o ExitOnForwardFailure=yes \
        -o ServerAliveInterval=30 \
        "${forwards[@]}" "$HOST"; then
        printf "%s[up]%s   tunnels to %s\n" "$C_GREEN" "$C_RESET" "$HOST"
        print_links
    else
        printf "%s[fail]%s could not open the tunnels to %s\n" "$C_RED" "$C_RESET" "$HOST"
        printf "       a local port may be in use: %s\n" "3001, 16686, 9090, 3030"
        return 1
    fi
}

down() {
    if ! is_up; then
        printf "[down] nothing open to %s\n" "$HOST"
        rm -f "$SOCKET"
        return 0
    fi
    ssh -S "$SOCKET" -O exit "$HOST" >/dev/null 2>&1 || true
    rm -f "$SOCKET"
    printf "%s[down]%s closed the tunnels to %s\n" "$C_GREEN" "$C_RESET" "$HOST"
}

status() {
    if is_up; then
        printf "%s[up]%s   %s\n" "$C_GREEN" "$C_RESET" "$HOST"
        print_links
    else
        printf "[down] no tunnels to %s\n" "$HOST"
        return 1
    fi
}

case "${1:-}" in
    up) up ;;
    down) down ;;
    status) status ;;
    *)
        printf "usage: %s up|down|status\n\n" "$0"
        printf "  ZK2_OBS_HOST    ssh destination (default: zk-demo)\n"
        printf "  ZK2_OBS_SOCKET  control socket path\n"
        exit 2
        ;;
esac
