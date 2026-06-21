"""
Tests — UsageTracker (tokens/requêtes par tenant) + exposition /metrics.
"""

import pytest
from fastapi.testclient import TestClient

from colaig.metrics import UsageTracker
from colaig.web.routes import create_app


class TestUsageTracker:

    def test_record_global_and_client(self):
        t = UsageTracker()
        t.record("client-a", prompt_tokens=10, completion_tokens=5)
        t.record("client-b", prompt_tokens=20, completion_tokens=0)
        snap = t.snapshot()
        assert snap["global"]["requests"] == 2
        assert snap["global"]["total_tokens"] == 35
        assert snap["by_client"]["client-a"]["total_tokens"] == 15
        assert snap["by_client"]["client-b"]["prompt_tokens"] == 20

    def test_record_from_usage(self):
        t = UsageTracker()
        t.record_from_usage("c", {"prompt_tokens": 7, "completion_tokens": 3})
        assert t.snapshot()["by_client"]["c"]["total_tokens"] == 10

    def test_record_from_usage_none_safe(self):
        t = UsageTracker()
        t.record_from_usage("c", None)
        assert t.snapshot()["by_client"]["c"]["requests"] == 1

    def test_prometheus_text(self):
        t = UsageTracker()
        t.record("svc", prompt_tokens=4, completion_tokens=6)
        text = t.prometheus_text()
        assert "colaig_llm_requests_total" in text
        assert 'client="svc"' in text
        assert "colaig_llm_tokens_total" in text


class TestQuota:

    def test_no_limit_allows(self):
        t = UsageTracker()
        assert t.check_quota("c") == (True, "")

    def test_request_limit_blocks(self):
        t = UsageTracker()
        t.set_limits(daily_request_limit=2)
        t.record("c"); t.record("c")
        allowed, reason = t.check_quota("c")
        assert allowed is False and "requêtes" in reason

    def test_token_limit_blocks(self):
        t = UsageTracker()
        t.set_limits(daily_token_limit=10)
        t.record("c", prompt_tokens=6, completion_tokens=6)
        allowed, reason = t.check_quota("c")
        assert allowed is False and "tokens" in reason

    def test_per_client_override(self):
        t = UsageTracker()
        t.set_limits(daily_request_limit=100, per_client_limits={"vip": {"requests": 1}})
        t.record("vip")
        assert t.check_quota("vip")[0] is False
        t.record("autre")
        assert t.check_quota("autre")[0] is True

    def test_under_limit_allows(self):
        t = UsageTracker()
        t.set_limits(daily_request_limit=5)
        t.record("c")
        assert t.check_quota("c")[0] is True


class TestAlbertQuotaEnforcement:

    async def test_check_quota_raises_when_over(self):
        from colaig.exceptions import QuotaExceededError
        from colaig.integrations.albert import AlbertClient
        from colaig.models import ColaigConfig

        t = UsageTracker()
        t.set_limits(daily_request_limit=1)
        t.record("c")
        client = AlbertClient(ColaigConfig(), usage_tracker=t, client_id="c")
        with pytest.raises(QuotaExceededError):
            await client.chat([{"role": "user", "content": "x"}])
        with pytest.raises(QuotaExceededError):
            await client.embed("x")


class TestMetricsEndpoints:

    def test_metrics_json_includes_usage(self):
        t = UsageTracker()
        t.record("x", prompt_tokens=1, completion_tokens=1)
        client = TestClient(create_app(usage_tracker=t))
        r = client.get("/metrics")
        assert r.status_code == 200
        assert "llm_usage" in r.json()
        assert r.json()["llm_usage"]["global"]["requests"] == 1

    def test_metrics_prometheus_endpoint(self):
        t = UsageTracker()
        t.record("x", prompt_tokens=2, completion_tokens=2)
        client = TestClient(create_app(usage_tracker=t))
        r = client.get("/metrics/prometheus")
        assert r.status_code == 200
        assert "colaig_llm_tokens_total" in r.text
