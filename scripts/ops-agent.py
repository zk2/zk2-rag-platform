#!/usr/bin/env python3
"""Start and stop managed services to match the switches in the admin panel.

Runs on the deployment host as a systemd service (`make prod-agent-install`).
Every few seconds it looks at which containers are running, reports that to the
API, and gets back the state a super-admin asked for. When the two differ it
runs docker compose to close the gap.

The API cannot do this itself, on purpose: access to the Docker daemon is root
on the host, and the API is the process facing the internet. Here that access
stays with a process nobody can reach.

It talks to the API through `docker compose exec api`, not through Caddy. The
proxy answers 404 for /api/ops/agent/*, the published port may be bound to the
front proxy's address rather than to loopback, and the token is expanded inside
the api container from its own environment - so this process holds no secret.

Standard library only, and Python 3.9: that is what an EL9 host has without
installing anything.
"""

from __future__ import annotations

import json
import logging
import os
import subprocess
import tempfile
import time
from pathlib import Path
from typing import Optional

REPO = Path(__file__).resolve().parent.parent
COMPOSE = [
    "docker",
    "compose",
    "--env-file",
    str(REPO / ".env.prod"),
    "-f",
    str(REPO / "infra" / "compose" / "docker-compose.prod.yml"),
    "--profile",
    "langfuse",
]

# Name in the admin panel -> the compose services behind it. Starting would
# only need the first two, depends_on brings up the rest; stopping needs all.
SERVICES: dict[str, list[str]] = {
    "langfuse": [
        "langfuse-web",
        "langfuse-worker",
        "langfuse-clickhouse",
        "langfuse-minio",
        "langfuse-redis",
        "langfuse-db",
    ],
}

INTERVAL_SECONDS = float(os.environ.get("ZK2_OPS_AGENT_INTERVAL", "5"))
# A first start may pull images, which takes minutes rather than seconds
ACTION_TIMEOUT_SECONDS = 900
# A compose command that failed is not retried every few seconds
RETRY_AFTER_FAILURE_SECONDS = 60

_REPORT = (
    'exec curl -fsS --max-time 20 -H "Content-Type: application/json" '
    '-H "X-Ops-Agent-Token: ${OPS_AGENT_TOKEN:?OPS_AGENT_TOKEN is not set in .env.prod}" '
    "--data-binary @- http://localhost:8000/ops/agent/report"
)

log = logging.getLogger("zk2-ops-agent")

# (state, detail) in the vocabulary the API accepts
Observed = tuple[str, Optional[str]]


class ReportFailed(Exception):
    pass


def _last_line(text: str) -> str:
    lines = [line.strip() for line in text.splitlines() if line.strip()]
    return lines[-1][:500] if lines else "no output"


def observe(containers: list[str]) -> Observed:
    try:
        proc = subprocess.run(
            [*COMPOSE, "ps", "--all", "--format", "json", *containers],
            capture_output=True,
            text=True,
            timeout=60,
            cwd=REPO,
            check=False,
        )
    except subprocess.TimeoutExpired:
        return "error", "docker compose ps did not answer within 60 s"
    if proc.returncode != 0:
        return "error", _last_line(proc.stderr)
    out = proc.stdout.strip()
    try:
        # One JSON object per line; compose releases before 2.21 printed an array
        if out.startswith("["):
            entries = json.loads(out)
        else:
            entries = [json.loads(line) for line in out.splitlines() if line.strip()]
    except ValueError:
        return "error", "unreadable output from docker compose ps"
    running = {entry.get("Service") for entry in entries if entry.get("State") == "running"}
    down = [name for name in containers if name not in running]
    if not down:
        return "running", None
    if len(down) == len(containers):
        return "stopped", None
    return "partial", "not running: " + ", ".join(down)


