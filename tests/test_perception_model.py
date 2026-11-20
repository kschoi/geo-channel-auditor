"""고객 인식 집계의 출처·기간·표본 단위와 비교 제한을 검증한다."""

from __future__ import annotations

from copy import deepcopy

import pytest

from app.perception_model import MAX_OBSERVATIONS, build_perception, perception_prompt

PERIOD = {"days": 90, "start": "2026-08-23", "end": "2026-11-20", "label": "최근 90일"}
STORES = [{"id": "target", "name": "모먼트", "location": "성수동", "industry": "카페", "query": "성수동 모먼트 카페"}]
POSITIVE = "직원분이 주문을 차근차근 도와주셨다."
NEGATIVE = "주문한 뒤 안내 없이 한참 기다려서 아쉬웠다."


def source(number=1, *, store_id="target", text=POSITIVE, date="2026-11-01", channel="naver_blog", **changes):
    result = {
        "source_id": f"{store_id}-S{number}", "store_id": store_id,
        "url": f"https://blog.naver.com/{store_id}/{number}", "title": "성수동 모먼트 방문 기록",
        "description": text, "date": date, "channel": channel, "period_status": "in_period",
        "materials": [{"id": "summary", "kind": "search_summary", "text": text}],
    }
    result.update(changes)
    return result


def observation(number=1, *, store_id="target", quote=POSITIVE, aspect="service", sentiment="positive", **changes):
    result = {
        "source_id": f"{store_id}-S{number}", "store_id": store_id, "material_id": "summary",
        "quote": quote, "aspect": aspect, "sentiment": sentiment, "voice": "customer_experience",
    }
    result.update(changes)
    return result


def build(sources, observations, *, stores=None, period=None):
    return build_perception(stores or STORES, sources, {"observations": observations}, period or PERIOD)


def aspect_of(store, name="service"):
    return next(row for row in store["aspects"] if row["id"] == name)


def paired_samples(count=5):
    stores = deepcopy(STORES)
    stores.append({**STORES[0], "id": "competitor-1", "name": "이웃카페", "query": "성수동 이웃카페"})
    sources = [source(number, store_id=store["id"]) for store in stores for number in range(1, count + 1)]
    observations = [observation(number, store_id=store["id"]) for store in stores for number in range(1, count + 1)]
    return stores, sources, observations


def test_exact_quote_offsets_and_identity_are_preserved():
    text = "앞부분 😀\n" + POSITIVE + " 뒤 문장"
    result = build([source(text=text)], [observation()])
    evidence = result["stores"][0]["evidence"][0]
    assert text[evidence["start"]:evidence["end"]] == POSITIVE
    assert evidence["store_id"] == "target"
    assert evidence["source_id"] == "target-S1"
    assert evidence["url"] == "https://blog.naver.com/target/1"
    assert result["validation"] == {"accepted": 1, "rejected": 0}


@pytest.mark.parametrize("changes", [
    {"quote": "실제 자료에 없는 아주 친절한 직원의 서비스"},
    {"quote": "짧은 글"}, {"quote": "가" * 501}, {"quote": []},
    {"source_id": "없는출처"}, {"material_id": "없는자료"}, {"store_id": "competitor-1"},
    {"aspect": "전체만족도"}, {"aspect": []}, {"sentiment": "최고"}, {"sentiment": {}},
    {"voice": "verified_customer"}, {"voice": []},
])
def test_forged_malformed_or_cross_store_observations_are_rejected(changes):
    result = build([source()], [observation(**changes)])
    assert result["validation"] == {"accepted": 0, "rejected": 1}
    assert result["stores"][0]["summary"]["documents"] == 0
    assert result["decisions"] == []


@pytest.mark.parametrize("material_id,kind", [("title", "search_title"), ("anything", "search_title"), ("title", "other")])
def test_title_alone_cannot_be_customer_experience(material_id, kind):
    item = source(materials=[{"id": material_id, "kind": kind, "text": POSITIVE}])
    result = build([item], [observation(material_id=material_id)])
    assert result["validation"]["rejected"] == 1
    assert result["stores"][0]["summary"]["unclassified_documents"] == 1


