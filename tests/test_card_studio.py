"""카드뉴스는 실제 연결·허용된 검색 출처·완전한 3장 원고만 반환한다."""

from __future__ import annotations

import asyncio
import copy
import json

import httpx
import pytest
from pydantic import ValidationError

from app.card_studio import CardRequest, generate_cards
from app.liner_client import LinerAPIError, LinerClient

SECRET = "synthetic-card-key-12345"
SOURCE_URL = "https://example.com/sanbon/dideli"


def request_payload(**changes):
    payload = {
        "store_name": "디델리",
        "location": "산본",
        "evidence_sources": [{"title": "산본 디델리 라볶이", "description": "라볶이를 소개합니다.", "url": SOURCE_URL}],
        "context": "방문 안내를 짧게 쓰고 싶어요.",
        "api_key": SECRET,
    }
    payload.update(changes)
    return CardRequest.model_validate(payload)


def valid_draft():
    return {
        "cards": [
            {"headline": "산본 디델리", "body": "라볶이 한 끼, 디델리에서 만나보세요.", "source_urls": [SOURCE_URL]},
            {"headline": "오늘은 라볶이", "body": "메뉴와 가격은 방문 전에 확인해 주세요.", "source_urls": [SOURCE_URL]},
            {"headline": "산본에서 만나요", "body": "영업시간은 [확인 필요]예요.", "source_urls": [SOURCE_URL]},
        ],
        "caption": "산본 디델리에서 만나요. 방문 전 운영 정보를 확인해 주세요. #산본 #디델리",
        "warnings": [],
    }


@pytest.fixture()
def router(monkeypatch):
    def install(draft=None, *, status=200, timeout=False):
        observed = {"requests": [], "clients": []}

        def handler(request):
            observed["requests"].append(json.loads(request.content))
            if timeout:
                raise httpx.ReadTimeout(f"private {SECRET}", request=request)
            if status != 200:
                return httpx.Response(status, json={"error": SECRET})
            return httpx.Response(200, json={
                "id": SECRET,
                "choices": [{"message": {"content": json.dumps(draft if draft is not None else valid_draft())}}],
            })

        original_init = LinerClient.__init__

        def patched_init(self, api_key=None, **kwargs):
            original_init(self, api_key=api_key, transport=httpx.MockTransport(handler), **kwargs)
            observed["clients"].append(self)

        monkeypatch.setattr(LinerClient, "__init__", patched_init)
        return observed

    return install


def test_cards_are_three_editable_copy_blocks_with_real_source_links(router):
    observed = router()
    result = asyncio.run(generate_cards(request_payload()))
    assert len(result["cards"]) == 3
    assert all(0 < len(card["headline"]) <= 40 and 0 < len(card["body"]) <= 180 for card in result["cards"])
    assert all(card["source_urls"] == [SOURCE_URL] for card in result["cards"])
    assert 0 < len(result["caption"]) <= 1500
    assert result["pipeline"]["totals"]["live"] == 1
    assert result["pipeline"]["totals"]["demo"] == 0
    assert result["pipeline"]["calls"][0]["status"] == "ok"
    assert len(observed["requests"]) == 1
    assert "temperature" not in observed["requests"][0]
    assert "response_format" not in observed["requests"][0]
    user_message = observed["requests"][0]["messages"][1]["content"]
    assert user_message.startswith("매장: 디델리\n지역: 산본\n검색자료:\n")
    assert SOURCE_URL in user_message
    assert "라볶이를 소개합니다." in user_message
    assert request_payload().context not in user_message
    assert "한국어 JSON으로 작성" in user_message
    assert SECRET not in json.dumps(result)
    assert SECRET not in json.dumps(observed["requests"])
    system = observed["requests"][0]["messages"][0]["content"]
    assert "[확인 필요]" in system and "다른 매장·지점" in system
    assert "context" in system and "사실 근거가 아니다" in system


def test_no_key_is_error_even_when_offline_test_flag_is_set(router):
    observed = router()
    with pytest.raises(LinerAPIError, match="API 키가 필요"):
        asyncio.run(generate_cards(request_payload(api_key=None)))
    assert not observed["requests"]


