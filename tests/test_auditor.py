"""Geo Channel Auditor 검증.

* 데모 모드: 샘플 3종 + 직접 입력 (Liner API 미호출)
* 라이브 모드: httpx.MockTransport 로 Liner API 3종 응답을 흉내 내 실제 파싱 경로 검증
* 폴백: 라이브 호출 실패 시 데모 데이터로 대체되는지 검증
"""

from __future__ import annotations

import json
from typing import Any

import httpx
import pytest

from app import samples as sample_data
from app.liner_client import LinerClient

SEARCH_URL = "https://example.com/evidence/seongsu-parking"
AGENT_URL = "https://example.com/evidence/seongsu-demand"


# --------------------------------------------------------------------------- #
# 라이브 모드 모의 응답
# --------------------------------------------------------------------------- #

def _router_payload() -> dict[str, Any]:
    return {
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


def _make_transport(fail_status: int | None = None) -> httpx.MockTransport:
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
                    "totalCount": 1,
                    "results": [
                        {
                            "title": f"검색 결과: {body['query']}",
                            "url": SEARCH_URL,
                            "hostname": "example.com",
                            "faviconUrl": "https://example.com/favicon.ico",
                            "description": "상권 검색 수요 근거",
                            "date": "2026-11-01",
                        }
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
                            "hostname": "example.com",
                            "description": "수요 요약",
                            "date": "2026-11-02",
                        }
                    ],
                },
            )
        if path.endswith("/chat/completions"):
            return httpx.Response(
                200,
                json={
                    "id": "req-router-1",
                    "choices": [
                        {"message": {"role": "assistant", "content": "```json\n" + json.dumps(_router_payload()) + "\n```"}}
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

    def install(fail_status: int | None = None) -> None:
        original_init = LinerClient.__init__

        def patched_init(self, api_key=None, **kwargs):
            kwargs.setdefault("transport", _make_transport(fail_status=fail_status))
            original_init(self, api_key=api_key, **kwargs)

        monkeypatch.setattr(LinerClient, "__init__", patched_init)

    return install


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
    assert "DEMO MOCK MODE" in html or "LIVE LINER API" in html
    assert "모먼트 로스터리 성수점" in html
    assert "브라이트 피부과의원" in html
    assert "버거워크 산본점" in html


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


def test_audit_requires_input(client):
    response = client.post("/api/audit", json={"location": "", "industry": "", "channel_text": ""})
    assert response.status_code == 400
    assert "입력" in response.json()["detail"]

    response = client.post(
        "/api/audit",
        json={"location": "서울 성동구", "industry": "카페", "channel_text": ""},
    )
    assert response.status_code == 400


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
        "channel_text": sample_data.channel_text_of(sample_data.SAMPLES[0]),
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

    # 요청에 넣은 API 키는 응답 어디에도 나타나지 않아야 한다.
    assert "test-key-do-not-leak" not in response.text


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
