"""실패 응답과 키 비노출 회귀 검증. 실제 자격 증명이나 네트워크를 쓰지 않는다."""

from __future__ import annotations

import asyncio
import json

import httpx
import pytest

from app.liner_client import LinerAPIError, LinerClient

SECRET = "synthetic-private-key-12345"


@pytest.mark.parametrize("path", ["/api/audit", "/api/audit/stream"])
@pytest.mark.parametrize("payload", [
    {"query": "성수동 카페", "api_key": {"value": SECRET}},
    [{"query": "성수동 카페", "api_key": SECRET}],
])
def test_validation_does_not_echo_request_body(client, path, payload):
    response = client.post(path, json=payload)
    assert response.status_code == 422
    assert SECRET not in response.text
    assert all("input" not in error and "ctx" not in error for error in response.json()["detail"])


@pytest.mark.parametrize("path", ["/api/audit", "/api/audit/stream"])
@pytest.mark.parametrize("error_type", [RuntimeError, ValueError])
def test_unexpected_failure_does_not_echo_exception(client, monkeypatch, path, error_type):
    async def fail(*_args):
        raise error_type(f"internal details: {SECRET}")

    monkeypatch.setattr("app.main.run_audit", fail)
    response = client.post(path, json={"query": "성수동 카페"})
    assert SECRET not in response.text
    assert "internal details" not in response.text
    if path.endswith("stream"):
        assert "event: error" in response.text
    else:
        assert response.status_code in (400, 500)


@pytest.mark.parametrize("response", [
    httpx.Response(200, text="<html>upstream failure</html>"),
    httpx.Response(200, json={}),
    httpx.Response(200, json={"results": None}),
    httpx.Response(200, json={"results": ["invalid entry"]}),
    httpx.Response(302, headers={"location": "https://example.com"}),
])
def test_invalid_search_response_is_a_failure(response):
    api = LinerClient(api_key=SECRET, transport=httpx.MockTransport(lambda _request: response))
    with pytest.raises(LinerAPIError):
        asyncio.run(api.search("성수동 카페"))
    assert api.calls[-1]["status"] == "error"


def test_empty_search_results_are_a_valid_empty_state():
    api = LinerClient(
        api_key=SECRET,
        transport=httpx.MockTransport(lambda _request: httpx.Response(200, json={"results": []})),
    )
    assert asyncio.run(api.search("성수동 카페"))["results"] == []
    assert api.calls[-1]["status"] == "ok"


@pytest.mark.parametrize("data", [
    {"answer": "", "references": []},
    {"answer": "정보 요약", "references": None},
    {"answer": "정보 요약", "references": ["invalid entry"]},
])
def test_invalid_search_agent_response_is_a_failure(data):
    api = LinerClient(
        api_key=SECRET, transport=httpx.MockTransport(lambda _request: httpx.Response(200, json=data)),
    )
    with pytest.raises(LinerAPIError):
        asyncio.run(api.search_agent([{"role": "user", "content": "성수동 카페"}]))
    assert api.calls[-1]["status"] == "error"


def test_timeout_has_specific_safe_message():
    def timeout(request):
        raise httpx.ReadTimeout(f"request header: {SECRET}", request=request)

    api = LinerClient(api_key=SECRET, transport=httpx.MockTransport(timeout))
    with pytest.raises(LinerAPIError, match="시간이 초과") as exc:
        asyncio.run(api.search("성수동 카페"))
    assert SECRET not in str(exc.value)
    assert api.calls[-1]["detail"] == "timeout"
    assert SECRET not in json.dumps(api.calls)


def test_provider_echoed_key_is_redacted_from_results_and_telemetry():
    api = LinerClient(
        api_key=SECRET,
        transport=httpx.MockTransport(lambda _request: httpx.Response(200, json={
            "requestId": SECRET,
            "results": [{"title": f"unexpected echo {SECRET}", "url": "https://example.com"}],
        })),
    )
    result = asyncio.run(api.search("성수동 카페"))
    assert SECRET not in json.dumps(result)
    assert SECRET not in json.dumps(api.calls)


@pytest.mark.parametrize("content", ["{}", [], None])
def test_empty_or_nontext_router_content_is_a_failure(content):
    with pytest.raises(LinerAPIError):
        LinerClient.parse_json_content({"choices": [{"message": {"content": content}}]})


def test_malformed_upstream_returns_error_without_fabricated_result(client, monkeypatch):
    original_init = LinerClient.__init__

    def init(self, api_key=None, **kwargs):
        original_init(self, api_key=api_key, transport=httpx.MockTransport(
            lambda _request: httpx.Response(200, text="not JSON")
        ), **kwargs)

    monkeypatch.setattr(LinerClient, "__init__", init)
    response = client.post("/api/audit", json={"query": "성수동 카페 모먼트", "api_key": SECRET})
    assert response.status_code == 502
    body = response.json()
    assert "형식" in body["detail"]
    assert "contents" not in body
    assert "overall_score" not in body
    assert SECRET not in response.text


def test_closing_stream_cancels_audit_and_honors_request_key(monkeypatch):
    from app.main import AuditRequest, audit_stream

    async def check():
        started = asyncio.Event()
        stopped = asyncio.Event()

        async def pending(*_args):
            started.set()
            try:
                await asyncio.Event().wait()
            finally:
                stopped.set()

        monkeypatch.setattr("app.main.run_audit", pending)
        response = await audit_stream(AuditRequest(query="성수동 카페", api_key=SECRET))
        opening = await anext(response.body_iterator)
        assert '"mode": "live"' in opening
        await asyncio.wait_for(started.wait(), timeout=1)
        await response.body_iterator.aclose()
        assert stopped.is_set()

    asyncio.run(check())


@pytest.mark.parametrize("path", ["/api/audit", "/api/audit/stream"])
def test_product_without_key_never_returns_demo_result(client, monkeypatch, path):
    monkeypatch.delenv("GEO_AUDITOR_TEST_DEMO", raising=False)
    response = client.post(path, json={"query": "성수동 카페 로우키"})
    assert "API 키" in response.text
    assert "overall_score" not in response.text
    assert "contents" not in response.text
    if path.endswith("stream"):
        assert "event: error" in response.text
        assert "event: result" not in response.text
    else:
        assert response.status_code == 502


def test_api_failure_stream_does_not_return_demo_result(client, monkeypatch):
    original_init = LinerClient.__init__

    def init(self, api_key=None, **kwargs):
        original_init(self, api_key=api_key, transport=httpx.MockTransport(
            lambda _request: httpx.Response(503, json={"error": "unavailable"})
        ), **kwargs)

    monkeypatch.setattr(LinerClient, "__init__", init)
    response = client.post("/api/audit/stream", json={"query": "성수동 카페 로우키", "api_key": SECRET})
    assert "event: error" in response.text
    assert "event: result" not in response.text
    assert "overall_score" not in response.text
    assert SECRET not in response.text
