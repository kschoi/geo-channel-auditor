"""Synthetic fixtures exercise evidence validation; they are not seeded market data."""
import json
from copy import deepcopy

import pytest

from app.market_data import MARKET_SYSTEM_PROMPT, build_market, market_prompt


def source(text, *, sid="market-S1", url="https://data.seoul.go.kr/example", kind="summary"):
    return {"source_id": sid, "url": url, "title": "공식 통계", "date": "2026-11-01",
            "materials": [{"id": "summary", "kind": kind, "text": text}]}


def fact(quote="2026년 3분기 산본1동 상주인구: 여성 20대 1,234명.", **changes):
    return {"source_id": "market-S1", "material_id": "summary", "quote": quote,
            "label": "상주인구", "value": 1234, "region": "산본1동", "period": "2026년 3분기",
            "unit": "명", "gender": "female", "age_group": "20s", **changes}


def run(item=None, *, sources=None, location="산본"):
    item = fact() if item is None else item
    return build_market(location, sources or [source(item["quote"])], {"facts": [item]})


def cell(result, gender="female", age="20s"):
    return next(c for c in result["demographics"]["cells"] if c["gender"] == gender and c["age_group"] == age)


def test_exact_joint_quote_has_count_and_source_but_no_customer_inference():
    result = run()
    assert result["status"] == "available"
    assert result["demographics"]["status"] == "partial"
    assert cell(result)["count"] == 1234
    assert cell(result)["quote"] == fact()["quote"]
    assert cell(result)["source_id"] == "market-S1"
    assert cell(result, "male")["count"] is None
    assert "매장 고객 수 아님" in result["demographics"]["metric_label"]
    assert len(result["demographics"]["cells"]) == 12


@pytest.mark.parametrize("changes", [
    {"source_id": "missing"}, {"material_id": "missing"}, {"quote": "2026년 3분기 산본1동 상주인구 여성 20대 999명"},
    {"value": 999}, {"value": True}, {"value": "1234"}, {"value": 1234.0}, {"value": -1},
    {"value": 9007199254740992}, {"region": "서울"}, {"period": "2026년 4분기"}, {"unit": "%"},
    {"gender": "unknown"}, {"gender": "male"}, {"age_group": "30s"}, {"age_group": "20-24"},
    {"label": "직장인구"}, {"label": "추측한 인구수"}, {"quote": {"malformed": True}},
])
def test_forged_or_malformed_fact_is_not_a_statistic(changes):
    original = fact()
    result = run(original | changes, sources=[source(original["quote"])])
    assert result["status"] == "unavailable"
    assert result["facts"] == []
    assert all(c["count"] is None for c in result["demographics"]["cells"])


@pytest.mark.parametrize("url", [
    "https://data.seoul.go.kr.evil.test/a", "https://data.seoul.go.kr@evil.test/a",
    "https://evil.test@data.seoul.go.kr/a", "javascript:alert(1)", "file:///etc/passwd",
    "https://example.com/a", "https://data.seoul.go.kr:9999/a", "https://data.seoul.go.kr\\@evil.test/a",
])
def test_unofficial_or_unsafe_url_is_rejected(url):
    result = run(sources=[source(fact()["quote"], url=url)])
    assert result["facts"] == []
    assert result["sources"] == []
    assert url not in market_prompt("산본", [source(fact()["quote"], url=url)])


def test_separate_marginals_never_create_a_joint_population():
    quote = "2026년 3분기 산본1동 상주인구: 여성 1,234명, 20대 1,234명."
    inferred = run(fact(quote))
    assert cell(inferred)["count"] is None
    assert run(fact(quote, gender=None, age_group=None))["facts"] == []
    marginal = run(fact("2026년 3분기 산본1동 상주인구: 여성 1,234명.", gender=None, age_group=None))
    assert marginal["facts"][0]["value"] == 1234
    assert all(c["count"] is None for c in marginal["demographics"]["cells"])


def test_percentages_cannot_be_converted_to_people():
    quote = "2026년 3분기 산본1동 상주인구: 여성 50%, 20대 30%, 전체 100명."
    result = run(fact(quote, value=15))
    assert result["facts"] == []
    assert cell(result)["count"] is None


@pytest.mark.parametrize("label", ["여성 20대 1.234천명", "여성 20대 *명", "여성 20대 비공개", "여성 20대 123.4명"])
def test_scaled_fractional_suppressed_values_are_not_counts(label):
    result = run(fact(f"2026년 3분기 산본1동 상주인구: {label}.", value=1234))
    assert result["facts"] == []


def test_zero_is_preserved_and_missing_is_not_zero():
    result = run(fact("2026년 3분기 산본1동 상주인구: 여성 20대 0명.", value=0))
    assert cell(result)["count"] == 0
    assert cell(result, age="30s")["count"] is None


