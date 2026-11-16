"""Geo Channel Auditor 검증.

* 데모 모드: 샘플 3종 + 직접 입력 (Liner API 미호출)
* 공개 채널 자동 수집: channel_text 없이 실행 (auto) / 짧은 문구 (hybrid) / 충분한 문구 (manual)
* 인용 출처 분석: 수집한 출처 URL의 채널 분류와 비중, 추천 채널 폴백
* 라이브 모드: httpx.MockTransport 로 Liner API 3종 응답을 흉내 내 실제 파싱 경로 검증
* 폴백: 라이브 호출 실패 시 데모 데이터로 대체되는지 검증
"""

from __future__ import annotations

import asyncio
import json
import re
from typing import Any

import httpx
import pytest

from app import samples as sample_data
from app.auditor import (
    CITATION_CHANNELS,
    build_citation_analysis,
    build_collection_queries,
    classify_channel,
)
from app.liner_client import LinerClient

SEARCH_URL = "https://blog.naver.com/moment_roastery/223456789"
SEARCH_INSTA_URL = "https://www.instagram.com/p/CxDemoReel1/"
AGENT_URL = "https://map.naver.com/p/entry/place/1234567"
LONG_CHANNEL_TEXT = sample_data.channel_text_of(sample_data.SAMPLES[0])


# --------------------------------------------------------------------------- #
# 라이브 모드 모의 응답
# --------------------------------------------------------------------------- #

def _router_payload(*, with_recommendations: bool = True) -> dict[str, Any]:
    payload: dict[str, Any] = {
        "overall_score": 63,
        "summary": "가격과 주차 정보가 비어 있어 비교 단계에서 밀립니다.",
        "scores": [
            {"key": "visit_convenience", "label": "방문편의", "score": 55, "comment": "주차 안내가 없습니다."},
            {"key": "price_transparency", "label": "가격투명성", "score": 40, "comment": "가격표가 없습니다."},
            {"key": "differentiation", "label": "차별화", "score": 78, "comment": "로스팅 서사가 있습니다."},
            {"key": "trust_evidence", "label": "신뢰근거", "score": 62, "comment": "후기 링크가 없습니다."},
        ],
        "gaps": [
            {
                "title": "주차 정보 부재",
                "severity": "high",
                "dimension": "visit_convenience",
                "reason": "주차 안내가 없습니다.",
                "demand_evidence": "'성수동 카페 주차' 검색이 반복됩니다.",
                "source_urls": [SEARCH_URL],
                "fix_hint": "주차 3줄을 소개 첫 문단에 넣으세요.",
            },
            {
                "title": "가격 정보 부재",
                "severity": "high",
                "dimension": "price_transparency",
                "reason": "가격표가 없습니다.",
                "demand_evidence": "가격 비교 글이 상위에 노출됩니다.",
                "source_urls": [AGENT_URL],
                "fix_hint": "대표 메뉴 가격을 공개하세요.",
            },
            {
                "title": "좌석 정보 부재",
                "severity": "medium",
                "dimension": "visit_convenience",
                "reason": "좌석·콘센트 안내가 없습니다.",
                "demand_evidence": "노트북 카페 질의가 있습니다.",
                "source_urls": [SEARCH_URL, AGENT_URL],
                "fix_hint": "좌석 수를 숫자로 적으세요.",
            },
        ],
        "contents": [
            {
                "channel": "instagram",
                "title": "주차부터 가격까지",
                "summary": "카드뉴스 5장",
                "body": "1장 표지\n2장 주차\n3장 가격\n4장 좌석\n5장 안내",
                "hashtags": ["#성수동카페"],
                "caution": "",
            },
            {
                "channel": "naver_place",
                "title": "방문 전 확인 안내",
                "summary": "플레이스 새소식",
                "body": "주차·가격·좌석 안내 본문",
                "hashtags": [],
                "caution": "",
            },
            {
                "channel": "blog_faq",
                "title": "자주 묻는 질문 3가지",
                "summary": "FAQ",
                "body": "Q1. 주차?\nA. ...\nQ2. 가격?\nA. ...",
                "hashtags": [],
                "caution": "의료 광고 시 심의 확인",
            },
        ],
    }
    if with_recommendations:
        payload["recommended_channels"] = [
            {
                "rank": 1,
                "channel": "naver_blog",
                "label": "네이버 블로그",
                "reason": "네이버 블로그 인용이 66.7%로 가장 높습니다. FAQ 글로 먼저 채우세요.",
                "priority_badge": "1순위",
            },
            {
                "rank": 2,
                "channel": "naver_place",
                "label": "네이버 플레이스",
                "reason": "플레이스 인용 33.3%. 가격·주차를 플레이스에 먼저 고정하세요.",
                "priority_badge": "2순위",
            },
            {
                "rank": 3,
                "channel": "review_media",
                "label": "리뷰·언론·기타",
                "reason": "리뷰 채널 비중이 낮아 검증 근거 보강이 필요합니다.",
                "priority_badge": "3순위",
            },
        ]
    return payload


