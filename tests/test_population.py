"""Population snapshot integrity and public API boundaries."""
from fastapi import FastAPI
from fastapi.testclient import TestClient

from app.population import _snapshot, find_regions, get_population, router

app = FastAPI()
app.include_router(router)
client = TestClient(app)


def test_official_snapshot_has_complete_sex_age_cross_counts_and_unique_regions():
    snapshot = _snapshot()
    assert len(snapshot["records"]) == 3921
    assert len({r["code"] for r in snapshot["records"]}) == 3921
    for region in snapshot["records"]:
        assert len(region["cells"]) == 12
        assert len({(c["gender"], c["age_group"]) for c in region["cells"]}) == 12
        assert all(isinstance(c["count"], int) and c["count"] >= 0 for c in region["cells"])
        assert sum(c["count"] for c in region["cells"]) == region["total"]
        assert region["under10"]["male"] + region["under10"]["female"] == region["under10"]["total"]
        assert region["total"] + region["under10"]["total"] == region["all_ages_total"]
    assert snapshot["dataset"]["raw_sha256"] == "d0e63aa22b4456b4c8796e1536ba9637ff4ae5f88669a45ac0de07b630d224a3"


def test_sanbon_real_source_counts_and_age_denominator():
    result = get_population("4141054000")
    assert result["source_type"] == "official_resident_population"
    assert result["region"]["name"] == "경기도 군포시 산본1동"
    assert result["all_ages_total"] == 14090
    assert result["under10"] == {"male": 202, "female": 154, "total": 356}
    assert result["total"] == 14090
    assert result["display_total"] == 13734
    cells = {(c["gender"], c["age_group"]): c for c in result["cells"]}
    assert cells["female", "20s"]["count"] == 684
    assert cells["male", "60plus"]["count"] == 1427 + 803 + 264 + 35
    assert cells["female", "20s"]["share_pct"] == round(684 / 13734 * 100, 2)
    assert result["period"] == "2026-10"
    assert "매장 방문객" in result["dataset"]["limitations"][0]


def test_region_picker_keeps_ambiguous_names_separate_and_dong_first():
    matches = find_regions("산본")
    assert {r["code"] for r in matches} == {"4141054000", "4141055000"}
    assert all(r["level"] == "dong" for r in matches)
    broader = find_regions("군포")
    assert broader[-1]["level"] == "district"
    assert len(find_regions("동")) == 30
    assert find_regions("경기도 산본1동")[0]["code"] == "4141054000"
    assert find_regions("4141054000")[0]["name"] == "경기도 군포시 산본1동"
    assert find_regions("  ") == []


def test_unknown_and_reversed_codes_do_not_fallback_to_any_region():
    for value in ["0004501414", "9999999999", "산본1동", "414105400", "../4141054000", "４１４１０５４０００"]:
        assert get_population(value) is None
    assert client.get("/api/population/9999999999").status_code == 404
    assert client.get("/api/population/0004501414").status_code == 404
    assert client.get("/api/population/regions", params={"q": "' OR 1=1"}).json()["regions"] == []


def test_routes_bound_input_and_preserve_regions_route_precedence():
    assert client.get("/api/population/regions", params={"q": "산본"}).status_code == 200
    assert client.get("/api/population/regions").status_code == 422
    assert client.get("/api/population/regions", params={"q": ""}).status_code == 422
    assert client.get("/api/population/regions", params={"q": "가" * 81}).status_code == 422
    response = client.get("/api/population/4141054000")
    assert response.status_code == 200
    assert len(response.json()["cells"]) == 12


def test_result_mutation_does_not_change_other_requests():
    first = get_population("4141054000")
    first["cells"][0]["count"] = 99999999
    first["dataset"]["limitations"].clear()
    second = get_population("4141054000")
    assert second["cells"][0]["count"] != 99999999
    assert second["dataset"]["limitations"]