def test_duplicate_source_or_material_ids_are_ambiguous_and_rejected():
    first = source()
    second = source(url="https://other.example/review")
    assert build([first, second], [observation()])["validation"]["rejected"] == 1
    first["materials"].append(dict(first["materials"][0]))
    assert build([first], [observation()])["validation"]["rejected"] == 1


@pytest.mark.parametrize("url", ["javascript:alert(1)", "https://user:secret@example.com/", "https://example.com/ has-space"])
def test_unsafe_source_urls_are_never_evidence(url):
    result = build([source(url=url)], [observation()])
    assert result["stores"][0]["evidence"] == []


def test_word_counts_do_not_override_router_interpretation():
    # 부정 단어가 있어도 문맥을 읽은 Router 판단을 서버 단어 사전으로 뒤집지 않는다.
    quote = "불친절할까 걱정했는데 전혀 그렇지 않아 편하게 머물렀다."
    result = build([source(text=quote)], [observation(quote=quote, sentiment="positive")])
    assert result["stores"][0]["summary"]["positive"] == 1


@pytest.mark.parametrize("voice", ["owner_claim", "editorial", "unclear"])
def test_non_customer_voices_do_not_create_customer_ratios_or_actions(voice):
    result = build([source(text=NEGATIVE)], [observation(quote=NEGATIVE, sentiment="negative", voice=voice)])
    store = result["stores"][0]
    assert len(store["evidence"]) == 1
    assert store["summary"]["non_customer_documents"] == 1
    assert store["summary"]["documents"] == 0
    assert store["summary"]["negative_pct"] is None
    assert result["decisions"] == []


def test_period_is_recomputed_and_unknown_dates_are_separate():
    sources = [
        source(1, date="2026-11-01", period_status="out_of_period"),
        source(2, date="", period_status="in_period"),
        source(3, date="2024-11-01", period_status="in_period"),
        source(4, date="2026-11-21", period_status="in_period"),
        source(5, date="2026-11", period_status="in_period"),
    ]
    result = build(sources, [observation(number) for number in range(1, 6)])
    summary = result["stores"][0]["summary"]
    assert summary["documents"] == 1
    assert summary["undated_documents"] == 2
    assert summary["positive_pct"] == 100
    assert result["validation"] == {"accepted": 3, "rejected": 2}
    assert [item["period_status"] for item in result["stores"][0]["evidence"]] == ["in_period", "unknown", "unknown"]


def test_period_boundaries_are_inclusive():
    sources = [source(1, date=PERIOD["start"]), source(2, date=PERIOD["end"])]
    assert build(sources, [observation(1), observation(2)])["stores"][0]["summary"]["documents"] == 2


def test_unknown_negative_evidence_does_not_create_action():
    result = build([source(text=NEGATIVE, date="게시일 없음")], [observation(quote=NEGATIVE, sentiment="negative")])
    assert result["stores"][0]["summary"]["undated_documents"] == 1
    assert result["stores"][0]["summary"]["negative"] == 0
    assert result["decisions"] == []


def test_many_quotes_and_tracking_urls_still_count_one_document_per_aspect():
    text = POSITIVE + " " + NEGATIVE
    sources = [source(text=text), source(2, text=text, url="https://blog.naver.com/target/1?utm_source=search#section")]
    observations = [
        observation(), observation(), observation(2),
        observation(quote=NEGATIVE, sentiment="negative"),
        observation(2, quote=NEGATIVE, sentiment="negative"),
        observation(aspect="quality"),
    ]
    result = build(sources, observations)
    store = result["stores"][0]
    assert store["summary"]["documents"] == 1
    assert store["summary"]["mixed"] == 1
    assert aspect_of(store)["documents"] == 1
    assert aspect_of(store)["mixed"] == 1
    assert aspect_of(store, "quality")["positive"] == 1
    assert sum(item["documents"] for item in store["channels"]) == 1


def test_opposite_aspects_collapse_to_mixed_at_whole_document_level():
    text = POSITIVE + " " + NEGATIVE
    result = build([source(text=text)], [observation(), observation(quote=NEGATIVE, sentiment="negative", aspect="wait")])
    store = result["stores"][0]
    assert store["summary"]["mixed"] == 1
    assert aspect_of(store)["positive"] == 1
    assert aspect_of(store, "wait")["negative"] == 1