def _make_transport(
    fail_status: int | None = None, *, with_recommendations: bool = True
) -> httpx.MockTransport:
    def handler(request: httpx.Request) -> httpx.Response:
        if fail_status is not None:
            return httpx.Response(fail_status, json={"error": "mock failure"})
        path = request.url.path
        if path.endswith("/tools/search/web"):
            body = json.loads(request.content)
            return httpx.Response(
                200,
                json={
                    "requestId": "req-search-1",
                    "totalCount": 2,
                    "results": [
                        {
                            "title": f"검색 결과: {body['query']}",
                            "url": SEARCH_URL,
                            "hostname": "blog.naver.com",
                            "faviconUrl": "https://blog.naver.com/favicon.ico",
                            "description": "상권 검색 수요 근거",
                            "date": "2026-11-01",
                        },
                        {
                            "title": f"릴스: {body['query']}",
                            "url": SEARCH_INSTA_URL,
                            "hostname": "www.instagram.com",
                            "description": "SNS 노출 근거",
                            "date": "2026-11-02",
                        },
                    ],
                },
            )
        if path.endswith("/agents/search"):
            return httpx.Response(
                200,
                json={
                    "requestId": "req-agent-1",
                    "answer": "고객은 주차·가격·좌석 정보를 먼저 확인합니다.",
                    "references": [
                        {
                            "title": "상권 수요 정리",
                            "url": AGENT_URL,
                            "hostname": "map.naver.com",
                            "description": "수요 요약",
                            "date": "2026-11-02",
                        }
                    ],
                },
            )
        if path.endswith("/chat/completions"):
            payload = _router_payload(with_recommendations=with_recommendations)
            return httpx.Response(
                200,
                json={
                    "id": "req-router-1",
                    "choices": [
                        {"message": {"role": "assistant", "content": "```json\n" + json.dumps(payload) + "\n```"}}
                    ],
                },
            )
        if path.endswith("/credits/balance"):
            return httpx.Response(200, json={"balance": 200})
        return httpx.Response(404, json={"error": "not found"})

    return httpx.MockTransport(handler)


@pytest.fixture()
def mock_transport(monkeypatch: pytest.MonkeyPatch):
    """LinerClient가 실제 네트워크 대신 MockTransport를 쓰도록 바꾸는 헬퍼."""

    def install(fail_status: int | None = None, *, with_recommendations: bool = True) -> None:
        original_init = LinerClient.__init__

        def patched_init(self, api_key=None, **kwargs):
            kwargs.setdefault(
                "transport",
                _make_transport(fail_status=fail_status, with_recommendations=with_recommendations),
            )
            original_init(self, api_key=api_key, **kwargs)

        monkeypatch.setattr(LinerClient, "__init__", patched_init)

    return install


# --------------------------------------------------------------------------- #
# 인용 출처 분류 (단위)
# --------------------------------------------------------------------------- #

