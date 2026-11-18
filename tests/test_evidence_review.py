from copy import deepcopy

import pytest

from app.evidence_review import build_review, extraction_prompt, prepare_sources


def source(text, *, date="2026-11-01", url="https://example.com/store"):
    return {"title": "산본 디델리", "description": text, "url": url, "date": date}


def observation(value, *, field="hours", quote=None, source_id="S1", subject="영업시간", scope="", qualification="stated"):
    return {"field": field, "value": value, "quote": quote or value, "source_id": source_id,
            "material_id": "summary", "subject": subject, "scope": scope, "qualification": qualification}


def review(sources, observations):
    return build_review(observations, prepare_sources(sources), store_name="디델리", location="산본", collected_at="2026-11-18")


def field(result, key):
    return next(item for item in result["fields"] if item["key"] == key)


def test_quote_and_offsets_point_to_supplied_text():
    sources = prepare_sources([source("안내: 영업시간 매일 10:00-22:00")])
    result = build_review([observation("10:00-22:00", quote="영업시간 매일 10:00-22:00")], sources)
    item = field(result, "hours")["observations"][0]
    text = sources[0]["materials"][0]["text"]
    assert text[item["quote_start"]:item["quote_end"]] == item["quote"]
    assert item["evidence_label"] == "검색 결과 요약"
    assert item["scope"] == "매일"


@pytest.mark.parametrize("change", [
    {"quote": "영업시간 매일 09:00-22:00"}, {"value": "09:00-22:00"},
    {"source_id": "S999"}, {"material_id": "invented"}, {"field": "invented"},
    {"field": "price", "value": "10:00-22:00"}, {"value": {"secret": "hidden"}},
])
def test_forged_or_malformed_observations_are_rejected(change):
    item = observation("10:00-22:00", quote="영업시간 매일 10:00-22:00") | change
    result = review([source("영업시간 매일 10:00-22:00")], [item])
    assert result["stats"]["rejected_observations"] == 1
    assert result["stats"]["observed_fields"] == 0


def test_different_hours_take_priority_over_missing_info():
    result = review([source("매일 10:00-22:00"), source("11:00-20:30", date="", url="https://other.test")], [
        observation("10:00-22:00", quote="매일 10:00-22:00"),
        observation("11:00-20:30", source_id="S2"),
    ])
    assert field(result, "hours")["status"] == "different"
    assert result["actions"][0]["field"] == "hours"
    assert result["actions"][0]["source_ids"] == ["S1", "S2"]


@pytest.mark.parametrize("quotes", [
    ("월요일 10:00-22:00", "화요일 11:00-20:30"),
    ("평일 10:00-22:00", "주말 11:00-20:30"),
])
def test_different_days_are_not_compared(quotes):
    result = review([source(quotes[0]), source(quotes[1], url="https://other.test")], [
        observation("10:00-22:00", quote=quotes[0]), observation("11:00-20:30", quote=quotes[1], source_id="S2"),
    ])
    assert field(result, "hours")["status"] == "found"


def test_different_menu_prices_are_not_a_conflict():
    result = review([source("라볶이 8,000원, 김밥 4,000원")], [
        observation("8,000원", field="price", subject="라볶이", quote="라볶이 8,000원"),
        observation("4,000원", field="price", subject="김밥", quote="김밥 4,000원"),
    ])
    assert field(result, "price")["status"] == "found"


def test_same_menu_different_prices_flag_different_not_current_price():
    result = review([source("라볶이 8,000원"), source("라볶이 9,000원", url="https://other.test")], [
        observation("8,000원", field="price", subject="라볶이", quote="라볶이 8,000원"),
        observation("9,000원", field="price", subject="라볶이", quote="라볶이 9,000원", source_id="S2"),
    ])
    assert field(result, "price")["status"] == "different"


@pytest.mark.parametrize("date_value, expected", [("2020-06-26", "historical"), ("", "undated"), ("2026", "undated"), ("2026-12-31", "uncertain")])
def test_publication_uncertainty(date_value, expected):
    result = review([source("10:00-22:00", date=date_value)], [observation("10:00-22:00")])
    assert field(result, "hours")["status"] == "needs_check"
    assert field(result, "hours")["observations"][0]["qualification"] == expected


def test_speculation_cannot_become_parking_fact():
    result = review([source("지하주차장에 하면 될 듯")], [observation("지하주차장", field="parking", quote="지하주차장에 하면 될 듯")])
    assert field(result, "parking")["status"] == "needs_check"
    assert field(result, "parking")["observations"][0]["qualification"] == "uncertain"


