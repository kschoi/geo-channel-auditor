"""Official, offline resident population snapshot. Never inferred customer attributes."""

from __future__ import annotations

import json
import re
from copy import deepcopy
from functools import lru_cache
from pathlib import Path
from typing import Any

from fastapi import APIRouter, HTTPException, Query

router = APIRouter(prefix="/api/population", tags=["population"])
_DATA_PATH = Path(__file__).with_name("data") / "population-202610.json"
_LEVEL_ORDER = {"dong": 0, "district": 1, "province": 2, "national": 3}


@lru_cache(maxsize=1)
def _snapshot() -> dict[str, Any]:
    with _DATA_PATH.open(encoding="utf-8") as file:
        return json.load(file)


@lru_cache(maxsize=1)
def _regions_by_code() -> dict[str, dict[str, Any]]:
    return {region["code"]: region for region in _snapshot()["records"]}


def _identity(region: dict[str, Any]) -> dict[str, str]:
    return {key: region[key] for key in ("code", "name", "level", "level_label")}


def find_regions(query: str) -> list[dict[str, str]]:
    """Return matching official region names; never guess a store's catchment area."""
    terms = query.split()
    if not terms or len(query) > 80:
        return []
    matches = [
        region for region in _snapshot()["records"]
        if all(term in region["name"] or term in region["code"] for term in terms)
    ]
    matches.sort(key=lambda region: (_LEVEL_ORDER[region["level"]], region["name"], region["code"]))
    return [_identity(region) for region in matches[:30]]


def get_population(region_code: str) -> dict[str, Any] | None:
    """Look up an exact code, with a denominator restricted to the displayed ages."""
    if not re.fullmatch(r"[0-9]{10}", region_code):
        return None
    region = _regions_by_code().get(region_code)
    if region is None:
        return None
    result = deepcopy(region)
    total = result["total"]
    for cell in result["cells"]:
        cell["share_pct"] = round(cell["count"] / total * 100, 2) if total else None
    return {
        "status": "available",
        "source_type": "official_resident_population",
        "schema_version": _snapshot()["schema_version"],
        "dataset": deepcopy(_snapshot()["dataset"]),
        "region": _identity(region),
        "period": "2026-10",
        "period_label": "2026년 10월 말",
        "source_url": _snapshot()["dataset"]["url"],
        "title": _snapshot()["dataset"]["title"],
        "metric": "주민등록 인구 · 10세 이상",
        "unit": "명",
        "cells": result["cells"],
        "total": result["all_ages_total"],
        "display_total": total,
        "under10": result["under10"],
        "all_ages_total": result["all_ages_total"],
    }


@router.get("/regions")
def population_regions(q: str = Query(min_length=1, max_length=80)) -> dict[str, Any]:
    dataset = _snapshot()["dataset"]
    return {
        "regions": find_regions(q),
        "dataset": {
            "period": "2026-10", "source_url": dataset["url"],
            "title": dataset["title"], "reference_date": dataset["reference_date"],
        },
    }


@router.get("/{region_code}")
def population_region(region_code: str) -> dict[str, Any]:
    result = get_population(region_code)
    if result is None:
        raise HTTPException(status_code=404, detail="해당 행정구역의 인구 자료를 찾지 못했어요.")
    return result
