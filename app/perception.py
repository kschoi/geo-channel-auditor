"""공개 근거 수집 → 인용 검증 → 고객 인식. 전체 고객에 대한 통계가 아니다."""
from __future__ import annotations

import asyncio
import re
from collections.abc import Awaitable, Callable
from datetime import date, datetime, timedelta, timezone
from typing import Any, Literal
from urllib.parse import parse_qsl, urlencode, urlsplit, urlunsplit

from pydantic import BaseModel, Field

from .auditor import AuditInput, _normalize_source, filter_store_sources
from .evidence_review import _chunks, prepare_sources
from .liner_client import LinerAPIError, LinerClient
from .market_data import MARKET_SYSTEM_PROMPT, build_market, market_prompt
from .perception_model import PERCEPTION_SYSTEM_PROMPT, build_perception, perception_prompt

KST = timezone(timedelta(hours=9))
Emit = Callable[[dict[str, Any]], Awaitable[None]]
OFFICIAL_DOMAINS = ("go.kr", "seoul.go.kr", "sgis.kostat.go.kr", "kosis.kr", "kostat.go.kr")


class PerceptionRequest(BaseModel):
    query: str = Field(default="", max_length=300)
    location: str = Field(default="", max_length=120)
    industry: str = Field(default="", max_length=100)
    store_name: str = Field(default="", max_length=120)
    competitors: list[str] = Field(default_factory=list, max_length=2)
    period_days: Literal[90, 180, 365] = 365
    api_key: str | None = Field(default=None, max_length=1000, repr=False)


def canonical_url(value: str) -> str:
    """추적 인자와 모바일 별칭만 제거. 문서를 구분하는 식별자는 유지한다."""
    try:
        parts = urlsplit(value)
        host = (parts.hostname or "").lower()
        if parts.scheme not in {"http", "https"} or not host or parts.username or parts.password:
            return ""
        if host in {"m.blog.naver.com", "blog.naver.com"}:
            host = "blog.naver.com"
            params = dict(parse_qsl(parts.query))
            if params.get("blogId") and params.get("logNo"):
                return f"https://{host}/{params['blogId']}/{params['logNo']}"
        host = host.removeprefix("www.")
        if parts.port and parts.port not in {80, 443}:
            host += f":{parts.port}"
        query = [(key, val) for key, val in parse_qsl(parts.query, keep_blank_values=True)
                 if not key.lower().startswith("utm_") and key.lower() not in
                 {"fbclid", "gclid", "dclid", "nclick", "trackingcode", "referrer", "ref"}]
        return urlunsplit(("https", host, parts.path.rstrip("/") or "/", urlencode(sorted(query)), ""))
    except ValueError:
        return ""


def source_channel(url: str) -> str:
    host = urlsplit(url).hostname or ""
    groups = {
        "naver_blog": ("blog.naver.com", "cafe.naver.com"),
        "review_platform": ("place.naver.com", "map.naver.com", "diningcode.com", "siksinhot.com", "map.kakao.com", "tripadvisor.com", "restaurantguru.com"),
        "social": ("instagram.com", "threads.net", "youtube.com", "youtu.be", "tiktok.com", "facebook.com"),
        "press": ("news.naver.com", "chosun.com", "hani.co.kr", "joongang.co.kr", "mk.co.kr", "hankyung.com", "yonhapnews.co.kr"),
    }
    for channel, domains in groups.items():
        if any(host == domain or host.endswith("." + domain) for domain in domains):
            return channel
    return "other"


def _period_status(raw: str, period: dict[str, Any]) -> str:
    match = re.match(r"^(\d{4})[-/.](\d{1,2})[-/.](\d{1,2})(?:\D|$)", raw)
    try:
        published = date(*map(int, match.groups())) if match else None
    except ValueError:
        published = None
    if published is None:
        return "unknown"
    return "in_period" if period["start"] <= published.isoformat() <= period["end"] else "out_of_period"


def _prepare(raw: list[dict[str, Any]], store_id: str, period: dict[str, Any], agent: Any = None) -> list[dict[str, Any]]:
    unique: dict[str, dict[str, Any]] = {}
    for source in raw:
        canonical = canonical_url(source.get("url", ""))
        if not canonical:
            continue
        if canonical not in unique:
            unique[canonical] = {**source, "canonical_url": canonical}
        else:
            existing = unique[canonical]
            if len(source.get("description", "")) > len(existing.get("description", "")):
                existing["description"] = source["description"]
            if not existing.get("date") and source.get("date"):
                existing["date"] = source["date"]
    kept = list(unique.values())[:24]
    aliases = {source["canonical_url"]: source["url"] for source in kept}
    chunks = [{**chunk, "sourceUrl": aliases.get(canonical_url(str(chunk.get("sourceUrl", ""))), "")}
              for chunk in _chunks(agent)]
    sources = prepare_sources(kept, {"referenceChunks": chunks})
    for source in sources:
        source.update(source_id=f"{store_id}-{source['source_id']}", store_id=store_id,
                      channel=source_channel(source["url"]),
                      period_status=_period_status(source.get("date", ""), period))
    return sources