@pytest.mark.parametrize(
    ("url", "expected"),
    [
        ("https://blog.naver.com/a/1", "naver_blog"),
        ("https://m.blog.naver.com/a/1", "naver_blog"),
        ("https://cafe.naver.com/abc/123", "naver_blog"),
        ("https://post.naver.com/viewer/postView.naver?volumeNo=1", "naver_blog"),
        ("https://place.naver.com/restaurant/123/home", "naver_place"),
        ("https://m.place.naver.com/restaurant/123", "naver_place"),
        ("https://map.naver.com/p/entry/place/123", "naver_place"),
        ("https://naver.me/abc123", "naver_place"),
        ("https://www.instagram.com/p/CxDemo/", "instagram_sns"),
        ("https://www.threads.net/@demo/post/1", "instagram_sns"),
        ("https://www.youtube.com/watch?v=abc", "instagram_sns"),
        ("https://youtu.be/abc", "instagram_sns"),
        ("https://www.tiktok.com/@demo/video/1", "instagram_sns"),
        ("https://www.facebook.com/demo/posts/1", "instagram_sns"),
        ("https://booking.naver.com/booking/12/bizes/123", "official_web"),
        ("https://smartstore.naver.com/demo", "official_web"),
        ("https://www.catchtable.net/restaurant/123", "official_web"),
        ("https://demo.tistory.com/12", "review_media"),
        ("https://brunch.co.kr/@demo/1", "review_media"),
        ("https://www.diningcode.com/profile.php?rid=abc", "review_media"),
        ("https://www.siksinhot.com/P/123", "review_media"),
        ("https://place.map.kakao.com/123", "review_media"),
        ("https://news.example.co.kr/article/1", "review_media"),
        ("https://search.naver.com/search.naver?where=blog&query=%EC%84%B1%EC%88%98", "naver_blog"),
        ("https://search.naver.com/search.naver?where=place&query=%EC%84%B1%EC%88%98", "naver_place"),
    ],
)
def test_classify_channel_by_domain(url: str, expected: str) -> None:
    assert classify_channel(url) == expected


def test_classify_channel_prefers_declared_hint() -> None:
    """데모 픽스처가 채널을 선언하면 도메인보다 그 값을 먼저 쓴다."""
    assert classify_channel("https://example.com/x", "instagram_sns") == "instagram_sns"


def test_citation_analysis_shares_sum_to_100() -> None:
    urls = (
        ["https://blog.naver.com/a/1", "https://blog.naver.com/a/2", "https://m.blog.naver.com/b/3"]
        + ["https://map.naver.com/p/entry/place/1", "https://place.naver.com/restaurant/2/home"]
        + ["https://www.instagram.com/p/x/", "https://demo.tistory.com/1"]
    ) + ["https://blog.naver.com/a/1"]  # 중복 URL은 한 번만 센다
    sources = [{"url": url, "hostname": url.split("/")[2]} for url in urls]
    citation = build_citation_analysis(sources)

    assert citation["total"] == 7
    assert sum(channel["count"] for channel in citation["channels"]) == 7
    assert round(sum(channel["share_pct"] for channel in citation["channels"]), 1) == 100.0
    assert [channel["channel"] for channel in citation["channels"]][:2] == ["naver_blog", "naver_place"]
    assert citation["top_channel"] == "naver_blog"
    assert citation["top_share_pct"] == pytest.approx(42.8, abs=0.05)
    assert citation["channels"][0]["domains"] == ["blog.naver.com", "m.blog.naver.com"]
    assert all(channel["label"] for channel in citation["channels"])
    assert {channel["channel"] for channel in citation["channels"]} == {key for key, _ in CITATION_CHANNELS}


def test_citation_analysis_handles_no_sources() -> None:
    citation = build_citation_analysis([])
    assert citation["total"] == 0
    assert citation["top_channel"] is None
    assert sum(channel["share_pct"] for channel in citation["channels"]) == 0


def test_content_badge_keeps_real_rank_when_top_channel_has_no_content() -> None:
    """1순위가 공식 웹·리뷰처럼 만들 수 없는 채널이면 다음 채널에 실제 순위로 배지를 단다."""
    from app.auditor import apply_content_priority

    contents = [
        {"channel": "instagram", "body": ""},
        {"channel": "naver_place", "body": ""},
        {"channel": "blog_faq", "body": ""},
    ]
    recommendations = [
        {"rank": 1, "channel": "review_media", "content_channel": None, "reason": "리뷰 비중이 가장 큽니다."},
        {"rank": 2, "channel": "instagram_sns", "content_channel": "instagram", "reason": "SNS 인용이 13.7%."},
    ]
    apply_content_priority(contents, recommendations)

    assert contents[0]["priority_badge"] == "2순위 추천"
    assert contents[0]["priority_rank"] == 2
    assert contents[0]["priority_reason"] == "SNS 인용이 13.7%."
    assert contents[1]["priority_badge"] == ""
    assert contents[2]["priority_badge"] == ""


