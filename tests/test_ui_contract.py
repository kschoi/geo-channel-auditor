"""프런트엔드 계약 검증.

`app/static/app.js`는 /api/audit 응답의 특정 키를 직접 읽는다. 서버 응답 구조가 바뀌어
화면이 조용히 비는 일을 막기 위해, 데모/자동 수집 응답이 JS가 기대하는 키를 모두 갖는지 확인한다.
템플릿 쪽도 \"입력 폼이 첫 화면에 오는가(직접 입력 우선)\" 같은 UI 계약을 함께 검증한다.
"""

from __future__ import annotations

import json
import re
from pathlib import Path

from app import samples as sample_data

ROOT = Path(__file__).resolve().parents[1]
INDEX_HTML = ROOT / "app" / "templates" / "index.html"
APP_JS = ROOT / "app" / "static" / "app.js"
STYLES_CSS = ROOT / "app" / "static" / "styles.css"


def _demo_result(client) -> dict:
    sample = sample_data.SAMPLES[0]
    response = client.post("/api/audit", json={"sample_id": sample["id"]})
    assert response.status_code == 200, response.text
    return response.json()


def _auto_result(client) -> dict:
    response = client.post(
        "/api/audit",
        json={"location": "서울 마포구 연남동", "industry": "베이커리 카페", "store_name": "계약 테스트"},
    )
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
        for key in ("id", "label", "emoji", "store_name", "location", "industry", "channels", "one_liner"):
            assert key in sample, f"샘플에 {key}가 없습니다."
        assert "instagram" in sample["channels"]


def test_index_loads_static_assets(client):
    html = client.get("/").text
    assert "/static/app.js" in html
    assert "/static/styles.css" in html
    assert "cdn.tailwindcss.com" in html
    assert client.get("/static/app.js").status_code == 200
    assert client.get("/static/styles.css").status_code == 200


# --------------------------------------------------------------------------- #
# 입력 화면 계약: 직접 입력이 먼저, 채널 텍스트는 접힌 선택 입력, 샘플은 맨 아래
# --------------------------------------------------------------------------- #

def test_input_form_comes_first_and_channel_text_is_optional(client):
    html = client.get("/").text

    form_at = html.index('id="audit-form"')
    samples_at = html.index('id="sample-list"')
    assert form_at < samples_at, "직접 입력 폼이 빠른 예시(샘플)보다 위에 있어야 합니다."
    assert "빠른 예시" in html[samples_at - 400 : samples_at]

    # 채널 텍스트는 <details> 안의 선택 입력이고 required 가 아니다.
    textarea_at = html.index('id="channel_text"')
    details_at = html.rindex("<details", 0, textarea_at)
    assert "</details>" in html[textarea_at:], "채널 텍스트가 접힌 영역에 있어야 합니다."
    assert details_at < textarea_at
    textarea_tag = html[textarea_at : textarea_at + 600].split(">")[0]
    assert "required" not in textarea_tag

    assert "자동 수집" in html
    assert "공개 채널" in html
    # 위치·업종은 여전히 필수
    for required_id in ("location", "industry"):
        tag = re.search(rf'<input id="{required_id}"[^>]*>', html)
        assert tag and "required" in tag.group(0)


def test_intro_is_compact_without_stacked_steps(client):
    """첫 화면 밀도: 세로 3단 01/02/03 스택을 없애고 한 줄 파이프라인만 둔다."""
    html = client.get("/").text
    assert 'class="flow"' not in html
    assert "flow-num" not in html
    assert 'class="hero-flow"' in html
    assert "Liner Search" in html and "Search Agent" in html and "Router" in html
    # 소개 문단은 한두 줄 분량으로 짧게 유지한다.
    lead = re.search(r'<p class="hero-lead">(.*?)</p>', html, re.DOTALL)
    assert lead and len(lead.group(1)) < 160


def test_result_views_have_citation_and_recommendation_styles():
    css = STYLES_CSS.read_text(encoding="utf-8")
    for selector in (".prop-bar", ".cite-row", ".cite-pct", ".rec-hero", ".rec-badge", ".tab-badge",
                     ".collect-badge", ".autocollect-line", ".sample-chip"):
        assert selector in css, f"{selector} 스타일이 없습니다."
    assert "tabular-nums" in css
    # 모바일 390px 대응
    assert "@media (max-width: 480px)" in css


# --------------------------------------------------------------------------- #
# 응답 계약
# --------------------------------------------------------------------------- #