def report(observed: dict[str, Observed]) -> dict[str, str]:
    """Tell the API what is running; return the state each service should be in."""
    body = json.dumps(
        {
            "services": [
                {"name": name, "state": state, "detail": detail}
                for name, (state, detail) in observed.items()
            ]
        }
    )
    try:
        proc = subprocess.run(
            [*COMPOSE, "exec", "-T", "api", "sh", "-c", _REPORT],
            input=body,
            capture_output=True,
            text=True,
            timeout=60,
            cwd=REPO,
            check=False,
        )
    except subprocess.TimeoutExpired as exc:
        raise ReportFailed("the api did not answer within 60 s") from exc
    if proc.returncode != 0:
        raise ReportFailed(_last_line(proc.stderr or proc.stdout))
    try:
        return {s["name"]: s["desired_state"] for s in json.loads(proc.stdout)["services"]}
    except (ValueError, KeyError, TypeError) as exc:
        raise ReportFailed("unexpected answer: " + proc.stdout[:200]) from exc


class Action:
    """A docker compose up or stop, running in the background.

    In the background so the agent keeps reporting while ClickHouse and
    Postgres come up: a start that blocks the loop for a minute would look,
    from the admin panel, exactly like an agent that has died.
    """

    def __init__(self, want: str, containers: list[str]) -> None:
        self.want = want
        self.started = time.monotonic()
        # A file rather than a pipe: image pull progress can fill a pipe buffer
        # and leave compose blocked on a write nobody reads
        self._output = tempfile.TemporaryFile(mode="w+")
        verb = ["up", "-d"] if want == "on" else ["stop"]
        self._proc = subprocess.Popen(
            [*COMPOSE, *verb, *containers],
            stdout=self._output,
            stderr=subprocess.STDOUT,
            cwd=REPO,
        )

    @property
    def transition(self) -> str:
        return "starting" if self.want == "on" else "stopping"

    def poll(self) -> tuple[bool, str] | None:
        """None while it runs; then whether it succeeded, and its last line."""
        if self._proc.poll() is None:
            if time.monotonic() - self.started < ACTION_TIMEOUT_SECONDS:
                return None
            self._proc.kill()
            self._proc.wait()
            self._output.close()
            return False, f"timed out after {ACTION_TIMEOUT_SECONDS} s"
        self._output.seek(0)
        last = _last_line(self._output.read())
        self._output.close()
        return self._proc.returncode == 0, last


def main() -> None:
    logging.basicConfig(level=logging.INFO, format="%(levelname)s %(message)s")
    log.info("watching %s every %.0f s", ", ".join(SERVICES), INTERVAL_SECONDS)

    actions: dict[str, Action] = {}
    failures: dict[str, tuple[float, str]] = {}
    report_error: str | None = None

    while True:
        observed: dict[str, Observed] = {}
        for name, containers in SERVICES.items():
            action = actions.get(name)
            if action is not None:
                outcome = action.poll()
                if outcome is None:
                    observed[name] = (action.transition, None)
                    continue
                del actions[name]
                succeeded, last = outcome
                if succeeded:
                    log.info("%s switched %s", name, action.want)
                else:
                    log.error("switching %s %s failed: %s", name, action.want, last)
                    failures[name] = (time.monotonic(), last)
            failure = failures.get(name)
            if failure is not None:
                if time.monotonic() - failure[0] < RETRY_AFTER_FAILURE_SECONDS:
                    observed[name] = ("error", failure[1])
                    continue
                del failures[name]
            observed[name] = observe(containers)

        try:
            desired = report(observed)
        except ReportFailed as exc:
            # The api restarts on every deploy: say so once, not every tick
            if str(exc) != report_error:
                log.warning("cannot report to the api: %s", exc)
                report_error = str(exc)
            time.sleep(INTERVAL_SECONDS)
            continue
        if report_error is not None:
            log.info("reporting to the api again")
            report_error = None

        for name, containers in SERVICES.items():
            want = desired.get(name)
            state = observed[name][0]
            if want is None or state in ("starting", "stopping", "error"):
                continue
            if state == ("running" if want == "on" else "stopped"):
                continue
            log.info("%s is %s, switching it %s", name, state, want)
            actions[name] = Action(want, containers)

        time.sleep(INTERVAL_SECONDS)


if __name__ == "__main__":
    main()