def test_router_timeout_is_longer_than_search_timeout(monkeypatch) -> None:
    monkeypatch.setenv("LINER_TIMEOUT_SECONDS", "5")
    monkeypatch.setenv("LINER_ROUTER_TIMEOUT_SECONDS", "90")
    client = LinerClient(api_key="test-key")
    assert client.timeout == 5.0
    assert client.router_timeout == 90.0


def test_chat_completions_requests_router_timeout(monkeypatch) -> None:
    seen: dict[str, Any] = {}

    def handler(request: httpx.Request) -> httpx.Response:
        seen["timeout"] = request.extensions.get("timeout")
        return httpx.Response(200, json={"choices": [{"message": {"content": "{}"}}]})

    monkeypatch.setenv("LINER_ROUTER_TIMEOUT_SECONDS", "90")
    client = LinerClient(api_key="test-key", transport=httpx.MockTransport(handler))
    asyncio.run(client.chat_completions([{"role": "user", "content": "hi"}]))
    timeout = seen.get("timeout") or {}
    assert (timeout.get("read") or 0) >= 90


def test_build_collection_queries_uses_store_and_location() -> None:
    from app.auditor import AuditInput

    audit = AuditInput(location="서울 성동구 성수동2가", industry="스페셜티 카페", store_name="모먼트 성수점")
    queries = build_collection_queries(audit)
    assert queries[0] == "서울 성동구 성수동2가 모먼트 성수점"
    assert "네이버 플레이스" in queries[1]
    assert len(queries) == 2

    no_store = build_collection_queries(AuditInput(location="서울 마포구 연남동", industry="베이커리"))
    assert no_store[0] == "서울 마포구 연남동 베이커리"


# --------------------------------------------------------------------------- #
# 단일 검색창 입력 파싱
# --------------------------------------------------------------------------- #

@pytest.mark.parametrize(
    ("query", "location", "industry", "store_name"),
    [
        ("성수동 모먼트 카페", "성수동", "카페", "모먼트"),
        ("서울대입구 브라이트 피부과", "서울대입구", "피부과", "브라이트"),
        ("강남역 네일샵", "강남역", "네일샵", ""),
        ("홍대 버거", "홍대", "버거", ""),
        ("연남동 베이커리", "연남동", "베이커리", ""),
        ("성수동, 카페", "성수동", "카페", ""),
        ("서울 성동구 성수동2가 315-12 카페", "서울 성동구 성수동2가 315-12", "카페", ""),
    ],
)
def test_parse_search_query_splits_korean_query(query, location, industry, store_name) -> None:
    from app.auditor import parse_search_query

    parsed = parse_search_query(query)
    assert parsed["location"] == location
    assert parsed["industry"] == industry
    assert parsed["store_name"] == store_name
    assert parsed["query"] == query


def test_parse_search_query_falls_back_safely_on_ambiguous_input() -> None:
    from app.auditor import FALLBACK_INDUSTRY, parse_search_query

    parsed = parse_search_query("브라이트")
    # 나눌 수 없으면 원문을 위치·매장명으로 두고 업종만 일반화한다(검색 품질 보존).
    assert parsed["location"] == "브라이트"
    assert parsed["store_name"] == "브라이트"
    assert parsed["industry"] == FALLBACK_INDUSTRY
    assert parsed["location_matched"] is False

    empty = parse_search_query("   ")
    assert empty["location"] == "" and empty["industry"] == "" and empty["store_name"] == ""


def test_build_queries_uses_single_query_text() -> None:
    from app.auditor import build_queries

    queries = build_queries("성수동", "카페", None, query="성수동 모먼트 카페")
    assert queries[0].startswith("성수동 모먼트 카페 ")
    assert len(queries) == 3

    # 필드만 준 기존 호출도 그대로 동작한다(중복 토큰은 한 번만).
    legacy = build_queries("성수동", "카페", None, store_name="성수동")
    assert legacy[0].startswith("성수동 카페 ")

    sample_queries = build_queries("성수동", "카페", sample_data.SAMPLES[0]["id"])
    assert sample_queries == sample_data.SAMPLES[0]["queries"]


def test_audit_input_prefers_explicit_fields_over_query() -> None:
    from app.auditor import AuditInput

    audit = AuditInput.from_payload(
        {"query": "성수동 모먼트 카페", "location": "서울 성동구 성수동2가", "industry": "스페셜티 카페"}
    )
    assert audit.location == "서울 성동구 성수동2가"
    assert audit.industry == "스페셜티 카페"
    assert audit.query == "성수동 모먼트 카페"

    query_only = AuditInput.from_payload({"query": "강남역 네일샵"})
    assert (query_only.location, query_only.industry, query_only.store_name) == ("강남역", "네일샵", "")
    assert query_only.display_name == "강남역 네일샵"