def test_primary_duplicate_wins_over_unknown_date_without_input_order_dependency():
    sources = [source(1, date=""), source(2, url="https://blog.naver.com/target/1")]
    result = build(sources, [observation(1), observation(2)])
    summary = result["stores"][0]["summary"]
    assert summary["documents"] == 1
    assert summary["undated_documents"] == 0


def test_ratios_include_mixed_and_neutral_in_denominator():
    sentiments = ["positive", "negative", "mixed", "neutral"]
    result = build([source(number) for number in range(1, 5)],
                   [observation(number, sentiment=sentiment) for number, sentiment in enumerate(sentiments, 1)])
    summary = result["stores"][0]["summary"]
    assert summary["documents"] == 4
    assert summary["positive_pct"] == summary["negative_pct"] == 25
    assert sum(summary[key] for key in sentiments) == 4


def test_empty_evidence_has_unknown_ratios_and_demographics_not_zero_people():
    result = build([source()], [])
    store = result["stores"][0]
    assert store["summary"]["documents"] == 0
    assert store["summary"]["unclassified_documents"] == 1
    assert all(row["positive_pct"] is None and row["negative_pct"] is None for row in store["aspects"])
    assert store["demographics"]["status"] == "unknown"
    assert len(store["demographics"]["cells"]) == 12
    assert all(cell["count"] is cell["positive"] is cell["negative"] is None for cell in store["demographics"]["cells"])
    assert result["comparison"]["status"] == "not_requested"
    assert result["decisions"] == []


def test_demographic_claims_in_router_output_are_ignored():
    raw = {"observations": [observation()], "gender": "female", "age": 30,
           "demographics": {"female": 1000}, "overall_score": 100, "winner": "target"}
    result = build_perception(STORES, [source()], raw, PERIOD)
    assert result["stores"][0]["demographics"]["status"] == "unknown"
    assert "overall_score" not in result and "winner" not in result


def test_comparison_needs_five_dated_customer_documents_for_each_store():
    stores, sources, observations = paired_samples(4)
    result = build(sources, observations, stores=stores)
    assert result["comparison"]["status"] == "limited"
    assert "5건" in result["comparison"]["reason"]
    assert not any(row["comparable"] for row in result["comparison"]["rows"])


def test_comparison_is_descriptive_and_only_qualified_aspect_rows_are_comparable():
    stores, sources, observations = paired_samples()
    result = build(sources, observations, stores=stores)
    comparison = result["comparison"]
    assert comparison["status"] == "comparable"
    assert next(row for row in comparison["rows"] if row["aspect"] == "service")["comparable"]
    assert not next(row for row in comparison["rows"] if row["aspect"] == "quality")["comparable"]
    assert "통계적 유의성" in comparison["reason"]
    assert not any(key in comparison for key in ("winner", "rank", "score"))


def test_different_channel_coverage_limits_comparison():
    stores, sources, observations = paired_samples()
    for item in sources:
        if item["store_id"] == "competitor-1":
            item["channel"] = "social"
    comparison = build(sources, observations, stores=stores)["comparison"]
    assert comparison["status"] == "limited"
    assert "채널" in comparison["reason"]
    assert not any(row["comparable"] for row in comparison["rows"])


def test_same_channels_but_skewed_mix_limits_comparison():
    stores, sources, observations = paired_samples(10)
    for index, item in enumerate(sources):
        item["channel"] = "social" if index < 8 or 10 <= index < 12 else "naver_blog"
    comparison = build(sources, observations, stores=stores)["comparison"]
    assert comparison["status"] == "limited"


def test_aspect_channel_mix_is_checked_even_when_overall_mix_matches():
    stores, sources, observations = paired_samples(10)
    for index, item in enumerate(sources):
        item["channel"] = "social" if index % 10 < 5 else "naver_blog"
    for index, item in enumerate(observations):
        item["aspect"] = "service" if index < 5 or index >= 15 else "quality"
    comparison = build(sources, observations, stores=stores)["comparison"]
    assert comparison["status"] == "limited"
    assert not any(row["comparable"] for row in comparison["rows"])