def _stores(payload: PerceptionRequest) -> tuple[AuditInput, list[dict[str, str]]]:
    target = AuditInput.from_payload(payload.model_dump(exclude={"competitors", "period_days"}))
    if not target.store_name.strip() or not target.location.strip():
        raise ValueError("동네와 실제 매장명을 함께 입력해 주세요.")
    stores = [{"id": "target", "name": target.store_name, "location": target.location,
               "industry": target.industry, "query": payload.query or f"{target.location} {target.store_name}"}]
    identities = {(re.sub(r"\s+", "", target.location), re.sub(r"\s+", "", target.store_name))}
    for query in payload.competitors:
        query = query.strip()
        if not query:
            continue
        if len(query) > 300:
            raise ValueError("경쟁점 검색어는 300자 이내로 입력해 주세요.")
        rival = AuditInput.from_payload({"query": query})
        if not rival.store_name or not rival.location:
            raise ValueError("경쟁점도 동네와 실제 매장명을 함께 입력해 주세요.")
        identity = (re.sub(r"\s+", "", rival.location), re.sub(r"\s+", "", rival.store_name))
        if identity in identities:
            raise ValueError("같은 매장을 중복해서 비교할 수 없어요.")
        identities.add(identity)
        stores.append({"id": f"competitor-{len(stores)}", "name": rival.store_name,
                       "location": rival.location, "industry": rival.industry, "query": query})
    return target, stores