# --------------------------------------------------------------------------- #
# 헬스체크 / 샘플
# --------------------------------------------------------------------------- #

def test_health_demo_mode(client):
    response = client.get("/api/health")
    assert response.status_code == 200
    body = response.json()
    assert body["status"] == "ok"
    assert body["mode"] == "demo"
    assert body["key_configured"] is False
    assert body["model"] == "liner-gpt-router"
    assert body["base_url"].endswith("/api/v1")
    assert "test-key" not in json.dumps(body)


def test_samples_endpoint(client):
    response = client.get("/api/samples")
    assert response.status_code == 200
    body = response.json()
    assert len(body["samples"]) == 3
    ids = {s["id"] for s in body["samples"]}
    assert ids == {"seongsu-cafe", "snu-derm", "sanbon-burger"}
    for sample in body["samples"]:
        assert sample["store_name"]
        assert set(sample["channels"]) == {"instagram", "naver_place", "blog"}
        assert len(sample["queries"]) >= 2
    assert "가상" in body["notice"]


def test_index_page_renders(client):
    response = client.get("/")
    assert response.status_code == 200
    html = response.text
    assert "Geo Channel Auditor" in html
    # 첫 화면은 단일 검색창이고 모드 배지는 LIVE/DEMO 점으로 표시한다.
    assert 'id="query"' in html
    assert re.search(r'id="mode-label">(LIVE|DEMO)<', html)
    # 가상 매장 3종은 예시 데이터로 템플릿에 실린다(JSON은 \uXXXX로 이스케이프된다).
    match = re.search(r'<script id="samples-data" type="application/json">(.*?)</script>', html, re.DOTALL)
    assert match
    store_names = {sample["store_name"] for sample in json.loads(match.group(1))}
    assert store_names == {"모먼트 로스터리 성수점", "브라이트 피부과의원", "버거워크 산본점"}


# --------------------------------------------------------------------------- #
# 데모 모드 진단
# --------------------------------------------------------------------------- #

@pytest.mark.parametrize("sample", sample_data.SAMPLES, ids=lambda s: s["id"])
def test_audit_demo_all_samples(client, sample):
    payload = {
        "sample_id": sample["id"],
        "location": sample["location"],
        "industry": sample["industry"],
        "store_name": sample["store_name"],
        "channel_text": sample_data.channel_text_of(sample),
    }
    response = client.post("/api/audit", json=payload)
    assert response.status_code == 200, response.text
    body = response.json()

    assert body["mode"] == "demo"
    assert body["degraded"] is False
    assert body["status"] == "ok"
    assert isinstance(body["overall_score"], int) and 0 <= body["overall_score"] <= 100
    assert len(body["scores"]) == 4
    assert [s["key"] for s in body["scores"]] == [
        "visit_convenience",
        "price_transparency",
        "differentiation",
        "trust_evidence",
    ]
    assert len(body["gaps"]) == 3
    assert len(body["contents"]) == 3
    assert [c["channel"] for c in body["contents"]] == ["instagram", "naver_place", "blog_faq"]
    assert body["sources"], "출처가 비어 있으면 안 됩니다."
    assert body["demand"]["answer"]

    # 채널 텍스트를 충분히 넣었으니 자동 수집 없이 직접 입력으로 진단한다.
    assert body["collected_channel"]["mode"] == "manual"
    assert body["collected_channel"]["source_count"] == 0
    assert body["collected_channel"]["label"] == "직접 입력"
    assert body["input"]["channel_text_chars"] > 0

    # 인용 출처 분석 + 채널 우선순위 추천은 항상 채워진다.
    citation = body["citation_analysis"]
    assert citation["total"] == len(body["sources"])
    assert round(sum(channel["share_pct"] for channel in citation["channels"]), 1) == 100.0
    recommendations = body["recommended_channels"]
    assert [rec["rank"] for rec in recommendations] == [1, 2, 3]
    assert all(rec["reason"] for rec in recommendations)
    assert all(rec["priority_badge"].endswith("순위") for rec in recommendations)
    # 1순위 추천 채널에 해당하는 콘텐츠 탭이 표시된다.
    badged = [content for content in body["contents"] if content["priority_badge"]]
    assert len(badged) == 1
    assert badged[0]["priority_badge"] == "1순위 추천"

    # 결핍마다 근거 출처 링크가 붙어야 한다.
    for gap in body["gaps"]:
        assert gap["title"] and gap["reason"] and gap["demand_evidence"]
        assert gap["severity"] in {"high", "medium", "low"}
        assert gap["source_urls"], "결핍에 근거 URL이 필요합니다."
        assert all(url.startswith("http") for url in gap["source_urls"])

    # 세 채널 콘텐츠 모두 발행 가능한 본문 길이를 갖는다.
    for content in body["contents"]:
        assert len(content["body"]) > 120
        assert content["title"]

    # 파이프라인 투명성: 검색 3회 + 에이전트 1회 + 라우터 1회
    totals = body["pipeline"]["totals"]
    assert totals["search.web"] == len(body["queries"]) == 3
    assert totals["agents.search"] == 1
    assert totals["chat.completions"] == 1
    assert totals["total"] == 5
    assert len(body["pipeline"]["steps"]) == 3
    assert all(step["status"] == "done" for step in body["pipeline"]["steps"])