@pytest.mark.parametrize("demographic,age,accepted", [
    ("여성 10대", "10s", True), ("여성연령대_10_", "10s", False),
    ("여성연령대_20_", "20s", True), ("여성 20~24세", "20s", False),
    ("여성 20~29세", "20s", True), ("20대 여성", "20s", True),
    ("여성 60대", "60plus", False), ("여성 60세 이상", "60plus", True),
    ("여성 20대 이상", "20s", False),
])
def test_age_boundaries_are_not_invented(demographic, age, accepted):
    result = run(fact(f"2026년 3분기 산본1동 상주인구: {demographic} 1,234명.", age_group=age))
    assert (cell(result, age=age)["count"] == 1234) is accepted


def test_first_count_must_belong_to_the_joint_label():
    quote = "2026년 3분기 산본1동 상주인구: 여성 20대 100명, 여성 30대 1,234명."
    result = run(fact(quote))
    assert result["facts"] == []


def test_single_value_table_is_allowed_but_ambiguous_columns_are_not():
    single = "2026년 3분기 산본1동 상주인구 (단위: 명) | 여성 20대 | 1,234 |"
    assert cell(run(fact(single)))["count"] == 1234
    ambiguous = "2026년 3분기 산본1동 상주인구 (단위: 명) | 여성 20대 | 여성 30대 | 1,234 | 999 |"
    assert cell(run(fact(ambiguous)))["count"] is None


def test_no_cross_period_or_metric_join():
    quotes = ["2026년 3분기 산본1동 상주인구: 여성 20대 1,234명.",
              "2026년 2분기 산본1동 상주인구: 남성 30대 100명.",
              "2026년 3분기 산본1동 직장인구: 남성 40대 200명."]
    facts = [fact(quotes[0]), fact(quotes[1], source_id="market-S2", value=100, gender="male",
             age_group="30s", period="2026년 2분기"),
             fact(quotes[2], source_id="market-S3", label="직장인구", value=200, gender="male", age_group="40s")]
    result = build_market("산본", [source(q, sid=f"market-S{i}") for i, q in enumerate(quotes, 1)], {"facts": facts})
    assert len(result["facts"]) == 1
    assert sum(c["count"] is not None for c in result["demographics"]["cells"]) == 1
    assert result["status"] == "partial"


def test_conflicting_same_cell_stays_unknown():
    one = fact()
    two = fact(one["quote"].replace("1,234", "999"), source_id="market-S2", value=999)
    result = build_market("산본", [source(one["quote"]), source(two["quote"], sid="market-S2")], {"facts": [one, two]})
    assert cell(result)["count"] is None
    assert result["facts"] == []
    assert any("서로 다른 값" in warning for warning in result["limitations"])


def test_duplicate_sources_and_materials_are_not_ambiguous_evidence():
    data = source(fact()["quote"])
    assert run(sources=[data, deepcopy(data)])["facts"] == []
    data["materials"] *= 2
    assert run(sources=[data])["facts"] == []


def test_unrelated_region_and_empty_location_do_not_gain_local_counts():
    assert run(location="강남")["facts"] == []
    assert run(location="")["facts"] == []


def test_title_or_publication_date_is_not_statistical_evidence():
    assert run(sources=[source(fact()["quote"], kind="title")])["facts"] == []
    quote = "산본1동 상주인구: 여성 20대 1,234명."
    assert run(fact(quote), sources=[source(quote)])["facts"] == []


def test_input_not_mutated_and_empty_result_has_official_links():
    sources = [source(fact()["quote"])]
    raw = {"facts": [fact()]}
    before = deepcopy((sources, raw))
    build_market("산본", sources, raw)
    assert (sources, raw) == before
    empty = build_market("산본", [], None)
    assert empty["status"] == "unavailable"
    assert empty["demographics"]["status"] == "unknown"
    assert all(r["url"].startswith("https://") for r in empty["references"])
    assert "API" in " ".join(empty["limitations"])
    assert "비율×연령 비율" in MARKET_SYSTEM_PROMPT
    assert json.loads(market_prompt("산본", sources))["sources"][0]["source_id"] == "market-S1"


@pytest.mark.parametrize("count", ["-1,234명", "−1,234명"])
def test_negative_text_cannot_become_a_positive_population(count):
    quote = f"2026년 3분기 산본1동 상주인구: 여성 20대 {count}."
    assert run(fact(quote))["facts"] == []


def test_different_population_metrics_in_same_quote_are_not_relabelled():
    quote = "2026년 3분기 산본1동 상주인구: 여성 20대 100명. 직장인구: 여성 20대 1,234명."
    assert run(fact(quote))["facts"] == []


