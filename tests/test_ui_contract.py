"""프런트엔드 계약 검증.

`app/static/app.js`는 /api/audit 응답의 특정 키를 직접 읽는다. 서버 응답 구조가 바뀌어
화면이 조용히 비는 일을 막기 위해, 데모/자동 수집/단일 검색어 응답이 JS가 기대하는 키를
모두 갖는지 확인한다. 템플릿 쪽은 "첫 화면은 검색창 하나"라는 UI 계약과
토스 톤 디자인 토큰을 함께 검증한다.
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


def _query_result(client) -> dict:
    response = client.post("/api/audit", json={"query": "성수동 모먼트 카페"})
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
    # Pretendard 웹폰트를 쓴다. (Tailwind CDN은 토스 톤 정리를 위해 걷어냈다.)
    assert "pretendard" in html.lower()
    assert "cdn.tailwindcss.com" not in html
    assert client.get("/static/app.js").status_code == 200
    assert client.get("/static/styles.css").status_code == 200


# --------------------------------------------------------------------------- #
# 첫 화면 계약: 검색창 하나 + 예시 알약 3개 + 접힌 고급 입력
# --------------------------------------------------------------------------- #

def test_single_search_input_is_the_only_main_field(client):
    html = client.get("/").text

    assert 'id="query"' in html
    query_tag = re.search(r'<input id="query"[^>]*>', html)
    assert query_tag, "query 입력이 없습니다."
    assert "예:" in html, "검색창에 예시 placeholder가 있어야 합니다."

    # 진단 CTA는 하나뿐이고 검색 카드 안에 있다.
    assert html.count('id="run-btn"') == 1
    card_at = html.index('class="card search-card"')
    cta_at = html.index('id="run-btn"')
    assert card_at < cta_at

    # 예시는 검색창 아래 작은 알약 3개.
    assert html.index('id="query"') < html.index('id="sample-list"')
    chips = re.findall(r'<button type="button" class="pill"[^>]*>([^<]+)</button>', html)
    assert len(chips) == 3, f"빠른 예시는 3개여야 합니다: {chips}"
    for label in ("성수동 카페", "서울대입구 피부과", "홍대 버거"):
        assert any(label in chip for chip in chips), f"{label} 예시가 없습니다."


def test_advanced_inputs_exist_but_are_hidden_by_toggle(client):
    html = client.get("/").text

    # 기존 입력 id는 접힌 고급 영역에 그대로 남아 있다(접근성·계약 호환).
    for element_id in ("location", "industry", "store_name", "channel_text", "api_key"):
        assert f'id="{element_id}"' in html, f"#{element_id}가 사라졌습니다."

    advanced_at = html.index('id="advanced"')
    assert "hidden" in re.search(r'<div class="advanced[^"]*"[^>]*id="advanced"[^>]*>|<div[^>]*id="advanced"[^>]*>', html).group(0)

    for element_id in ("location", "industry", "store_name", "channel_text"):
        tag = re.search(rf'<(?:input|textarea) id="{element_id}"[^>]*>', html)
        assert tag, f"#{element_id} 입력이 없습니다."
        assert advanced_at < html.index(tag.group(0)), f"#{element_id}가 고급 영역 밖에 있습니다."
        assert "required" not in tag.group(0), f"#{element_id}는 선택 입력이어야 합니다."

    # 채널 글은 토글 문구로 접혀 있다.
    assert html.index('id="channel_text"') > html.index('id="toggle-advanced"')
    assert "채널 글 직접 넣기" in html
    assert "API 설정" in html


def test_first_screen_stays_text_light(client):
    html = client.get("/").text

    # 히어로 문단·파이프라인 칩은 없애고 짧은 헤드라인만 남긴다.
    assert 'class="hero-flow"' not in html
    assert 'class="hero-lead"' not in html
    assert "flow-num" not in html
    headline = re.search(r'<h1 class="hero-title">(.*?)</h1>', html, re.DOTALL)
    assert headline, "헤드라인이 없습니다."
    plain = re.sub(r"<[^>]+>", "", headline.group(1)).strip()
    assert 0 < len(plain) <= 60, f"헤드라인이 너무 깁니다: {plain}"

    # 개발자용 텔레메트리·수집 쿼리 문구는 기본 화면에 노출하지 않는다.
    for noise in ("파이프라인", "텔레메트리", "Liner 인용 출처 분석"):
        assert noise not in html
    assert "자세히 보기" not in html  # 결과 렌더링 시에만 나온다.


def test_toss_design_tokens_are_applied():
    css = STYLES_CSS.read_text(encoding="utf-8").lower()
    for token in ("#f2f4f6", "#ffffff", "#3182f6", "#191f28", "#4e5968", "#8b95a1"):
        assert token in css, f"토스 토큰 {token}이 없습니다."
    assert "border-radius: 20px" in css or "--radius-card: 20px" in css
    assert "pretendard" in css
    assert "tabular-nums" in css
    assert "--pad-card: 24px" in css
    # 모바일 390px 1열 대응
    assert "@media (max-width: 480px)" in css

    for selector in (".search-field", ".cta", ".pill", ".card", ".score-big", ".metric-bar",
                     ".prop-bar", ".cite-row", ".cite-pct", ".gap-num", ".segments",
                     ".segment", ".tab-badge", ".copy-btn", ".more", ".mode-dot"):
        assert selector in css, f"{selector} 스타일이 없습니다."


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

    for key in ("query", "store_name", "location", "industry", "channel_text",
                "channel_text_chars", "auto_collect"):
        assert key in body["input"], f"input.{key}가 없습니다."
    assert body["input"]["auto_collect"] is True

    assert body["demand"]["answer"]
    assert isinstance(body["demand"]["references"], list)

    for score in body["scores"]:
        for key in ("key", "label", "score", "comment"):
            assert key in score
        assert 0 <= score["score"] <= 100

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
        for key in ("title", "severity", "reason", "demand_evidence", "source_urls", "sources",
                    "fix_hint", "dimension_label"):
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


def test_query_only_response_matches_frontend_contract(client):
    """한 줄 검색어만 보내도 위치·업종·매장명이 채워져 화면 계약을 만족한다."""
    body = _query_result(client)

    assert body["status"] == "ok"
    assert body["input"]["query"] == "성수동 모먼트 카페"
    assert body["input"]["location"] == "성수동"
    assert body["input"]["industry"] == "카페"
    assert body["input"]["store_name"] == "모먼트"
    # 검색 쿼리는 사용자가 쓴 한 줄에서 자연스럽게 이어진다.
    assert all("성수동 모먼트 카페" in query for query in body["queries"])
    assert len(body["gaps"]) == 3
    assert len(body["contents"]) == 3
    assert len(body["recommended_channels"]) == 3


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


def test_app_js_renders_the_four_result_cards():
    js = APP_JS.read_text(encoding="utf-8")
    for token in ("citation_analysis", "recommended_channels", "priority_badge", "prop-bar",
                  "segments", "copy-btn", "more", "score-big", "metric-bar", "gap-num"):
        assert token in js, f"app.js가 {token}를 렌더링하지 않습니다."

    # 입력은 단일 검색어(query) 중심이고, 응답의 input.query를 그대로 쓴다.
    assert "query" in js
    # 개발자용 텔레메트리는 접힌 '자세히 보기'로만 노출한다.
    assert "자세히 보기 (출처 · API 로그)" in js
    assert "파이프라인" not in js


def test_html_does_not_reference_missing_ids(client):
    """app.js가 찾는 DOM id가 템플릿에 모두 존재하는지 확인한다."""
    html = INDEX_HTML.read_text(encoding="utf-8")
    js = APP_JS.read_text(encoding="utf-8")
    ids = set(re.findall(r"\$\('#([a-zA-Z0-9\-]+)'\)", js))
    assert ids, "app.js가 참조하는 id가 없습니다."
    for element_id in ids:
        assert f'id="{element_id}"' in html, f"index.html에 #{element_id}가 없습니다."