def test_audit_response_matches_frontend_contract(client):
    body = _demo_result(client)

    # 최상위
    for key in ("mode", "degraded", "warnings", "generated_at", "summary", "overall_score",
                "scores", "gaps", "contents", "sources", "demand", "queries", "pipeline", "input",
                "collected_channel", "citation_analysis", "recommended_channels"):
        assert key in body, f"응답에 {key}가 없습니다."

    for key in ("store_name", "location", "industry", "channel_text", "channel_text_chars", "auto_collect"):
        assert key in body["input"], f"input.{key}가 없습니다."
    assert body["input"]["channel_text"]
    assert body["input"]["auto_collect"] is True

    assert body["demand"]["answer"]
    assert isinstance(body["demand"]["references"], list)

    for score in body["scores"]:
        for key in ("key", "label", "score", "comment"):
            assert key in score
        assert 0 <= score["score"] <= 100

    # 수집된 매장 채널 현황
    collected = body["collected_channel"]
    for key in ("mode", "label", "summary", "source_count", "queries", "sources",
                "input_chars", "auto", "breakdown"):
        assert key in collected, f"collected_channel.{key}가 없습니다."
    assert collected["mode"] in {"manual", "auto", "hybrid"}

    # Liner 인용 출처 분석 (Liner Search + Liner Search Agent)
    citation = body["citation_analysis"]
    for key in ("total", "summary", "channels", "top_channel", "top_label", "top_share_pct"):
        assert key in citation, f"citation_analysis.{key}가 없습니다."
    assert len(citation["channels"]) == 5
    for channel in citation["channels"]:
        for key in ("channel", "label", "count", "share_pct", "domains", "urls"):
            assert key in channel, f"인용 채널에 {key}가 없습니다."
        assert 0 <= channel["share_pct"] <= 100
        assert isinstance(channel["domains"], list) and isinstance(channel["urls"], list)
    assert sum(channel["share_pct"] for channel in citation["channels"]) == 100.0

    # 채널 우선순위 추천
    recommendations = body["recommended_channels"]
    assert len(recommendations) == 3
    for index, item in enumerate(recommendations, start=1):
        for key in ("rank", "channel", "label", "reason", "priority_badge", "share_pct", "count"):
            assert key in item, f"추천에 {key}가 없습니다."
        assert item["rank"] == index
        assert item["reason"]
    assert recommendations[0]["channel"] == citation["top_channel"]

    for gap in body["gaps"]:
        for key in ("title", "severity", "reason", "demand_evidence", "source_urls", "sources", "fix_hint", "dimension_label"):
            assert key in gap, f"결핍에 {key}가 없습니다."
        for source in gap["sources"]:
            assert source["url"].startswith("http")
            assert source["title"] and source["hostname"] and source["faviconUrl"]
            assert "demo" in source

    for content in body["contents"]:
        for key in ("channel", "channel_label", "title", "summary", "body", "hashtags", "caution",
                    "priority_rank", "priority_badge", "priority_reason"):
            assert key in content, f"콘텐츠에 {key}가 없습니다."
    assert sum(1 for content in body["contents"] if content["priority_badge"]) == 1

    for source in body["sources"]:
        for key in ("title", "url", "hostname", "faviconUrl", "description", "date", "demo", "origin"):
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


def test_auto_collect_response_matches_frontend_contract(client):
    body = _auto_result(client)
    collected = body["collected_channel"]
    assert collected["mode"] == "auto"
    assert collected["queries"]
    assert collected["source_count"] > 0
    assert collected["breakdown"], "수집 채널 분포가 있어야 합니다."
    assert body["input"]["channel_text_chars"] == 0
    assert len(body["recommended_channels"]) == 3
    assert body["citation_analysis"]["total"] > 0


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


def test_app_js_renders_new_analysis_sections():
    js = APP_JS.read_text(encoding="utf-8")
    for token in ("collected_channel", "citation_analysis", "recommended_channels",
                  "priority_badge", "prop-bar", "rec-hero", "collect-badge"):
        assert token in js, f"app.js가 {token}를 렌더링하지 않습니다."
    # 채널 텍스트는 이제 선택 입력이다.
    assert "채널 텍스트를 입력해 주세요" not in js


def test_html_does_not_reference_missing_ids(client):
    """app.js가 찾는 DOM id가 템플릿에 모두 존재하는지 확인한다."""
    html = INDEX_HTML.read_text(encoding="utf-8")
    js = APP_JS.read_text(encoding="utf-8")
    ids = set(re.findall(r"\$\('#([a-zA-Z0-9\-]+)'\)", js))
    for element_id in ids:
        assert f'id="{element_id}"' in html, f"index.html에 #{element_id}가 없습니다."
