"""3단계 진단 파이프라인.

1. Liner Search API       — 상권 위치 + 업종으로 검색 쿼리를 만들어 실시간 수요·경쟁 출처 수집
2. Liner Search Agent API — 상권 고객이 기대하는 정보·질문 요약 + 인용 수집
3. Liner Router API       — 채널 텍스트와 수요를 대조해 결핍 진단 + 채널별 보완 콘텐츠 생성

`LINER_API_KEY`가 없으면 같은 파이프라인이 `app/demo_fixtures.py`의 가상 응답으로 동작한다.
API 호출이 실패하면 남은 단계를 데모 데이터로 폴백하고, 응답에 `degraded` 플래그와 경고를 남긴다.
"""

from __future__ import annotations

import asyncio
import os
import time
from dataclasses import dataclass, field
from datetime import datetime, timedelta, timezone
from typing import Any, Awaitable, Callable

from . import samples as sample_data
from .demo_fixtures import build_generic_fixture, get_fixture
from .liner_client import DEFAULT_MODEL, LinerAPIError, LinerClient

KST = timezone(timedelta(hours=9))

DIMENSIONS: list[tuple[str, str, float]] = [
    ("visit_convenience", "방문편의", 0.25),
    ("price_transparency", "가격투명성", 0.30),
    ("differentiation", "차별화", 0.20),
    ("trust_evidence", "신뢰근거", 0.25),
]
DIMENSION_LABELS = {key: label for key, label, _ in DIMENSIONS}
CHANNEL_ORDER = ["instagram", "naver_place", "blog_faq"]
CHANNEL_LABELS = {
    "instagram": "Instagram 카드뉴스",
    "naver_place": "Naver Place 새소식",
    "blog_faq": "Blog FAQ",
}

STEP_TITLES = [
    "Liner Search · 상권 수요 수집",
    "Liner Search Agent · 고객 기대 정보 분석",
    "Liner Router · 결핍 진단 및 콘텐츠 처방",
]

Emit = Callable[[dict[str, Any]], Awaitable[None]]


@dataclass
class AuditInput:
    location: str
    industry: str
    store_name: str = ""
    channel_text: str = ""
    sample_id: str | None = None
    api_key: str | None = None
    max_results: int = 8

    @classmethod
    def from_payload(cls, payload: dict[str, Any]) -> "AuditInput":
        sample = sample_data.get_sample(payload.get("sample_id") or "")
        if sample:
            return cls(
                location=str(payload.get("location") or sample["location"]),
                industry=str(payload.get("industry") or sample["industry"]),
                store_name=str(payload.get("store_name") or sample["store_name"]),
                channel_text=str(payload.get("channel_text") or sample_data.channel_text_of(sample)),
                sample_id=sample["id"],
                api_key=payload.get("api_key"),
                max_results=int(payload.get("max_results") or 8),
            )
        return cls(
            location=str(payload.get("location") or "").strip(),
            industry=str(payload.get("industry") or "").strip(),
            store_name=str(payload.get("store_name") or "").strip(),
            channel_text=str(payload.get("channel_text") or "").strip(),
            sample_id=None,
            api_key=payload.get("api_key"),
            max_results=int(payload.get("max_results") or 8),
        )

    @property
    def display_name(self) -> str:
        return self.store_name or f"{self.location} {self.industry}".strip()


def build_queries(location: str, industry: str, sample_id: str | None = None) -> list[str]:
    """상권 + 업종으로 검색 쿼리 3개를 만든다. 샘플은 상권 맞춤 쿼리를 쓴다."""
    sample = sample_data.get_sample(sample_id or "")
    if sample:
        return list(sample["queries"])
    base = " ".join(part for part in (location, industry) if part).strip()
    return [
        f"{base} 주차 운영시간 예약",
        f"{base} 가격 메뉴 후기",
        f"{base} 차별점 비교",
    ]


# --------------------------------------------------------------------------- #
# 정규화 헬퍼
# --------------------------------------------------------------------------- #

_SCORE_ALIASES = {
    "visit_convenience": "visit_convenience",
    "visit": "visit_convenience",
    "convenience": "visit_convenience",
    "방문편의": "visit_convenience",
    "price_transparency": "price_transparency",
    "price": "price_transparency",
    "pricing": "price_transparency",
    "가격투명성": "price_transparency",
    "differentiation": "differentiation",
    "unique": "differentiation",
    "차별화": "differentiation",
    "trust_evidence": "trust_evidence",
    "trust": "trust_evidence",
    "evidence": "trust_evidence",
    "신뢰근거": "trust_evidence",
}