def test_audit_demo_custom_input(client):
    payload = {
        "location": "서울 마포구 연남동",
        "industry": "베이커리 카페",
        "store_name": "테스트 베이커리",
        "channel_text": "연남동 골목 작은 빵집입니다. 매일 아침 직접 반죽합니다. 영업시간 09:00-19:00.",
    }
    response = client.post("/api/audit", json=payload)
    assert response.status_code == 200, response.text
    body = response.json()
    assert body["mode"] == "demo"
    assert len(body["gaps"]) == 3
    assert len(body["contents"]) == 3
    assert any("데모 템플릿" in w for w in body["warnings"])
    # 입력에 없는 정보(가격·주차 등)가 결핍으로 잡혀야 한다.
    titles = " ".join(gap["title"] for gap in body["gaps"])
    assert "가격" in titles or "주차" in titles
    # 짧은 문구이므로 자동 수집이 붙는다.
    assert body["collected_channel"]["mode"] == "hybrid"


def test_audit_auto_collects_channels_without_channel_text(client):
    """매장명·상권 위치만으로도 진단이 돌아가고 공개 채널을 스스로 모은다."""
    payload = {
        "location": "서울 성동구 성수동2가",
        "industry": "스페셜티 카페",
        "store_name": "테스트 로스터리",
        "channel_text": "",
    }
    response = client.post("/api/audit", json=payload)
    assert response.status_code == 200, response.text
    body = response.json()

    assert body["degraded"] is False
    collected = body["collected_channel"]
    assert collected["mode"] == "auto"
    assert collected["label"] == "자동 수집"
    assert collected["auto"] is True
    assert collected["source_count"] > 0
    assert collected["queries"] and len(collected["queries"]) == 2
    assert collected["summary"]
    assert collected["input_chars"] == 0
    assert collected["sources"], "자동 수집 출처가 응답에 포함되어야 합니다."
    assert all(source["origin"] == "collect" for source in collected["sources"])

    # 자동 수집분이 인용 분석과 파이프라인에 반영된다.
    assert body["citation_analysis"]["total"] >= collected["source_count"] - 1
    totals = body["pipeline"]["totals"]
    assert totals["search.web"] == len(body["queries"]) + len(collected["queries"]) == 5
    assert body["pipeline"]["steps"][0]["counts"]["collected"] > 0
    assert len(body["recommended_channels"]) == 3
    assert body["contents"][0]["channel"] or body["contents"][1]["channel"]


def test_audit_can_disable_auto_collect(client):
    payload = {
        "location": "서울 성동구 성수동2가",
        "industry": "스페셜티 카페",
        "channel_text": "",
        "auto_collect": False,
    }
    response = client.post("/api/audit", json=payload)
    assert response.status_code == 200, response.text
    body = response.json()
    assert body["collected_channel"]["mode"] == "manual"
    assert body["collected_channel"]["auto"] is False
    assert body["collected_channel"]["source_count"] == 0
    assert body["pipeline"]["totals"]["search.web"] == 3


