"""프런트엔드 계약 검증.

`app/static/app.js`는 /api/audit 응답의 특정 키를 직접 읽는다. 서버 응답 구조가 바뀌어
화면이 조용히 비는 일을 막기 위해, 데모/라이브 응답이 JS가 기대하는 키를 모두 갖는지 확인한다.
"""

from __future__ import annotations

import json
import re
from pathlib import Path

from app import samples as sample_data

ROOT = Path(__file__).resolve().parents[1]
INDEX_HTML = ROOT / "app" / "templates" / "index.html"
APP_JS = ROOT / "app" / "static" / "app.js"


def _demo_result(client) -> dict:
    sample = sample_data.SAMPLES[0]
    response = client.post("/api/audit", json={"sample_id": sample["id"]})
    assert response.status_code == 200, response.text
    return response.json()


def test_index_embeds_parsable_sample_json(client):
    html = client.get("/").text
    match = re.search(
        r'<script id="samples-data" type="application/json">(.*?)</script>', html, re.DOTALL
    )
    assert match, "샘플 JSON 스크립트가 없습니다."
    samples = json.loads(match.group(1))
    assert len(samples) == 3
    for sample in samples:
        for key in ("id", "label", "emoji", "store_name", "location", "industry", "channels"):
            assert key in sample, f"샘플에 {key}가 없습니다."
        assert "instagram" in sample["channels"]


def test_index_loads_static_assets(client):
    html = client.get("/").text
    assert "/static/app.js" in html
    assert "/static/styles.css" in html
    assert "cdn.tailwindcss.com" in html
    assert client.get("/static/app.js").status_code == 200
    assert client.get("/static/styles.css").status_code == 200


def test_audit_response_matches_frontend_contract(client):
    body = _demo_result(client)

    # 최상위
    for key in ("mode", "degraded", "warnings", "generated_at", "summary", "overall_score",
                "scores", "gaps", "contents", "sources", "demand", "queries", "pipeline", "input"):
        assert key in body, f"응답에 {key}가 없습니다."

    for key in ("store_name", "location", "industry", "channel_text"):
        assert body["input"][key], f"input.{key}가 비어 있습니다."

    assert body["demand"]["answer"]
    assert isinstance(body["demand"]["references"], list)

    for score in body["scores"]:
        for key in ("key", "label", "score", "comment"):
            assert key in score
        assert 0 <= score["score"] <= 100

    for gap in body["gaps"]:
        for key in ("title", "severity", "reason", "demand_evidence", "source_urls", "sources", "fix_hint", "dimension_label"):
            assert key in gap, f"결핍에 {key}가 없습니다."
        for source in gap["sources"]:
            assert source["url"].startswith("http")
            assert source["title"] and source["hostname"] and source["faviconUrl"]
            assert "demo" in source

    for content in body["contents"]:
        for key in ("channel", "channel_label", "title", "summary", "body", "hashtags", "caution"):
            assert key in content, f"콘텐츠에 {key}가 없습니다."

    for source in body["sources"]:
        for key in ("title", "url", "hostname", "faviconUrl", "description", "date", "demo"):
            assert key in source

    pipeline = body["pipeline"]
    for key in ("mode", "degraded", "model", "steps", "calls", "totals", "latency_ms", "base_url"):
        assert key in pipeline
    for step in pipeline["steps"]:
        for key in ("index", "title", "status", "latency_ms", "detail"):
            assert key in step
    for call in pipeline["calls"]:
        for key in ("endpoint", "mode", "status", "latency_ms", "request_id", "detail"):
            assert key in call
    for key in ("total", "search.web", "agents.search", "chat.completions", "live", "demo"):
        assert key in pipeline["totals"]


def test_app_js_references_existing_endpoints(client):
    js = APP_JS.read_text(encoding="utf-8")
    endpoints = set(re.findall(r"fetch\('(/api/[^']+)'", js))
    assert endpoints == {"/api/audit/stream"}
    for endpoint in endpoints:
        # 스트리밍 엔드포인트는 실제로 응답해야 한다.
        with client.stream(
            "POST", endpoint, json={"sample_id": sample_data.SAMPLES[2]["id"]}
        ) as response:
            assert response.status_code == 200
            assert "event: result" in "".join(response.iter_text())


def test_html_does_not_reference_missing_ids(client):
    """app.js가 찾는 DOM id가 템플릿에 모두 존재하는지 확인한다."""
    html = INDEX_HTML.read_text(encoding="utf-8")
    js = APP_JS.read_text(encoding="utf-8")
    ids = set(re.findall(r"\$\('#([a-zA-Z0-9\-]+)'\)", js))
    for element_id in ids:
        assert f'id="{element_id}"' in html, f"index.html에 #{element_id}가 없습니다."