_CHANNEL_ALIASES = {
    "instagram": "instagram",
    "insta": "instagram",
    "ig": "instagram",
    "card_news": "instagram",
    "cardnews": "instagram",
    "naver_place": "naver_place",
    "naverplace": "naver_place",
    "naver": "naver_place",
    "place": "naver_place",
    "smartplace": "naver_place",
    "blog_faq": "blog_faq",
    "blog": "blog_faq",
    "faq": "blog_faq",
    "blogfaq": "blog_faq",
}


def _clamp_score(value: Any, default: int = 50) -> int:
    try:
        number = int(round(float(value)))
    except (TypeError, ValueError):
        return default
    return max(0, min(100, number))


def _as_list(value: Any) -> list[Any]:
    if isinstance(value, list):
        return value
    if isinstance(value, str) and value.strip():
        return [value]
    return []


def _normalize_source(raw: Any) -> dict[str, Any] | None:
    if not isinstance(raw, dict):
        if isinstance(raw, str) and raw.startswith("http"):
            host = raw.split("/")[2] if "//" in raw else ""
            return {
                "title": raw,
                "url": raw,
                "hostname": host,
                "faviconUrl": f"https://www.google.com/s2/favicons?domain={host}&sz=64",
                "description": "",
                "date": "",
                "demo": False,
                "origin": "model",
            }
        return None
    url = str(raw.get("url") or "").strip()
    if not url.startswith("http"):
        return None
    host = str(raw.get("hostname") or (url.split("/")[2] if "//" in url else ""))
    return {
        "title": str(raw.get("title") or url),
        "url": url,
        "hostname": host,
        "faviconUrl": raw.get("faviconUrl") or f"https://www.google.com/s2/favicons?domain={host}&sz=64",
        "description": str(raw.get("description") or ""),
        "date": str(raw.get("date") or ""),
        "demo": bool(raw.get("demo", False)),
        "origin": raw.get("origin", "model"),
    }


def _normalize_scores(raw: Any) -> list[dict[str, Any]]:
    """라우터가 dict/list 어느 형태로 주든 4개 세부 점수로 정규화한다."""
    by_key: dict[str, dict[str, Any]] = {}
    items: list[dict[str, Any]] = []
    if isinstance(raw, dict):
        for key, value in raw.items():
            if isinstance(value, dict):
                items.append({"key": key, **value})
            else:
                items.append({"key": key, "score": value})
    elif isinstance(raw, list):
        items = [item for item in raw if isinstance(item, dict)]

    for item in items:
        key_raw = str(item.get("key") or item.get("name") or item.get("label") or "").strip().lower()
        key = _SCORE_ALIASES.get(key_raw) or _SCORE_ALIASES.get(
            str(item.get("label") or "").strip()
        )
        if not key or key in by_key:
            continue
        by_key[key] = {
            "key": key,
            "label": DIMENSION_LABELS[key],
            "score": _clamp_score(item.get("score", item.get("value"))),
            "comment": str(item.get("comment") or item.get("reason") or "").strip(),
        }

    normalized: list[dict[str, Any]] = []
    for key, label, _weight in DIMENSIONS:
        normalized.append(
            by_key.get(key)
            or {"key": key, "label": label, "score": 50, "comment": "모델이 이 항목을 평가하지 않았습니다."}
        )
    return normalized


def weighted_overall(scores: list[dict[str, Any]]) -> int:
    total = 0.0
    for (key, _label, weight), item in zip(DIMENSIONS, scores):
        if item.get("key") == key:
            total += _clamp_score(item.get("score")) * weight
    return _clamp_score(total)