@pytest.mark.parametrize("field,value", [("location", "연남동"), ("industry", "식당")])
def test_different_input_area_or_category_limits_comparison(field, value):
    stores, sources, observations = paired_samples()
    stores[1][field] = value
    comparison = build(sources, observations, stores=stores)["comparison"]
    assert comparison["status"] == "limited"
    assert "지역·업종" in comparison["reason"]


def test_actions_have_target_evidence_and_priority_uses_document_counts():
    sources = [source(number, text=NEGATIVE if number <= 3 else POSITIVE) for number in range(1, 6)]
    observations = [observation(number, quote=NEGATIVE if number <= 3 else POSITIVE,
                                sentiment="negative" if number <= 3 else "positive") for number in range(1, 6)]
    result = build(sources, observations)
    decision = result["decisions"][0]
    assert decision["priority"] == "P1"
    assert decision["store_id"] == "target"
    assert len(decision["evidence_ids"]) == 3
    assert "5건" in decision["reason"] and "부정 3건" in decision["reason"]
    assert build(sources[:3], observations[:3])["decisions"][0]["priority"] == "P2"
    # 같은 출처 인용을 반복해서 P1로 올릴 수 없다.
    assert build(sources[:1], observations[:1] * 20)["decisions"][0]["priority"] == "P2"


def test_competitor_complaints_do_not_create_target_actions():
    stores, sources, observations = paired_samples()
    for item in observations:
        if item["store_id"] == "competitor-1":
            item["sentiment"] = "negative"
    decisions = build(sources, observations, stores=stores)["decisions"]
    assert decisions and all(item["kind"] == "strength" for item in decisions)
    assert all(item["store_id"] == "target" for item in decisions)


def test_prompt_contains_only_owned_materials_not_out_of_period_data():
    sources = [source(), source(2, text="제외할 오래된 자료", period_status="out_of_period"),
               source(3, text="다른 매장 비허용 자료", store_id="unrequested")]
    text = perception_prompt(STORES, sources)
    assert POSITIVE in text
    assert "제외할 오래된 자료" not in text
    assert "다른 매장 비허용 자료" not in text


def test_input_is_not_mutated():
    sources, observations, stores, period = [source()], [observation()], deepcopy(STORES), dict(PERIOD)
    before = deepcopy((sources, observations, stores, period))
    build(sources, observations, stores=stores, period=period)
    assert (sources, observations, stores, period) == before


def test_malformed_observations_and_response_budget_are_bounded():
    result = build_perception(STORES, [source()], {"observations": "not a list"}, PERIOD)
    assert result["validation"] == {"accepted": 0, "rejected": 1}
    result = build([source()], [observation()] * (MAX_OBSERVATIONS + 20))
    assert result["validation"] == {"accepted": 1, "rejected": MAX_OBSERVATIONS + 19}


@pytest.mark.parametrize("period", [
    {"start": "2026-11", "end": "2026-11-20"},
    {"start": "2026-11-21", "end": "2026-11-20"},
    {"start": "2026-02-31", "end": "2026-11-20"},
])
def test_invalid_windows_fail_before_generating_results(period):
    with pytest.raises(ValueError):
        build([source()], [observation()], period=period)


def test_prompt_reserves_equal_input_budget_for_every_store():
    import json
    stores = [{"id": sid, "name": sid, "location": "산본", "industry": "분식"}
              for sid in ("target", "competitor-1", "competitor-2")]
    sources = [{"store_id": store["id"], "source_id": f"{store['id']}-{i}", "url": f"https://example.com/{store['id']}/{i}",
                "date": "2026-11-19", "period_status": "in_period",
                "materials": [{"id": f"m{n}", "kind": "agent_excerpt", "text": "a" * 1800} for n in range(3)]}
               for store in stores for i in range(24)]
    payload = json.loads(perception_prompt(stores, sources).split("\n", 1)[1])
    totals = {store["id"]: sum(len(m["text"]) for s in payload["sources"] if s["store_id"] == store["id"] for m in s["materials"]) for store in stores}
    assert len(set(totals.values())) == 1
    assert 0 < totals["competitor-2"] <= 40000


ALL_PERIOD = {"days": 0, "start": None, "end": "2026-11-20", "label": "찾은 전체 기간", "scope": "all_available"}