def test_audit_requires_location_and_industry(client):
    response = client.post("/api/audit", json={"location": "", "industry": "", "channel_text": ""})
    assert response.status_code == 400
    assert "입력" in response.json()["detail"]


def test_audit_stream_emits_steps_and_result(client):
    sample = sample_data.SAMPLES[0]
    payload = {"sample_id": sample["id"], "channel_text": sample_data.channel_text_of(sample)}
    with client.stream("POST", "/api/audit/stream", json=payload) as response:
        assert response.status_code == 200
        assert response.headers["content-type"].startswith("text/event-stream")
        text = "".join(response.iter_text())

    assert text.count("event: step") == 6  # 3단계 × (running, done)
    assert "event: result" in text
    events = [line for line in text.splitlines() if line.startswith("event:")]
    assert events[0] == "event: open"
    result_line = [line for line in text.splitlines() if line.startswith("data:")][-1]
    result = json.loads(result_line[5:])
    assert result["overall_score"] >= 0
    assert len(result["gaps"]) == 3
    assert len(result["recommended_channels"]) == 3


def test_audit_stream_auto_collect_emits_extra_step_detail(client):
    with client.stream(
        "POST", "/api/audit/stream", json={"location": "서울 마포구 연남동", "industry": "베이커리"}
    ) as response:
        assert response.status_code == 200
        text = "".join(response.iter_text())

    result_line = [line for line in text.splitlines() if line.startswith("data:")][-1]
    result = json.loads(result_line[5:])
    assert result["collected_channel"]["mode"] == "auto"
    assert "자동 수집" in text


# --------------------------------------------------------------------------- #
# 라이브 모드 (Liner API 모의)
# --------------------------------------------------------------------------- #

def test_audit_live_mode_uses_liner_api(client, monkeypatch, mock_transport):
    monkeypatch.setenv("LINER_API_KEY", "test-key-do-not-leak")
    mock_transport()

    payload = {
        "sample_id": "seongsu-cafe",
        "location": "서울 성동구 성수동2가",
        "industry": "스페셜티 카페",
        "channel_text": LONG_CHANNEL_TEXT,
    }
    response = client.post("/api/audit", json=payload)
    assert response.status_code == 200, response.text
    body = response.json()

    assert body["mode"] == "live"
    assert body["degraded"] is False
    assert body["warnings"] == []
    assert body["pipeline"]["totals"]["live"] == 5
    assert body["pipeline"]["totals"]["demo"] == 0
    assert [s["score"] for s in body["scores"]] == [55, 40, 78, 62]
    assert body["overall_score"] == 63
    assert body["demand"]["answer"].startswith("고객은 주차")
    # router JSON이 코드펜스로 감싸져 있어도 파싱되어야 한다.
    assert body["gaps"][0]["title"] == "주차 정보 부재"
    assert body["gaps"][0]["sources"][0]["url"] == SEARCH_URL
    assert body["pipeline"]["calls"][0]["request_id"] == "req-search-1"

    # 인용 출처 분석은 실제 수집 URL의 도메인으로 계산된다.
    citation = body["citation_analysis"]
    assert citation["total"] == len(body["sources"])
    assert citation["top_channel"] == "naver_blog"
    assert {channel["channel"] for channel in citation["channels"] if channel["count"]} == {
        "naver_blog",
        "naver_place",
        "instagram_sns",
    }
    # Router가 준 추천이 그대로 반영된다.
    assert [rec["channel"] for rec in body["recommended_channels"]] == [
        "naver_blog",
        "naver_place",
        "review_media",
    ]
    assert all(rec["source"] == "router" for rec in body["recommended_channels"])
    assert "66.7%" in body["recommended_channels"][0]["reason"]
    # 1순위 추천(network blog) → blog_faq 콘텐츠에 배지
    blog = next(content for content in body["contents"] if content["channel"] == "blog_faq")
    assert blog["priority_badge"] == "1순위 추천"
    assert blog["priority_rank"] == 1

    # 요청에 넣은 API 키는 응답 어디에도 나타나지 않아야 한다.
    assert "test-key-do-not-leak" not in response.text