def _normalize_gaps(raw: Any, known_urls: dict[str, dict[str, Any]]) -> list[dict[str, Any]]:
    raw_list = [g for g in _as_list(raw) if isinstance(g, dict)]
    gaps: list[dict[str, Any]] = []
    for index, item in enumerate(raw_list[:3], start=1):
        urls = [u for u in _as_list(item.get("source_urls") or item.get("sources")) if isinstance(u, str)]
        url_list: list[str] = []
        resolved: list[dict[str, Any]] = []
        for url in urls:
            url = url.strip()
            if not url.startswith("http") or url in url_list:
                continue
            url_list.append(url)
            source = known_urls.get(url)
            if source:
                resolved.append(source)
            else:
                minimal = _normalize_source(url)
                if minimal:
                    resolved.append(minimal)
        severity = str(item.get("severity") or "medium").lower()
        if severity not in ("high", "medium", "low"):
            severity = "medium"
        dimension = _SCORE_ALIASES.get(str(item.get("dimension") or "").lower(), "")
        gaps.append(
            {
                "id": str(item.get("id") or f"gap-{index}"),
                "title": str(item.get("title") or f"정보 결핍 {index}"),
                "severity": severity,
                "dimension": dimension,
                "dimension_label": DIMENSION_LABELS.get(dimension, ""),
                "reason": str(item.get("reason") or item.get("missing_reason") or "").strip(),
                "demand_evidence": str(
                    item.get("demand_evidence") or item.get("evidence") or item.get("customer_demand") or ""
                ).strip(),
                "source_urls": url_list,
                "sources": resolved,
                "fix_hint": str(item.get("fix_hint") or item.get("recommendation") or "").strip(),
            }
        )
    return gaps


def _normalize_contents(raw: Any) -> list[dict[str, Any]]:
    items = [c for c in _as_list(raw) if isinstance(c, dict)]
    by_channel: dict[str, dict[str, Any]] = {}
    for item in items:
        channel_raw = str(item.get("channel") or item.get("type") or "").strip().lower().replace("-", "_")
        channel = _CHANNEL_ALIASES.get(channel_raw)
        if not channel or channel in by_channel:
            continue
        hashtags = item.get("hashtags") or []
        if isinstance(hashtags, str):
            hashtags = [tag.strip() for tag in hashtags.replace(",", " ").split() if tag.strip()]
        by_channel[channel] = {
            "channel": channel,
            "channel_label": CHANNEL_LABELS[channel],
            "title": str(item.get("title") or "").strip(),
            "summary": str(item.get("summary") or item.get("intent") or "").strip(),
            "body": str(item.get("body") or item.get("content") or item.get("copy") or "").strip(),
            "hashtags": [str(tag) for tag in hashtags][:12],
            "caution": str(item.get("caution") or item.get("warning") or "").strip(),
        }
    return [by_channel[c] for c in CHANNEL_ORDER if c in by_channel] + [
        item for item in by_channel.values() if item["channel"] not in CHANNEL_ORDER
    ]


# --------------------------------------------------------------------------- #
# 프롬프트
# --------------------------------------------------------------------------- #

ROUTER_SYSTEM_PROMPT = (
    "너는 오프라인 매장의 외부 채널(인스타그램·네이버 플레이스·블로그)을 진단하는 상권 마케팅 분석가다. "
    "주어진 상권 검색 수요와 현재 채널 텍스트를 대조해, 고객이 방문 전에 확인하려는 정보 중 채널에 없는 "
    "것을 결핍으로 진단하고, 즉시 발행 가능한 보완 콘텐츠를 쓴다.\n\n"
    "반드시 아래 JSON 객체 하나만 출력한다. 설명 문장, 마크다운, 코드펜스를 붙이지 않는다.\n"
    "{\n"
    '  "overall_score": 0~100 정수,\n'
    '  "summary": "한 문장 진단",\n'
    '  "scores": [\n'
    '    {"key": "visit_convenience", "label": "방문편의", "score": 0~100, "comment": "근거 한 문장"},\n'
    '    {"key": "price_transparency", "label": "가격투명성", "score": 0~100, "comment": "근거 한 문장"},\n'
    '    {"key": "differentiation", "label": "차별화", "score": 0~100, "comment": "근거 한 문장"},\n'
    '    {"key": "trust_evidence", "label": "신뢰근거", "score": 0~100, "comment": "근거 한 문장"}\n'
    "  ],\n"
    '  "gaps": [\n'
    '    {"title": "결핍 제목", "severity": "high 또는 medium", "dimension": "visit_convenience|price_transparency|differentiation|trust_evidence",'
    ' "reason": "왜 채널에 없는지/왜 문제인지", "demand_evidence": "고객 검색 수요 근거",'
    ' "source_urls": ["제공된 출처 URL"], "fix_hint": "한 줄 처방"}\n'
    "  ],\n"
    '  "contents": [\n'
    '    {"channel": "instagram", "title": "카드뉴스 제목", "summary": "기획 의도",'
    ' "body": "1장부터 5장까지 장별 카피를 포함한 완성 원고", "hashtags": ["#태그"], "caution": "주의 문구 또는 빈 문자열"},\n'
    '    {"channel": "naver_place", "title": "새소식 제목", "summary": "기획 의도", "body": "완성 원고", "hashtags": [], "caution": ""},\n'
    '    {"channel": "blog_faq", "title": "FAQ 제목", "summary": "기획 의도", "body": "Q/A 3개 이상 완성 원고", "hashtags": [], "caution": ""}\n'
    "  ]\n"
    "}\n\n"
    "규칙: gaps는 정확히 3개, contents는 instagram·naver_place·blog_faq 각 1개. "
    "source_urls에는 제공된 출처 목록의 URL만 쓴다. 본문은 실제로 발행할 수 있는 완성된 한국어 문장으로 쓰고, "
    "확인되지 않은 수치를 지어내지 말고 [확인 필요] 표기를 쓴다. 의료·법률 등 규제 업종이면 "
    "caution에 광고 심의·표시광고 주의를 적는다."
)


