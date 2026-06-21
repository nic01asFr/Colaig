"""
Tests — Probes ops (/ready, /live), request_id middleware.
"""

from fastapi.testclient import TestClient

from colaig.web.routes import create_app


class _OkStorage:
    async def exists(self, path):
        return True


class _BadStorage:
    async def exists(self, path):
        raise RuntimeError("storage down")


class _LLM:
    def __init__(self, healthy):
        self._healthy = healthy

    async def ping(self, timeout=5.0):
        return self._healthy


class TestLive:
    def test_live_ok(self):
        client = TestClient(create_app())
        r = client.get("/live")
        assert r.status_code == 200
        assert r.json()["status"] == "alive"


class TestReady:
    def test_ready_storage_ok(self):
        client = TestClient(create_app(storage=_OkStorage()))
        r = client.get("/ready")
        assert r.status_code == 200
        assert r.json()["status"] == "ready"
        assert r.json()["checks"]["storage"] == "ok"

    def test_ready_storage_down_returns_503(self):
        client = TestClient(create_app(storage=_BadStorage()))
        r = client.get("/ready")
        assert r.status_code == 503
        assert r.json()["status"] == "not_ready"

    def test_ready_llm_ok(self):
        client = TestClient(create_app(storage=_OkStorage(), llm_client=_LLM(True)))
        r = client.get("/ready")
        assert r.status_code == 200
        assert r.json()["checks"]["llm"] == "ok"

    def test_ready_llm_down_returns_503(self):
        client = TestClient(create_app(storage=_OkStorage(), llm_client=_LLM(False)))
        r = client.get("/ready")
        assert r.status_code == 503


class TestRequestID:
    def test_request_id_header_present(self):
        client = TestClient(create_app())
        r = client.get("/live")
        assert r.headers.get("x-request-id")

    def test_request_id_echoed_from_header(self):
        client = TestClient(create_app())
        r = client.get("/live", headers={"x-request-id": "abc123"})
        assert r.headers.get("x-request-id") == "abc123"