def test_audit_live_auto_collect_adds_two_searches(client, monkeypatch, mock_transport):
    """channel_text 없이 호출하면 라이브에서도 공개 채널 수집 검색 2회가 추가된다."""
    monkeypatch.setenv("LINER_API_KEY", "test-key-do-not-leak")
    mock_transport()

    response = client.post(
        "/api/audit",
        json={"location": "서울 성동구 성수동2가", "industry": "스페셜티 카페", "store_name": "테스트 로스터리"},
    )
    assert response.status_code == 200, response.text
    body = response.json()

    assert body["mode"] == "live"
    assert body["degraded"] is False
    collected = body["collected_channel"]
    assert collected["mode"] == "auto"
    assert collected["queries"]
    totals = body["pipeline"]["totals"]
    assert totals["search.web"] == 5
    assert totals["total"] == 7
    assert totals["live"] == 7
    assert collected["source_count"] == 2  # 모의 응답의 서로 다른 URL 2건
    collect_calls = [call for call in body["pipeline"]["calls"] if (call["detail"] or "").startswith("collect")]
    assert len(collect_calls) == 2
    assert len(body["recommended_channels"]) == 3


def test_audit_live_hybrid_when_channel_text_is_short(client, monkeypatch, mock_transport):
    monkeypatch.setenv("LINER_API_KEY", "test-key-do-not-leak")
    mock_transport()

    response = client.post(
        "/api/audit",
        json={
            "location": "서울 성동구 성수동2가",
            "industry": "스페셜티 카페",
            "channel_text": "성수동 로스터리 카페입니다.",
        },
    )
    assert response.status_code == 200, response.text
    body = response.json()
    assert body["collected_channel"]["mode"] == "hybrid"
    assert body["collected_channel"]["input_chars"] > 0
    assert body["pipeline"]["totals"]["search.web"] == 5


def test_recommendations_fallback_when_router_omits(client, monkeypatch, mock_transport):
    """Router가 추천을 빠뜨려도 인용 비중 기반 결정론적 추천으로 채운다."""
    monkeypatch.setenv("LINER_API_KEY", "test-key-do-not-leak")
    mock_transport(with_recommendations=False)

    response = client.post(
        "/api/audit",
        json={
            "location": "서울 성동구 성수동2가",
            "industry": "스페셜티 카페",
            "channel_text": LONG_CHANNEL_TEXT,
        },
    )
    assert response.status_code == 200, response.text
    body = response.json()

    recommendations = body["recommended_channels"]
    assert len(recommendations) == 3
    assert [rec["rank"] for rec in recommendations] == [1, 2, 3]
    assert all(rec["source"] == "citation" for rec in recommendations)
    assert recommendations[0]["channel"] == body["citation_analysis"]["top_channel"]
    assert all(rec["priority_badge"] == f"{rec['rank']}순위" for rec in recommendations)
    assert all(str(rec["share_pct"]) in rec["reason"] for rec in recommendations)


def test_audit_live_mode_falls_back_to_demo_on_api_error(client, monkeypatch, mock_transport):
    monkeypatch.setenv("LINER_API_KEY", "test-key-do-not-leak")
    mock_transport(fail_status=500)

    payload = {
        "sample_id": "seongsu-cafe",
        "channel_text": sample_data.channel_text_of(sample_data.SAMPLES[0]),
    }
    response = client.post("/api/audit", json=payload)
    assert response.status_code == 200, response.text
    body = response.json()

    assert body["degraded"] is True
    assert body["mode"] == "demo"
    assert any("실패" in w for w in body["warnings"])
    assert len(body["gaps"]) == 3
    assert len(body["contents"]) == 3
    assert len(body["recommended_channels"]) == 3


def test_request_api_key_is_not_echoed(client, mock_transport):
    """요청 본문으로 받은 키는 응답·텔레메트리 어디에도 남지 않아야 한다."""
    mock_transport(fail_status=401)
    sample = sample_data.SAMPLES[1]
    secret = "lin_live_SUPER_SECRET_VALUE_123"
    response = client.post(
        "/api/audit",
        json={
            "sample_id": sample["id"],
            "channel_text": sample_data.channel_text_of(sample),
            "api_key": secret,
        },
    )
    assert response.status_code == 200, response.text
    body = response.json()
    assert secret not in response.text
    assert body["input"]["api_key_source"] == "request"
    assert body["degraded"] is True
    assert "Liner API" in " ".join(body["warnings"])


def test_health_probe_does_not_break_without_key(client):
    response = client.get("/api/health?probe=true")
    assert response.status_code == 200
    assert response.json()["status"] == "ok"