def _router_user_prompt(
    audit: AuditInput,
    channel_text: str,
    demand_answer: str,
    sources: list[dict[str, Any]],
    queries: list[str],
) -> str:
    source_lines = "\n".join(
        f"- {src['title']} | {src['url']}" for src in sources[:12]
    ) or "- (수집된 출처 없음)"
    return (
        f"[매장] {audit.display_name}\n"
        f"[상권 위치] {audit.location}\n"
        f"[업종] {audit.industry}\n"
        f"[상권 검색 쿼리] {', '.join(queries)}\n\n"
        f"[상권 고객 수요 요약]\n{demand_answer}\n\n"
        f"[수집된 출처]\n{source_lines}\n\n"
        f"[현재 외부 채널 텍스트]\n{channel_text[:6000]}\n\n"
        "위 정보를 근거로 결핍 진단과 채널별 보완 콘텐츠를 JSON으로 작성해라."
    )


# --------------------------------------------------------------------------- #
# 파이프라인
# --------------------------------------------------------------------------- #

async def _emit(emit: Emit | None, **event: Any) -> None:
    if emit is not None:
        await emit(event)


async def _demo_pause() -> None:
    try:
        ms = int(os.environ.get("DEMO_LATENCY_MS", "380"))
    except ValueError:
        ms = 380
    if ms > 0:
        await asyncio.sleep(ms / 1000)


def _demo_payload(audit: AuditInput, queries: list[str]) -> dict[str, Any]:
    fixture = get_fixture(audit.sample_id)
    if fixture:
        return fixture
    return build_generic_fixture(
        store_name=audit.display_name,
        location=audit.location,
        industry=audit.industry,
        channel_text=audit.channel_text,
        queries=queries,
    )