def test_total_bill_is_not_menu_price():
    result = review([source("먹고 낸 금액은 9,500원")], [observation("9,500원", field="price", subject="결제액", quote="먹고 낸 금액은 9,500원")])
    assert field(result, "price")["status"] == "needs_check"
    assert field(result, "price")["observations"][0]["qualification"] == "historical"


def test_missing_reservation_is_not_unavailable():
    result = review([source("라볶이")], [])
    reservation = field(result, "reservation")
    assert reservation["status"] == "not_found"
    assert "불가" not in reservation["summary"]
    assert len(result["fields"]) == 6


def test_agent_excerpts_keep_origin_only_for_known_urls():
    payload = {"answer": "공식 영업시간 09:00", "chunks": [{"data": {"referenceChunks": [
        {"sourceUrl": "https://example.com/store", "content": "영업시간 10:00-22:00"},
        {"sourceUrl": "https://wrong.test", "content": "다른 지점 정보"},
    ]}}]}
    prepared = prepare_sources([source("자료")], payload)
    assert prepared[0]["materials"][-1]["kind"] == "agent_excerpt"
    assert prepared[0]["publisher_status"] == "unverified"
    prompt = extraction_prompt("디델리", "산본", prepared)
    assert "다른 지점 정보" not in prompt
    assert "09:00" not in prompt
    assert "2026-11-01" in prompt


def test_cards_do_not_reintroduce_raw_hours_price_or_speculation():
    data = source("라볶이. 영업시간 10:00-22:00. 예전 가격 9,500원. 주차 될 듯")
    data["title"] += " 9,500원 맛집"
    result = review([data], [observation("라볶이", field="menu")])
    cards = str(result["content_sources"])
    assert "10:00" not in cards
    assert "9,500" not in cards
    assert "될 듯" not in cards
    assert "라볶이" in cards
    assert "현재 판매 여부는 확인 필요" in cards


def test_source_input_not_mutated():
    sources = [source("영업시간 10:00-22:00")]
    original = deepcopy(sources)
    review(sources, [])
    assert sources == original


def test_hours_and_break_in_one_quote_are_not_conflated():
    quote = "영업시간 10:00-22:00, 브레이크타임 15:00-17:00"
    result = review([source(quote)], [
        observation("10:00-22:00", quote=quote),
        observation("15:00-17:00", subject="휴게시간", quote=quote),
    ])
    hours = field(result, "hours")
    assert hours["status"] == "found"
    assert [o["subject"] for o in hours["observations"]] == ["영업시간", "휴게시간"]


def test_menu_options_are_not_conflated():
    quote = "라볶이 소 8,000원, 라볶이 대 10,000원"
    result = review([source(quote)], [
        observation("8,000원", field="price", subject="라볶이", scope="소", quote=quote),
        observation("10,000원", field="price", subject="라볶이", scope="대", quote=quote),
    ])
    assert field(result, "price")["status"] == "found"


def test_two_weekdays_in_one_quote():
    quote = "월요일 10:00-22:00 화요일 11:00-20:30"
    result = review([source(quote)], [
        observation("10:00-22:00", quote=quote), observation("11:00-20:30", quote=quote),
    ])
    assert field(result, "hours")["status"] == "found"


@pytest.mark.parametrize("left,right,status", [
    ("오전 8시~오후 5시", "오후 8시~오전 5시", "different"),
    ("오전 10시~오후 8시", "10:00-20:00", "found"),
])
def test_am_pm_compares_actual_hours(left, right, status):
    result = review([source(left), source(right, url="https://other.test")], [
        observation(left), observation(right, source_id="S2"),
    ])
    assert field(result, "hours")["status"] == status


def test_auxiliary_subject_cannot_invent_a_fact():
    result = review([source("유료 주차 가능")], [
        observation("주차", field="parking", subject="무료 주차", quote="유료 주차 가능"),
    ])
    assert field(result, "parking")["observations"][0]["subject"] == ""


@pytest.mark.parametrize("scope1,scope2,expected", [
    ("월~토", "월요일", "different"), ("월–일 동일", "일요일", "different"),
    ("월-금", "주말", "found"), ("월/수/금", "화요일", "found"),
])
def test_overlapping_days_are_compared(scope1, scope2, expected):
    q1, q2 = f"{scope1} 10:00-20:00", f"{scope2} 09:00-19:00"
    result = review([source(q1), source(q2, url="https://other.test")], [
        observation("10:00-20:00", scope=scope1, quote=q1),
        observation("09:00-19:00", scope=scope2, quote=q2, source_id="S2"),
    ])
    assert field(result, "hours")["status"] == expected
