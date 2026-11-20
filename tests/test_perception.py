"""수집 범위, 취소, 키 반사, 오래된 자료와 경쟁점 격리 회귀 검사."""
import asyncio
import json

import pytest

from app.liner_client import LinerAPIError, LinerClient
from app.perception import PerceptionRequest, _period_status, _prepare, _stores, canonical_url, run_perception


@pytest.mark.parametrize(("left", "right"), [
    ("https://m.blog.naver.com/writer/123?utm_source=a", "https://blog.naver.com/writer/123"),
    ("https://blog.naver.com/PostView.naver?blogId=writer&logNo=123&redirect=Log", "https://blog.naver.com/writer/123"),
    ("https://www.example.com/a/?fbclid=foo&id=3#fragment", "https://example.com/a?id=3"),
])
def test_duplicate_document_urls(left, right):
    assert canonical_url(left) == canonical_url(right)


def test_distinct_documents_and_unsafe_urls():
    assert canonical_url("https://a.test/?id=1") != canonical_url("https://a.test/?id=2")
    assert canonical_url("javascript:alert(1)") == ""
    assert canonical_url("https://key:secret@a.test") == ""


def test_missing_dates_are_not_zero_or_in_period():
    period = {"start": "2026-01-01", "end": "2026-11-20"}
    assert _period_status("", period) == "unknown"
    assert _period_status("2026-02-30", period) == "unknown"
    assert _period_status("2027-01-01", period) == "out_of_period"
    assert _period_status("2026-02-01", period) == "in_period"


def test_prepared_ids_unique_and_dedup():
    source = {"url": "https://example.com/a", "title": "매장", "description": "자료", "date": ""}
    period = {"start": "2026-01-01", "end": "2026-11-20"}
    target = _prepare([source, {**source, "url": source["url"] + "?utm_source=x"}], "target", period)
    rival = _prepare([source], "competitor-1", period)
    assert len(target) == 1
    assert target[0]["source_id"] != rival[0]["source_id"]
    assert target[0]["period_status"] == "unknown"


def test_duplicate_competitor_is_rejected():
    with pytest.raises(ValueError, match="중복"):
        _stores(PerceptionRequest(query="산본 디델리", competitors=["산본 디델리"]))


def test_no_key_never_returns_demo(monkeypatch):
    monkeypatch.setenv("GEO_AUDITOR_TEST_DEMO", "1")
    with pytest.raises(LinerAPIError, match="키"):
        asyncio.run(run_perception(PerceptionRequest(query="산본 디델리")))


def test_partial_collection_and_card_source_isolation(monkeypatch):
    from app import perception as module
    calls = []
    secret = "test-private-value-never-return"

    class FakeClient(LinerClient):
        async def search(self, query, **kwargs):
            calls.append(query)
            if "site:go.kr" in query:
                return {"results": []}
            name = "디델리" if "디델리" in query else "두끼"
            return {"results": [
                {"url": f"https://blog.naver.com/test/{name}", "title": f"산본 {name} 후기",
                 "description": f"산본 {name}에서 먹으니 음식이 맛있었다. {secret}", "date": "2026-11-19"},
                {"url": "https://blog.naver.com/test/other", "title": "강남 다른가게 후기", "description": "맛있다"},
            ]}

        async def search_agent(self, *args, **kwargs):
            raise LinerAPIError("테스트 수집 실패")

        async def chat_completions(self, *args, **kwargs):
            assert kwargs.get("json_object") is False
            return {"choices": [{"message": {"content": json.dumps({"observations": [
                {"store_id": "target", "source_id": "target-S1", "material_id": "summary", "quote": "디델리에서 먹으니 음식이 맛있었다.",
                 "aspect": "quality", "sentiment": "positive", "voice": "customer_experience"},
                {"store_id": "competitor-1", "source_id": "competitor-1-S1", "material_id": "summary", "quote": "두끼에서 먹으니 음식이 맛있었다.",
                 "aspect": "quality", "sentiment": "positive", "voice": "customer_experience"},
            ]})}}]}

    monkeypatch.setattr(module, "LinerClient", FakeClient)
    result = asyncio.run(run_perception(PerceptionRequest(query="산본 디델리", competitors=["산본 두끼"], api_key=secret)))
    assert len(calls) == 8  # 3 per store + 2 market, never recurse through old audit
    assert len(result["sources"]) == 2
    assert result["coverage"]["failed_calls"] == 2
    assert result["warnings"]
    assert result["card_context"] == {"query": "산본 디델리"}
    assert secret not in json.dumps(result)
    assert "[REDACTED]" in json.dumps(result)
    assert all(cell["count"] is None for cell in result["stores"][0]["demographics"]["cells"])