async def run_perception(payload: PerceptionRequest, emit: Emit | None = None) -> dict[str, Any]:
    target, stores = _stores(payload)
    client = LinerClient(api_key=payload.api_key)
    if not client.live:
        raise LinerAPIError("공개 자료를 검색할 Liner API 키가 없어요. API 설정에서 키를 입력해 주세요.")
    today = datetime.now(KST).date()
    period = {"days": payload.period_days, "start": (today - timedelta(days=payload.period_days - 1)).isoformat(),
              "end": today.isoformat(), "label": f"최근 {payload.period_days}일"}
    semaphore = asyncio.Semaphore(3)
    warnings: list[str] = []
    attempts: list[dict[str, Any]] = []

    async def progress(message: str, store_id: str = "") -> None:
        if emit:
            await emit({"message": message, "store_id": store_id})

    async def search(query: str, owner: str) -> list[dict[str, Any]]:
        async with semaphore:
            try:
                response = await client.search(query, max_results=8)
            except LinerAPIError:
                attempts.append({"store_id": owner, "kind": "search", "ok": False})
                return []
            attempts.append({"store_id": owner, "kind": "search", "ok": True})
            return [source for raw in response.get("results", [])
                    if (source := _normalize_source({**raw, "origin": "search"}))]

    async def collect_store(store: dict[str, str]) -> tuple[list[dict[str, Any]], int]:
        sid, name, location = store["id"], store["name"], store["location"]
        await progress(f"{name} · 공개 후기와 웹문서를 찾고 있어요", sid)
        base = f'"{name}" {location}'
        # Every store gets exactly the same source/query budget. Dates are checked on the server.
        searches = await asyncio.gather(*(search(base + suffix, sid) for suffix in (
            " 후기 방문 맛 서비스 가격 분위기", " 리뷰 불편 대기 주차 청결", " 블로그 리뷰 언론")))
        async with semaphore:
            try:
                agent = await client.search_agent([
                    {"role": "user", "content": f"{location} {name} {store['industry']}의 실제 방문 후기와 공개 웹문서를 찾아 주세요. "
                     "좋았던 점과 불편했던 점을 모두 찾고 원문 URL·게시일·발췌를 제공하세요. 다른 지점이나 다른 매장 후기는 제외하세요. "
                     "작성자의 성별·나이는 추측하지 마세요. 검색 자료에 없으면 없다고 하세요."}])
                attempts.append({"store_id": sid, "kind": "agent", "ok": True})
            except LinerAPIError:
                agent = {}
                attempts.append({"store_id": sid, "kind": "agent", "ok": False})
        raw = [source for group in searches for source in group]
        raw.extend(source for item in agent.get("references", [])
                   if (source := _normalize_source({**item, "origin": "agent"})))
        audit = AuditInput(location=location, industry=store["industry"], store_name=name)
        matched, excluded = filter_store_sources(audit, raw)
        sources = _prepare(matched, sid, period, agent)
        await progress(f"{name} · 매장명이 맞는 자료 {len(sources)}건을 모았어요", sid)
        return sources, len({canonical_url(source["url"]) for source in excluded})

    async def collect_market() -> list[dict[str, Any]]:
        await progress("상권 · 공개 인구·상권 자료를 찾고 있어요", "market")
        groups = await asyncio.gather(
            search(f'{target.location} 상권 상주인구 성별 연령별 site:go.kr', "market"),
            search(f'{target.location} 상권 분석 유동인구 소비 site:go.kr', "market"),
        )
        official = [source for group in groups for source in group
                    if any((urlsplit(source["url"]).hostname or "") == d or
                           (urlsplit(source["url"]).hostname or "").endswith("." + d) for d in OFFICIAL_DOMAINS)]
        return _prepare(official[:12], "market", period)

    tasks = [asyncio.create_task(collect_store(store)) for store in stores]
    market_task = asyncio.create_task(collect_market())
    try:
        collected = await asyncio.gather(*tasks)
        market_sources = await market_task
    finally:
        for task in [*tasks, market_task]:
            if not task.done():
                task.cancel()
        await asyncio.gather(*tasks, market_task, return_exceptions=True)
    sources = [source for group, _ in collected for source in group]
    eligible = [source for source in sources if source["period_status"] != "out_of_period"]
    if not any(attempt["ok"] for attempt in attempts if attempt["store_id"] == "target"):
        raise LinerAPIError("매장 자료 검색에 연결하지 못했어요. API 상태를 확인하고 다시 시도해 주세요.")
    failures = sum(not attempt["ok"] for attempt in attempts)
    if failures:
        warnings.append(f"검색·수집 {len(attempts)}회 중 {failures}회가 실패했어요. 수집이 끝난 자료만 분석했어요.")
    await progress("고객이 말한 경험을 읽고, 인용문과 출처를 대조하고 있어요")

    async def classify_reviews() -> dict[str, Any]:
        if not eligible:
            return {"observations": []}
        completion = await client.chat_completions([
            {"role": "system", "content": PERCEPTION_SYSTEM_PROMPT},
            {"role": "user", "content": perception_prompt(stores, eligible)},
        ], json_object=False)
        raw = client.parse_json_content(completion)
        if not isinstance(raw.get("observations"), list):
            raise LinerAPIError("후기 분석 응답을 읽지 못했어요. 다시 시도해 주세요.")
        return raw

    async def classify_market() -> dict[str, Any]:
        if not market_sources:
            return {"facts": []}
        try:
            completion = await client.chat_completions([
                {"role": "system", "content": MARKET_SYSTEM_PROMPT},
                {"role": "user", "content": market_prompt(target.location, market_sources)},
            ], json_object=False)
            return client.parse_json_content(completion)
        except LinerAPIError:
            warnings.append("공개 상권 자료의 수치를 검증하지 못했어요. 숫자를 추정하지 않았어요.")
            return {"facts": []}

    classification_tasks = [asyncio.create_task(classify_reviews()), asyncio.create_task(classify_market())]
    try:
        raw, market_raw = await asyncio.gather(*classification_tasks)
    finally:
        for task in classification_tasks:
            if not task.done():
                task.cancel()
        await asyncio.gather(*classification_tasks, return_exceptions=True)
    result = build_perception(stores, sources, raw, period)
    market = build_market(target.location, market_sources, market_raw)
    # The public data never exposes reviewer sex/age. Demographics stay unknown, not zero.
    if result.get("validation", {}).get("rejected"):
        warnings.append("출처·원문·기간 조건을 통과하지 못한 분류는 집계에서 제외했어요.")
    for store in result["stores"]:
        own = [attempt for attempt in attempts if attempt["store_id"] == store["id"]]
        store["coverage"] = {"attempted": len(own), "succeeded": sum(a["ok"] for a in own),
                             "failed": sum(not a["ok"] for a in own)}
    # Store/industry differences are descriptive, never automatic competitors or distance claims.
    if len(stores) > 1 and len({(s["location"], s["industry"]) for s in stores}) > 1:
        result["comparison"]["status"] = "limited"
        result["comparison"]["reason"] = "지역 또는 업종 표기가 달라요. 같은 상권·업종인지 확인하기 전에는 우열을 비교하지 않아요."
        for row in result["comparison"].get("rows", []):
            row["comparable"] = False
    result.update(
        schema_version="perception-v1", generated_at=datetime.now(KST).isoformat(), period=period,
        input={"query": payload.query, "store_name": target.store_name, "location": target.location,
               "industry": target.industry, "api_key_source": client.key_source},
        coverage={"search_calls": len(attempts), "successful_calls": len(attempts) - failures,
                  "failed_calls": failures, "documents": len(sources),
                  "excluded_documents": sum(excluded for _, excluded in collected),
                  "notes": ["Liner가 찾은 공개 검색 자료만 포함해요. 사이트 전체 리뷰를 수집한 결과가 아니에요.",
                            "자료가 없다는 것은 리뷰나 고객이 0명이라는 뜻이 아니에요.",
                            "매장별 최대 24개 출처의 일부 문자열을 읽어요. 분석 입력은 매장별 같은 글자 수 한도를 적용해요."]},
        sources=sources, market=market, warnings=warnings, pipeline={"calls": client.calls},
        # No raw competitor evidence is forwarded to the independent card workflow.
        card_context={"query": payload.query or f"{target.location} {target.store_name}"},
    )
    await progress("근거 확인을 마쳤어요. 고객 인식과 비교 결과를 정리했어요")
    return client._redact(result)
