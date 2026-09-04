"""The /metrics endpoint and what it exposes."""

from __future__ import annotations

from typing import Any

import pytest
from httpx import AsyncClient

from zk2.config import get_settings
from zk2.core.metrics import record_llm_call

pytestmark = pytest.mark.integration


async def test_metrics_are_exposed_in_prometheus_format(client: AsyncClient) -> None:
    resp = await client.get("/metrics")
    assert resp.status_code == 200
    assert resp.headers["content-type"].startswith("text/plain")
    body = resp.text
    assert "zk2_http_requests_total" in body
    assert "zk2_llm_tokens_total" in body
    assert "zk2_active_websockets" in body
    assert "zk2_ingest_queue_depth" in body


async def test_http_requests_are_counted_by_route_template(client: AsyncClient) -> None:
    """Labels must carry the template, not the resolved path with its ids."""
    await client.get("/health")
    body = (await client.get("/metrics")).text
    assert 'route="/health"' in body


async def test_unmatched_paths_collapse_into_one_series(client: AsyncClient) -> None:
    """A scanner hitting random URLs must not create a time series each time."""
    await client.get("/definitely-not-a-route")
    await client.get("/another-missing-route")
    body = (await client.get("/metrics")).text
    assert 'route="unmatched"' in body
    assert "definitely-not-a-route" not in body


async def test_metrics_can_be_disabled(
    client: AsyncClient, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setattr(get_settings().observability, "metrics_enabled", False)
    assert (await client.get("/metrics")).status_code == 404


async def test_metrics_token_is_enforced_when_configured(
    client: AsyncClient, monkeypatch: pytest.MonkeyPatch
) -> None:
    from pydantic import SecretStr

    monkeypatch.setattr(get_settings().observability, "metrics_token", SecretStr("scrape-me"))
    assert (await client.get("/metrics")).status_code == 403
    assert (
        await client.get("/metrics", headers={"Authorization": "Bearer wrong"})
    ).status_code == 403
    ok = await client.get("/metrics", headers={"Authorization": "Bearer scrape-me"})
    assert ok.status_code == 200


async def test_llm_cost_metric_has_no_org_label(client: AsyncClient) -> None:
    """Per-org spend belongs in usage_events, not in a Prometheus label.

    One time series per tenant is a cardinality problem that never shrinks.
    """
    record_llm_call(
        provider="openai",
        model="gpt-4o-mini",
        duration_seconds=0.5,
        tokens_in=100,
        tokens_out=50,
        cost_usd=0.000075,
    )
    body = (await client.get("/metrics")).text
    cost_lines = [line for line in body.splitlines() if line.startswith("zk2_llm_cost_usd")]
    assert cost_lines, body[:500]
    assert any('provider="openai"' in line and 'model="gpt-4o-mini"' in line for line in cost_lines)
    assert not any("org" in line for line in cost_lines)


async def test_usage_summary_reports_tokens_and_spend(owner_client: AsyncClient, db: Any) -> None:
    from sqlalchemy import text

    await db.execute(
        text(
            "INSERT INTO usage_events (org_id, event_type, provider, model, tokens_in,"
            " tokens_out, cost_usd) VALUES (:org, 'llm_call', 'openai', 'gpt-4o-mini',"
            " 100, 50, 0.000075)"
        ),
        {"org": (await owner_client.get("/auth/me")).json()["memberships"][0]["org_id"]},
    )
    await db.commit()

    resp = await owner_client.get("/observability/usage", params={"days": 7})
    assert resp.status_code == 200, resp.text
    body = resp.json()
    assert body["calls"] == 1
    assert body["tokens_in"] == 100
    assert body["by_model"][0]["model"] == "gpt-4o-mini"


async def test_usage_summary_counts_calls_with_unknown_price(
    owner_client: AsyncClient, db: Any
) -> None:
    from sqlalchemy import text

    org_id = (await owner_client.get("/auth/me")).json()["memberships"][0]["org_id"]
    await db.execute(
        text(
            "INSERT INTO usage_events (org_id, event_type, provider, model, tokens_in,"
            " tokens_out, cost_usd) VALUES (:org, 'llm_call', 'openai', 'mystery-model',"
            " 10, 10, NULL)"
        ),
        {"org": org_id},
    )
    await db.commit()
    body = (await owner_client.get("/observability/usage")).json()
    assert body["calls_without_price"] == 1


async def test_links_describe_the_deployment(owner_client: AsyncClient) -> None:
    resp = await owner_client.get("/observability/links")
    assert resp.status_code == 200
    body = resp.json()
    assert set(body) == {
        "grafana_url",
        "jaeger_url",
        "langfuse_url",
        "prometheus_url",
        "sentry_enabled",
        "tracing_enabled",
    }
    # Tests run without an OTLP endpoint or Sentry DSN
    assert body["tracing_enabled"] is False
    assert body["sentry_enabled"] is False