def test_cancel_propagates_to_collection_tasks(monkeypatch):
    from app import perception as module
    canceled = []

    class SlowClient(LinerClient):
        async def search(self, query, **kwargs):
            try:
                await asyncio.Event().wait()
            finally:
                canceled.append(query)

    monkeypatch.setattr(module, "LinerClient", SlowClient)

    async def run():
        task = asyncio.create_task(run_perception(PerceptionRequest(query="산본 디델리", api_key="test-only")))
        await asyncio.sleep(0.02)
        task.cancel()
        with pytest.raises(asyncio.CancelledError):
            await task
        await asyncio.sleep(0)
        assert canceled
        assert not [t for t in asyncio.all_tasks() if t is not asyncio.current_task() and not t.done()]
    asyncio.run(run())


def test_api_validation_never_echoes_key(client):
    response = client.post("/api/perception", json={"api_key": "secret-test-key", "period_days": 5})
    assert response.status_code == 422
    assert "secret-test-key" not in response.text


def test_stream_no_key_reports_error(client):
    response = client.post("/api/perception/stream", json={"query": "산본 디델리"})
    assert "event: error" in response.text
    assert "event: result" not in response.text


def test_agent_excerpt_attaches_across_mobile_url_aliases():
    period = {"start": "2026-01-01", "end": "2026-11-20"}
    raw = [{"url": "https://blog.naver.com/user/123", "title": "산본 디델리", "description": "", "date": ""},
           {"url": "https://m.blog.naver.com/user/123", "title": "산본 디델리", "description": "", "date": ""}]
    agent = {"referenceChunks": [{"sourceUrl": "https://m.blog.naver.com/user/123", "content": "직접 먹어보니 라볶이가 맛있었다."}]}
    sources = _prepare(raw, "target", period, agent)
    assert len(sources) == 1
    assert any(m["text"] == "직접 먹어보니 라볶이가 맛있었다." for m in sources[0]["materials"])


def test_json_route_disconnect_cancels_analysis(monkeypatch):
    from app import main as module
    canceled = asyncio.Event()
    started = asyncio.Event()

    async def slow(*args, **kwargs):
        started.set()
        try:
            await asyncio.Event().wait()
        finally:
            canceled.set()
    monkeypatch.setattr(module, "run_perception", slow)

    async def run():
        body = json.dumps({"query": "산본 디델리"}).encode()
        incoming = asyncio.Queue()
        await incoming.put({"type": "http.request", "body": body, "more_body": False})
        async def receive():
            return await incoming.get()
        async def send(message):
            pass
        scope = {"type": "http", "asgi": {"version": "3.0"}, "http_version": "1.1", "method": "POST",
                 "scheme": "http", "path": "/api/perception", "raw_path": b"/api/perception", "query_string": b"",
                 "root_path": "", "headers": [(b"content-type", b"application/json")],
                 "server": ("localhost", 8765), "client": ("localhost", 54321)}
        task = asyncio.create_task(module.app(scope, receive, send))
        await asyncio.wait_for(started.wait(), 1)
        await incoming.put({"type": "http.disconnect"})
        await asyncio.wait_for(task, 1)
        assert canceled.is_set()
    asyncio.run(run())
