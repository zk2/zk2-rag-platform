"""Load profile for the API.

Run it against a deployment you are allowed to load:

    pip install locust
    locust -f infra/load/locustfile.py --host http://localhost:8000

Two things this deliberately does not do. It does not drive the chat WebSocket:
Locust speaks HTTP, and a chat turn's cost is dominated by the provider, so
loading it measures the provider's queue rather than this service. And it does
not create data on every iteration - a load test that grows the corpus is
measuring a different system with every minute that passes.

Set LOAD_EMAIL and LOAD_PASSWORD for an account that exists on the target.
"""

from __future__ import annotations

import os
import random

from locust import HttpUser, between, task

EMAIL = os.environ.get("LOAD_EMAIL", "")
PASSWORD = os.environ.get("LOAD_PASSWORD", "")


class ApiUser(HttpUser):
    """A signed-in user browsing their workspace."""

    wait_time = between(0.5, 3.0)

    def on_start(self) -> None:
        if not EMAIL or not PASSWORD:
            raise RuntimeError("Set LOAD_EMAIL and LOAD_PASSWORD")
        response = self.client.post(
            "/auth/login", json={"email": EMAIL, "password": PASSWORD}, name="POST /auth/login"
        )
        response.raise_for_status()
        body = response.json()
        self.client.headers.update({"Authorization": f"Bearer {body['access_token']}"})

        me = self.client.get("/auth/me", name="GET /auth/me").json()
        memberships = me.get("memberships", [])
        if memberships:
            self.client.headers.update({"X-Org-Id": str(memberships[0]["org_id"])})

    @task(5)
    def browse_sources(self) -> None:
        self.client.get("/sources/tree", name="GET /sources/tree")

    @task(4)
    def list_bots(self) -> None:
        self.client.get("/bots", name="GET /bots")

    @task(2)
    def model_catalog(self) -> None:
        # Cached client-side in the app, so a low weight is realistic
        self.client.get("/settings/models", name="GET /settings/models")

    @task(2)
    def usage(self) -> None:
        days = random.choice([7, 30])
        self.client.get(f"/observability/usage?days={days}", name="GET /observability/usage")

    @task(1)
    def allowance(self) -> None:
        self.client.get("/observability/allowance", name="GET /observability/allowance")

    @task(1)
    def pipelines(self) -> None:
        self.client.get("/pipelines", name="GET /pipelines")


class AnonymousUser(HttpUser):
    """Traffic that never signs in: health checks and the access-request form.

    Included because the global rate limit and the invite-only flow are exactly
    what unauthenticated load exercises.
    """

    wait_time = between(1.0, 5.0)

    @task(3)
    def health(self) -> None:
        self.client.get("/health", name="GET /health")

    @task(1)
    def request_access(self) -> None:
        # Rate-limited by design: 429 is a pass, not a failure
        with self.client.post(
            "/access-requests",
            json={"email": f"load-{random.randint(1, 10_000)}@example.com"},
            name="POST /access-requests",
            catch_response=True,
        ) as response:
            if response.status_code in (200, 201, 429):
                response.success()