async def run_audit(audit: AuditInput, emit: Emit | None = None) -> dict[str, Any]:
    """3단계 진단을 실행하고 UI가 그대로 렌더링할 수 있는 결과 dict를 돌려준다."""
    started = time.perf_counter()
    if not audit.location and not audit.industry:
        raise ValueError("상권 위치와 업종을 입력해 주세요.")
    if not audit.channel_text:
        raise ValueError("진단할 외부 채널 텍스트를 입력해 주세요.")
    client = LinerClient(api_key=audit.api_key)
    live = client.live
    using_demo = not live
    degraded = False
    warnings: list[str] = []

    queries = build_queries(audit.location, audit.industry, audit.sample_id)
    demo_payload: dict[str, Any] | None = None
    if using_demo:
        demo_payload = _demo_payload(audit, queries)
        if not get_fixture(audit.sample_id):
            warnings.append(
                "샘플이 아닌 직접 입력은 데모 템플릿으로 진단합니다. LINER_API_KEY를 설정하면 실제 상권 검색 결과로 진단합니다."
            )
    steps: list[dict[str, Any]] = []

    def fallback(reason: str, step_no: int) -> None:
        nonlocal using_demo, degraded, demo_payload
        degraded = True
        using_demo = True
        if demo_payload is None:
            demo_payload = _demo_payload(audit, queries)
        warnings.append(
            f"{STEP_TITLES[step_no - 1]} 단계에서 Liner API 호출이 실패해 데모 데이터로 대체했습니다: {reason}"
        )

    if not audit.location and not audit.industry:
        raise ValueError("상권 위치와 업종을 입력해 주세요.")
    if not audit.channel_text:
        raise ValueError("진단할 외부 채널 텍스트를 입력해 주세요.")

    # ---------------- Step 1 : Liner Search ----------------
    step_started = time.perf_counter()
    await _emit(emit, type="step", index=1, status="running", title=STEP_TITLES[0],
                detail=f"쿼리 {len(queries)}개로 상권 수요를 수집합니다.")

    search_bundles: list[dict[str, Any]] = []
    search_sources: list[dict[str, Any]] = []
    for query in queries:
        results: list[dict[str, Any]] = []
        if using_demo:
            demo_results = (demo_payload or {}).get("search", {}).get(query, [])
            results = [s for s in (_normalize_source(r) for r in demo_results) if s]
            client.record("search.web", mode="demo", status="ok", latency_ms=0,
                          detail=f"query={query}")
        else:
            try:
                response = await client.search(query, max_results=audit.max_results)
            except LinerAPIError as exc:
                fallback(str(exc), 1)
                demo_results = (demo_payload or {}).get("search", {}).get(query, [])
                results = [s for s in (_normalize_source(r) for r in demo_results) if s]
                client.record("search.web", mode="demo", status="fallback", latency_ms=0, detail=str(exc))
            else:
                results = [s for s in (_normalize_source({**r, "origin": "search"}) for r in response.get("results", [])) if s]
        for source in results:
            source["origin"] = "search"
        search_sources.extend(results)
        search_bundles.append({"query": query, "result_count": len(results), "results": results})

    if using_demo:
        await _demo_pause()
    step1_ms = int((time.perf_counter() - step_started) * 1000)
    steps.append({
        "index": 1,
        "title": STEP_TITLES[0],
        "status": "done",
        "latency_ms": step1_ms,
        "detail": f"쿼리 {len(queries)}개 · 출처 {len(search_sources)}건 수집",
        "queries": queries,
        "counts": {"queries": len(queries), "sources": len(search_sources)},
    })
    await _emit(emit, type="step", index=1, status="done", title=STEP_TITLES[0],
                detail=f"출처 {len(search_sources)}건 수집 완료", latency_ms=step1_ms)

    # ---------------- Step 2 : Liner Search Agent ----------------
    step_started = time.perf_counter()
    await _emit(emit, type="step", index=2, status="running", title=STEP_TITLES[1],
                detail="수집한 출처를 바탕으로 고객 기대 정보를 분석합니다.")
    agent_answer = ""
    agent_references: list[dict[str, Any]] = []
    if using_demo:
        agent = (demo_payload or {}).get("agent", {})
        agent_answer = str(agent.get("answer") or "")
        agent_references = [s for s in (_normalize_source({**r, "origin": "agent"}) for r in agent.get("references", [])) if s]
        client.record("agents.search", mode="demo", status="ok", latency_ms=0)
    else:
        agent_messages = [
            {
                "role": "user",
                "content": (
                    f"{audit.location} {audit.industry} 상권에서 고객이 방문 전에 확인하려는 정보와 자주 묻는 질문을 "
                    "5개 이내로 정리해줘. 출처를 인용하고, 마지막에 현재 채널에 빠졌을 가능성이 큰 정보를 짚어줘.\n\n"
                    f"현재 채널 요약: {audit.channel_text[:800]}"
                ),
            }
        ]
        try:
            agent = await client.search_agent(agent_messages)
        except LinerAPIError as exc:
            fallback(str(exc), 2)
            agent = (demo_payload or {}).get("agent", {})
        agent_answer = str(agent.get("answer") or "")
        agent_references = [
            s for s in (_normalize_source({**r, "origin": "agent"}) for r in agent.get("references", [])) if s
        ]

    if using_demo:
        await _demo_pause()
    step2_ms = int((time.perf_counter() - step_started) * 1000)
    steps.append({
        "index": 2,
        "title": STEP_TITLES[1],
        "status": "done",
        "latency_ms": step2_ms,
        "detail": f"인용 {len(agent_references)}건 · 수요 요약 {len(agent_answer)}자",
        "counts": {"references": len(agent_references)},
    })
    await _emit(emit, type="step", index=2, status="done", title=STEP_TITLES[1],
                detail=f"인용 {len(agent_references)}건 정리 완료", latency_ms=step2_ms)

    # ---------------- Step 3 : Liner Router ----------------
    step_started = time.perf_counter()
    await _emit(emit, type="step", index=3, status="running", title=STEP_TITLES[2],
                detail="채널 텍스트와 상권 수요를 대조해 결핍과 처방을 생성합니다.")

    all_sources: list[dict[str, Any]] = []
    seen: set[str] = set()
    for source in search_sources + agent_references:
        if source["url"] in seen:
            continue
        seen.add(source["url"])
        all_sources.append(source)
    known_urls = {s["url"]: s for s in all_sources}

    analysis: dict[str, Any] = {}
    if using_demo:
        analysis = dict((demo_payload or {}).get("analysis", {}))
        client.record("chat.completions", mode="demo", status="ok", latency_ms=0, detail=f"model={DEFAULT_MODEL}")
    else:
        messages = [
            {"role": "system", "content": ROUTER_SYSTEM_PROMPT},
            {
                "role": "user",
                "content": _router_user_prompt(audit, audit.channel_text, agent_answer, all_sources, queries),
            },
        ]
        try:
            completion = await client.chat_completions(messages)
            analysis = client.parse_json_content(completion)
        except LinerAPIError as exc:
            fallback(str(exc), 3)
            analysis = dict((demo_payload or {}).get("analysis", {}))

    scores = _normalize_scores(analysis.get("scores"))
    for item in scores:
        item["score"] = _clamp_score(item["score"])
    computed_overall = weighted_overall(scores)
    overall = _clamp_score(analysis.get("overall_score"), computed_overall)
    gaps = _normalize_gaps(analysis.get("gaps"), known_urls)
    contents = _normalize_contents(analysis.get("contents"))
    for gap in gaps:
        if not gap["sources"]:
            gap["sources"] = all_sources[:2]
            gap["source_urls"] = [s["url"] for s in gap["sources"]]

    if len(gaps) != 3:
        warnings.append(f"결핍이 3개가 아닌 {len(gaps)}개로 반환되었습니다.")
    if len(contents) != 3:
        warnings.append(f"콘텐츠가 3종이 아닌 {len(contents)}종으로 반환되었습니다.")
    if degraded:
        warnings.append("일부 단계가 데모 데이터로 대체되었습니다. 화면의 DEMO 배지와 파이프라인 로그를 확인하세요.")

    if using_demo:
        await _demo_pause()
    step3_ms = int((time.perf_counter() - step_started) * 1000)
    steps.append({
        "index": 3,
        "title": STEP_TITLES[2],
        "status": "done",
        "latency_ms": step3_ms,
        "detail": f"결핍 {len(gaps)}건 · 콘텐츠 {len(contents)}종 · 종합 {overall}점",
        "counts": {"gaps": len(gaps), "contents": len(contents)},
    })
    await _emit(emit, type="step", index=3, status="done", title=STEP_TITLES[2],
                detail=f"진단 완료 · 종합 {overall}점", latency_ms=step3_ms)

    total_ms = int((time.perf_counter() - started) * 1000)
    totals = client.totals()
    return {
        "status": "ok",
        "mode": "demo" if using_demo else "live",
        "degraded": degraded,
        "warnings": warnings,
        "generated_at": datetime.now(KST).isoformat(timespec="seconds"),
        "input": {
            "store_name": audit.display_name,
            "location": audit.location,
            "industry": audit.industry,
            "sample_id": audit.sample_id,
            "channel_text": audit.channel_text,
            "api_key_source": client.key_source,
        },
        "queries": queries,
        "demand": {"answer": agent_answer, "references": agent_references},
        "overall_score": overall,
        "computed_overall": computed_overall,
        "summary": str(analysis.get("summary") or "").strip(),
        "scores": scores,
        "gaps": gaps,
        "contents": contents,
        "sources": all_sources,
        "pipeline": {
            "mode": "demo" if using_demo else "live",
            "degraded": degraded,
            "model": DEFAULT_MODEL,
            "steps": steps,
            "calls": client.calls,
            "totals": totals,
            "latency_ms": total_ms,
            "base_url": client.base_url,
        },
    }