def test_all_available_includes_undated_and_old_customer_documents_without_relabeling():
    sources = [source(1, date=""), source(2, date="2020-02-01"), source(3, date="2027-01-01"),
               source(4, date="2026-11-19"), source(5, date="", text=NEGATIVE)]
    observations = [observation(1), observation(2), observation(3), observation(4, sentiment="neutral"),
                    observation(5, quote=NEGATIVE, sentiment="negative", voice="owner_claim")]
    result = build(sources, observations, period=ALL_PERIOD)
    summary = result["stores"][0]["summary"]
    assert summary["documents"] == 3
    assert summary["dated_documents"] == 2
    assert summary["undated_documents"] == 1
    assert summary["positive"] == 2 and summary["neutral"] == 1
    assert summary["positive_pct"] == 66.7
    assert summary["evidence_scope"] == "all_available"
    assert result["validation"] == {"accepted": 4, "rejected": 1}
    assert result["stores"][0]["evidence"][0]["period_status"] == "unknown"
    assert result["stores"][0]["evidence"][0]["date"] is None
    assert all(cell["count"] is None for cell in result["stores"][0]["demographics"]["cells"])
    bounded = build(sources, observations)
    assert bounded["stores"][0]["summary"]["documents"] == 1
    assert bounded["stores"][0]["summary"]["evidence_scope"] == "dated_period"


def test_all_available_deduplicates_known_and_unknown_versions_in_denominators():
    sources = [source(1, date=""), source(2, url="https://blog.naver.com/target/1?utm_source=test")]
    for values in (sources, list(reversed(sources))):
        result = build(values, [observation(1), observation(2)], period=ALL_PERIOD)
        summary = result["stores"][0]["summary"]
        assert summary["documents"] == summary["dated_documents"] == 1
        assert summary["undated_documents"] == 0
        assert aspect_of(result["stores"][0])["positive"] == 1
        assert sum(row["documents"] for row in result["stores"][0]["channels"]) == 1


def test_all_available_unknown_dates_limit_comparison_even_with_enough_documents():
    stores, sources, observations = paired_samples(5)
    sources[0]["date"] = ""
    result = build(sources, observations, stores=stores, period=ALL_PERIOD)
    assert result["stores"][0]["summary"]["documents"] == 5
    assert result["comparison"]["status"] == "limited"
    assert "게시일 미확인" in result["comparison"]["reason"]
    assert not any(row["comparable"] for row in result["comparison"]["rows"])
    assert next(row for row in result["comparison"]["rows"] if row["aspect"] == "service")["stores"][0]["positive"] == 5


def test_all_available_unknown_complaint_creates_explicitly_undated_action():
    result = build([source(text=NEGATIVE, date="")],
                   [observation(quote=NEGATIVE, sentiment="negative")], period=ALL_PERIOD)
    decision = result["decisions"][0]
    assert decision["kind"] == "improve"
    assert decision["priority"] == "P2"
    assert decision["documents"] == 1
    assert decision["includes_undated"] is True
    assert decision["evidence_scope"] == "all_available"
    assert decision["evidence_ids"] == [result["stores"][0]["evidence"][0]["id"]]
    assert "기간 내" not in decision["reason"] and "최근" not in decision["reason"]


def test_strength_uses_only_positive_documents_and_target_evidence():
    combined = POSITIVE + " " + NEGATIVE
    result = build([source(1), source(2, text=combined)],
                   [observation(1), observation(2), observation(2, quote=NEGATIVE, sentiment="negative")])
    improvement = next(item for item in result["decisions"] if item["kind"] == "improve")
    strength = next(item for item in result["decisions"] if item["kind"] == "strength")
    assert strength["documents"] == 1
    assert strength["evidence_ids"] == ["E1"]
    assert "긍정 문서가 1건" in strength["reason"]
    assert improvement["evidence_ids"] == ["E3"]
    assert "반복" not in strength["reason"]


def test_all_available_requires_both_explicit_scope_and_zero_days():
    with pytest.raises(ValueError):
        build([source()], [observation()], period={"days": 0, "start": None, "end": "2026-11-20"})
    with pytest.raises(ValueError):
        build([source()], [observation()], period={**ALL_PERIOD, "days": 365})