def test_numeric_age_suffix_is_not_an_age_group():
    quote = "2026년 3분기 산본1동 상주인구: 120대 여성 1,234명."
    assert run(fact(quote))["facts"] == []


def test_population_unit_cannot_be_money():
    quote = "2026년 3분기 산본1동 상주인구: 1,234원."
    assert run(fact(quote, gender=None, age_group=None, unit="원"))["facts"] == []


@pytest.mark.parametrize("demographic,gender", [
    ("10~20대 여성", "female"), ("10대 및 20대 여성", "female"), ("여성과 남성 20대", "male"),
])
def test_combined_groups_do_not_become_single_demographic_cells(demographic, gender):
    quote = f"2026년 3분기 산본1동 상주인구: {demographic} 1,234명."
    assert run(fact(quote, gender=gender))["facts"] == []


def test_substring_inside_an_unrelated_locality_is_not_a_region_match():
    quote = "2026년 3분기 광명동 상주인구: 여성 20대 1,234명."
    assert run(fact(quote, region="광명동"), location="명동")["facts"] == []


def test_demographic_label_can_use_the_unambiguous_metric_in_its_quote():
    assert cell(run(fact(label="여성 20대")))["count"] == 1234


@pytest.mark.parametrize("separator", [". ", ", ", "; ", "\n", " 그리고 "])
def test_region_and_period_cannot_be_borrowed_from_other_assertions(separator):
    quote = ("2026년 2분기 산본1동 상주인구: 여성 20대 80명" + separator
             + "2026년 3분기 광명동 상주인구: 여성 20대 1,234명.")
    result = run(fact(quote))
    assert result["facts"] == []
    assert cell(result)["count"] is None


@pytest.mark.parametrize("quote", [
    "2026년 3분기 산본1동 상주인구: 여성 20대 80명, 광명동 상주인구: 여성 20대 1,234명.",
    "2026년 2분기 산본1동 상주인구: 여성 20대 80명, 2026년 3분기 여성 20대 1,234명.",
    "2026년 3분기 산본1동, 광명동 상주인구: 여성 20대 1,234명.",
    "2026년 2분기, 2026년 3분기 산본1동 상주인구: 여성 20대 1,234명.",
    "2026년 3분기 산본1동. 광명동 상주인구: 여성 20대 1,234명.",
])
def test_even_one_clause_or_shared_count_does_not_resolve_multiple_scopes(quote):
    assert run(fact(quote))["facts"] == []


@pytest.mark.parametrize("modifier", ["이상", "이하", "미만", "초과", "이전", "이후", "미만의", "이상의"])
@pytest.mark.parametrize("age_label", ["20대 ", "연령대_20_", "20~29세 "])
def test_range_modifiers_never_mean_the_exact_twenties_group(modifier, age_label):
    quote = f"2026년 3분기 산본1동 상주인구: 여성{age_label}{modifier} 1,234명."
    assert run(fact(quote))["facts"] == []


@pytest.mark.parametrize("quote", [
    "2026년 3분기 산본1동 상주인구: 여성 20대에 해당하며 이하는 1,234명.",
    "2026년 3분기 산본1동 상주인구: 여성 20대 (이하) 1,234명.",
])
def test_modifier_in_the_bridge_cannot_attach_the_wrong_age_scope(quote):
    assert run(fact(quote))["facts"] == []


@pytest.mark.parametrize("period", ["2026. 10. 31.", "2026-10-31", "2026년 10월 31일", "20263", "2026Q3"])
def test_one_explicit_date_and_count_remain_supported(period):
    quote = f"{period} 산본1동 상주인구: 여성 20대 1,234명."
    assert cell(run(fact(quote, period=period)))["count"] == 1234


def test_other_locality_cannot_hide_inside_the_model_label():
    quote = "2026년 3분기 산본1동, 광명동 상주인구: 여성 20대 1,234명."
    assert run(fact(quote, label="광명동 상주인구"))["facts"] == []


def test_sentence_fragment_cannot_hide_inside_the_model_period():
    quote = "2026년 2분기 산본1동 상주인구: 여성 20대 80명. 2026년 3분기 광명동 상주인구: 여성 20대 1,234명."
    period = "2026년 2분기 산본1동 상주인구: 여성 20대 80명. 2026년 3분기"
    assert run(fact(quote, period=period))["facts"] == []


@pytest.mark.parametrize("region", ["산본1동, 광명동", "산본1동 및 광명동", "산본1동과 광명동"])
def test_composite_region_is_not_treated_as_a_single_local_scope(region):
    quote = f"2026년 3분기 {region} 상주인구: 여성 20대 1,234명."
    assert run(fact(quote, region=region))["facts"] == []