@pytest.mark.parametrize("status", [401, 429, 500])
def test_api_failure_never_creates_placeholder_cards(router, status):
    observed = router(status=status)
    with pytest.raises(LinerAPIError) as exc:
        asyncio.run(generate_cards(request_payload()))
    assert SECRET not in str(exc.value)
    assert observed["clients"][-1].calls[0]["status"] == "error"


def test_timeout_returns_safe_explicit_error(router):
    router(timeout=True)
    with pytest.raises(LinerAPIError, match="시간이 초과") as exc:
        asyncio.run(generate_cards(request_payload()))
    assert SECRET not in str(exc.value)


@pytest.mark.parametrize("defect", ["two_cards", "four_cards", "empty_headline", "object_body", "long_headline",
                                    "long_body", "long_caption", "invented_url", "empty_sources", "extra_field"])
def test_invalid_card_output_is_not_rendered_as_success(router, defect):
    draft = copy.deepcopy(valid_draft())
    if defect == "two_cards":
        draft["cards"].pop()
    elif defect == "four_cards":
        draft["cards"].append(copy.deepcopy(draft["cards"][0]))
    elif defect == "empty_headline":
        draft["cards"][0]["headline"] = "   "
    elif defect == "object_body":
        draft["cards"][0]["body"] = {"private": SECRET}
    elif defect == "long_headline":
        draft["cards"][0]["headline"] = "가" * 41
    elif defect == "long_body":
        draft["cards"][0]["body"] = "가" * 181
    elif defect == "long_caption":
        draft["caption"] = "가" * 1501
    elif defect == "invented_url":
        draft["cards"][0]["source_urls"] = ["https://not-provided.example/"]
    elif defect == "empty_sources":
        draft["cards"][0]["source_urls"] = []
    else:
        draft["unexpected"] = SECRET
    observed = router(draft)
    with pytest.raises(LinerAPIError) as exc:
        asyncio.run(generate_cards(request_payload()))
    assert SECRET not in str(exc.value)
    call = observed["clients"][-1].calls[-1]
    assert call["status"] == "error"
    assert call["detail"] == "invalid_response"


def test_evidence_must_match_store_in_title_or_url(router):
    observed = router()
    with pytest.raises(LinerAPIError, match="검색 자료가 없어요"):
        asyncio.run(generate_cards(request_payload(evidence_sources=[{
            "title": "봉추찜닭 산본점", "description": "주변 매장 추천: 산본 디델리",
            "url": "https://example.com/sanbon/bongchu",
        }])))
    assert not observed["requests"]


def test_other_store_evidence_is_not_sent_to_router(router):
    observed = router()
    payload = request_payload()
    evidence = [source.model_dump() for source in payload.evidence_sources]
    evidence.append({"title": "봉추찜닭 산본점", "description": "디델리 추천", "url": "https://example.com/bongchu"})
    result = asyncio.run(generate_cards(request_payload(evidence_sources=evidence)))
    user_message = observed["requests"][0]["messages"][1]["content"]
    assert "봉추찜닭" not in user_message
    assert any("제외" in warning for warning in result["warnings"])


@pytest.mark.parametrize("changes", [
    {"store_name": "   "}, {"location": ""}, {"context": "x" * 12001}, {"evidence_sources": []},
    {"evidence_sources": [{"title": "산본 디델리", "url": "javascript:alert(1)"}]},
    {"evidence_sources": [{"title": "산본 디델리", "url": "https://user:pass@example.com/"}]},
])
def test_card_request_rejects_invalid_inputs(changes):
    with pytest.raises(ValidationError):
        request_payload(**changes)


def test_provider_echoed_key_is_redacted_from_copy_and_logs(router):
    draft = valid_draft()
    draft["caption"] = f"문구 {SECRET}"
    router(draft)
    result = asyncio.run(generate_cards(request_payload()))
    assert SECRET not in json.dumps(result)
    assert result["caption"] != draft["caption"]


def test_more_than_thirty_evidence_sources_are_rejected():
    evidence = [{"title": "산본 디델리", "url": f"https://example.com/{index}"} for index in range(31)]
    with pytest.raises(ValidationError):
        request_payload(evidence_sources=evidence)
