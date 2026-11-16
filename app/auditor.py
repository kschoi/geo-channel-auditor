"""3단계 진단 파이프라인.

1. Liner Search API       — 상권 위치 + 업종으로 검색 쿼리를 만들어 실시간 수요·경쟁 출처 수집
                             (+ `channel_text`가 비어 있거나 짧으면 매장 공개 채널을 자동 수집)
2. Liner Search Agent API — 상권 고객이 기대하는 정보·질문 요약 + 인용 수집
3. Liner Router API       — 채널 텍스트와 수요를 대조해 결핍 진단 + 채널별 보완 콘텐츠 생성

부가 분석: Liner Search와 Liner Search Agent가 인용한 모든 출처 URL을 채널(네이버 블로그·플레이스·SNS·공식 웹·리뷰/언론)로
분류한 `citation_analysis`와, 그 비중으로 "어느 채널부터 채울지"를 정한 `recommended_channels`를
결과에 함께 담는다. 인용 비중은 Liner의 검색·에이전트가 실제로 인용한 출처만 세며, GPT·Perplexity·Gemini·Claude 등 다른 AI의 인용을 별도로 수집하지는 않는다.

`LINER_API_KEY`가 없으면 같은 파이프라인이 `app/demo_fixtures.py`의 가상 응답으로 동작한다.
API 호출이 실패하면 남은 단계를 데모 데이터로 폴백하고, 응답에 `degraded` 플래그와 경고를 남긴다.
"""

from __future__ import annotations

import asyncio
import os
import time
from collections.abc import Awaitable, Callable
from dataclasses import dataclass
from datetime import datetime, timedelta, timezone
from typing import Any

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

# 인용 출처 채널 분류 — UI·프롬프트·폴백이 같은 순서와 라벨을 공유한다.
CITATION_CHANNELS: list[tuple[str, str]] = [
    ("naver_blog", "네이버 블로그"),
    ("naver_place", "네이버 플레이스"),
    ("instagram_sns", "인스타그램·SNS"),
    ("official_web", "공식 웹·예약"),
    ("review_media", "리뷰·언론·기타"),
]
CITATION_LABELS = dict(CITATION_CHANNELS)
CITATION_ORDER = {key: index for index, (key, _label) in enumerate(CITATION_CHANNELS)}

# 채널 분류 기준 도메인. 호스트가 같거나 `.<도메인>`으로 끝나면 해당 채널이다.
CITATION_DOMAINS: dict[str, tuple[str, ...]] = {
    "naver_blog": ("blog.naver.com", "cafe.naver.com", "post.naver.com"),
    "naver_place": ("place.naver.com", "map.naver.com", "naver.me"),
    "instagram_sns": ("instagram.com", "threads.net", "youtube.com", "youtu.be", "tiktok.com", "facebook.com"),
    "official_web": ("catchtable.net", "catchtable.co.kr", "booking.naver.com", "smartstore.naver.com"),
    "review_media": ("tistory.com", "brunch.co.kr", "diningcode.com", "siksinhot.com", "kakao.com"),
}

# 추천 채널 → 이 도구가 실제로 만들어 줄 수 있는 콘텐츠 채널
CITATION_TO_CONTENT: dict[str, str | None] = {
    "naver_blog": "blog_faq",
    "naver_place": "naver_place",
    "instagram_sns": "instagram",
    "official_web": None,
    "review_media": None,
}

# 이보다 짧은 채널 텍스트는 "정보가 거의 없는 입력"으로 보고 공개 채널 자동 수집을 붙인다.
MIN_CHANNEL_TEXT_CHARS = 80

COLLECTED_MODE_LABELS = {
    "manual": "직접 입력",
    "auto": "자동 수집",
    "hybrid": "직접 입력 + 자동 수집",
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
    auto_collect: bool = True

    @classmethod
    def from_payload(cls, payload: dict[str, Any]) -> AuditInput:
        sample = sample_data.get_sample(payload.get("sample_id") or "")
        if sample:
            # 채널 텍스트를 명시하지 않은 경우에만 샘플 원문을 쓴다. 빈 문자열로 보내면 "자동 수집" 의도다.
            raw_text = payload.get("channel_text")
            channel_text = (
                sample_data.channel_text_of(sample) if raw_text is None else str(raw_text).strip()
            )
            return cls(
                location=str(payload.get("location") or sample["location"]),
                industry=str(payload.get("industry") or sample["industry"]),
                store_name=str(payload.get("store_name") or sample["store_name"]),
                channel_text=channel_text,
                sample_id=sample["id"],
                api_key=payload.get("api_key"),
                max_results=int(payload.get("max_results") or 8),
                auto_collect=bool(payload.get("auto_collect", True)),
            )
        return cls(
            location=str(payload.get("location") or "").strip(),
            industry=str(payload.get("industry") or "").strip(),
            store_name=str(payload.get("store_name") or "").strip(),
            channel_text=str(payload.get("channel_text") or "").strip(),
            sample_id=None,
            api_key=payload.get("api_key"),
            max_results=int(payload.get("max_results") or 8),
            auto_collect=bool(payload.get("auto_collect", True)),
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
        number = round(float(value))
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
                "channel": None,
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
        "channel": raw.get("channel") or None,
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
# 인용 출처 분석 / 채널 우선순위 추천
# --------------------------------------------------------------------------- #

_CITATION_ALIASES = {
    "naver_blog": "naver_blog",
    "naverblog": "naver_blog",
    "blog": "naver_blog",
    "blog_faq": "naver_blog",
    "naver_blog_faq": "naver_blog",
    "cafe": "naver_blog",
    "네이버 블로그": "naver_blog",
    "블로그": "naver_blog",
    "naver_place": "naver_place",
    "naverplace": "naver_place",
    "place": "naver_place",
    "smartplace": "naver_place",
    "map": "naver_place",
    "네이버 플레이스": "naver_place",
    "플레이스": "naver_place",
    "instagram_sns": "instagram_sns",
    "instagram": "instagram_sns",
    "insta": "instagram_sns",
    "ig": "instagram_sns",
    "sns": "instagram_sns",
    "reels": "instagram_sns",
    "youtube": "instagram_sns",
    "인스타그램": "instagram_sns",
    "인스타": "instagram_sns",
    "official_web": "official_web",
    "official": "official_web",
    "official_site": "official_web",
    "homepage": "official_web",
    "website": "official_web",
    "web": "official_web",
    "reservation": "official_web",
    "공식 홈페이지": "official_web",
    "공식웹": "official_web",
    "review_media": "review_media",
    "review": "review_media",
    "reviews": "review_media",
    "media": "review_media",
    "news": "review_media",
    "etc": "review_media",
    "리뷰": "review_media",
    "언론": "review_media",
}


def _host_of(url: str) -> str:
    host = url.split("://", 1)[-1].split("/", 1)[0].split("?", 1)[0].lower()
    if "@" in host:
        host = host.rsplit("@", 1)[-1]
    if ":" in host:
        host = host.split(":", 1)[0]
    return host.strip(".")


def citation_key(raw: Any) -> str | None:
    """모델이 준 채널 표기를 5개 분류 키로 정규화한다."""
    token = str(raw or "").strip().lower().replace("-", "_").replace(" ", "_")
    if not token:
        return None
    if token in _CITATION_ALIASES:
        return _CITATION_ALIASES[token]
    compact = token.replace("_", "")
    for alias, key in _CITATION_ALIASES.items():
        if alias.replace("_", "").replace(" ", "") == compact:
            return key
    return None


def classify_channel(url: str, hint: str | None = None) -> str:
    """출처 URL 하나를 인용 채널로 분류한다.

    `hint`(데모 픽스처가 선언한 채널)가 있으면 그것을 먼저 믿고, 없으면 호스트 도메인으로 판단한다.
    어느 규칙에도 걸리지 않는 도메인(뉴스·기타)은 `review_media`로 둔다.
    """
    hinted = citation_key(hint)
    if hinted:
        return hinted
    host = _host_of(str(url or ""))
    if not host:
        return "review_media"
    if host == "search.naver.com" or host.endswith(".search.naver.com"):
        lowered = str(url).lower()
        if "where=blog" in lowered or "section=blog" in lowered:
            return "naver_blog"
        if "where=place" in lowered or "where=map" in lowered:
            return "naver_place"
    for key, domains in CITATION_DOMAINS.items():
        if any(host == domain or host.endswith("." + domain) for domain in domains):
            return key
    return "review_media"


def _shares(counts: dict[str, int], total: int) -> dict[str, float]:
    """합계가 정확히 100.0이 되는 백분율(소수 1자리)을 최대잔여법으로 계산한다."""
    if total <= 0:
        return {key: 0.0 for key in counts}
    raw = {key: count * 1000 / total for key, count in counts.items()}
    tenths = {key: int(value) for key, value in raw.items()}
    remainder = 1000 - sum(tenths.values())
    for key in sorted(raw, key=lambda item: raw[item] - tenths[item], reverse=True)[: max(0, remainder)]:
        tenths[key] += 1
    return {key: value / 10 for key, value in tenths.items()}


def build_citation_analysis(sources: list[dict[str, Any]]) -> dict[str, Any]:
    """수집된 모든 출처 URL을 채널별로 분류하고 비중을 계산한다."""
    buckets: dict[str, dict[str, Any]] = {
        key: {"channel": key, "label": label, "count": 0, "urls": [], "domains": []}
        for key, label in CITATION_CHANNELS
    }
    seen: set[str] = set()
    total = 0
    for source in sources:
        url = str(source.get("url") or "").strip()
        if not url.startswith("http") or url in seen:
            continue
        seen.add(url)
        key = classify_channel(url, source.get("channel"))
        bucket = buckets[key]
        bucket["count"] += 1
        if len(bucket["urls"]) < 6:
            bucket["urls"].append(url)
        host = str(source.get("hostname") or _host_of(url)) or _host_of(url)
        if host and host not in bucket["domains"]:
            bucket["domains"].append(host)
        total += 1

    shares = _shares({key: bucket["count"] for key, bucket in buckets.items()}, total)
    channels = list(buckets.values())
    for channel in channels:
        channel["share_pct"] = shares[channel["channel"]]
        channel["domains"] = channel["domains"][:4]
    channels.sort(key=lambda item: (-item["count"], CITATION_ORDER[item["channel"]]))

    top = channels[0] if channels and channels[0]["count"] else None
    if top:
        cited = [c for c in channels if c["count"]]
        breakdown = ", ".join(f"{c['label']} {c['share_pct']}%({c['count']}건)" for c in cited)
        summary = (
            f"Liner Search·Search Agent가 인용한 출처 {total}건 중 "
            f"{cited[0]['label']}가 {cited[0]['share_pct']}%로 가장 많습니다. {breakdown}."
        )
    else:
        summary = "분석할 출처 URL이 없어 인용 비중을 계산하지 못했습니다."

    return {
        "total": total,
        "summary": summary,
        "channels": channels,
        "top_channel": top["channel"] if top else None,
        "top_label": top["label"] if top else "",
        "top_share_pct": top["share_pct"] if top else 0.0,
    }


_RECOMMEND_ACTIONS = {
    "naver_blog": "검색 유입 직후 비교 단계에서 읽히는 블로그 글을 먼저 채우세요. FAQ·후기형 글이 상권 검색 상위를 차지합니다.",
    "naver_place": "지도·플레이스에서 확인하는 가격·주차·영업시간을 먼저 고정하세요. 방문 직전 판단이 여기서 끝납니다.",
    "instagram_sns": "최근 게시물에서 확인할 수 있는 정보를 카드뉴스로 고정하세요. 저장·공유가 방문으로 이어집니다.",
    "official_web": "예약·가격·이용 안내를 공식 페이지에 먼저 정리하세요. 예약 플랫폼 노출과 함께 봐야 합니다.",
    "review_media": "후기·언론 인용이 많은 만큼 검증 근거를 내 채널에 연결하세요. FAQ와 사례로 신뢰를 보강합니다.",
}


def _recommend_reason(channel: dict[str, Any], rank: int) -> str:
    action = _RECOMMEND_ACTIONS.get(channel["channel"], "이 채널의 정보를 먼저 보완하세요.")
    if channel.get("count"):
        lead = (
            f"Liner 인용 비중 {channel['share_pct']}%({channel['count']}건)로 "
            f"인용 순위 {rank}위 채널입니다."
        )
    else:
        lead = "수집된 인용이 거의 없어 상대적으로 가장 비어 있는 채널입니다."
    return f"{lead} {action}"


def _fallback_recommendations(citation: dict[str, Any]) -> list[dict[str, Any]]:
    """Router가 추천을 주지 못해도 인용 비중으로 항상 3개 추천을 만든다."""
    channels = list(citation.get("channels") or [])
    ranked = sorted(
        channels,
        key=lambda item: (-int(item.get("count") or 0), CITATION_ORDER.get(item["channel"], 99)),
    )
    recommendations: list[dict[str, Any]] = []
    for rank, channel in enumerate(ranked[:3], start=1):
        recommendations.append(
            {
                "rank": rank,
                "channel": channel["channel"],
                "label": channel["label"],
                "reason": _recommend_reason(channel, rank),
                "priority_badge": f"{rank}순위",
                "share_pct": channel["share_pct"],
                "count": channel["count"],
                "content_channel": CITATION_TO_CONTENT.get(channel["channel"]),
                "source": "citation",
            }
        )
    return recommendations


def _normalize_recommendations(raw: Any, citation: dict[str, Any]) -> list[dict[str, Any]]:
    """Router 응답을 정규화하고, 비었거나 모자라면 인용 비중 기반 추천으로 채운다."""
    fallback = _fallback_recommendations(citation)
    fallback_by_channel = {item["channel"]: item for item in fallback}

    items = [item for item in _as_list(raw) if isinstance(item, dict)]

    def rank_of(item: dict[str, Any]) -> int:
        try:
            return int(item.get("rank"))
        except (TypeError, ValueError):
            return 99

    items.sort(key=rank_of)

    merged: list[dict[str, Any]] = []
    used: set[str] = set()
    for item in items:
        key = citation_key(item.get("channel") or item.get("type") or item.get("label"))
        if not key:
            # 콘텐츠 채널 이름만 준 경우(instagram/naver_place/blog_faq)도 받아 준다.
            content_key = _CHANNEL_ALIASES.get(
                str(item.get("channel") or item.get("type") or "").strip().lower().replace("-", "_")
            )
            if content_key:
                inverse = {value: key for key, value in CITATION_TO_CONTENT.items() if value}
                key = inverse.get(content_key)
        if not key or key in used:
            continue
        used.add(key)
        base = fallback_by_channel.get(key, {})
        merged.append(
            {
                "channel": key,
                "label": CITATION_LABELS[key],
                "reason": str(item.get("reason") or item.get("why") or base.get("reason") or "").strip(),
                "share_pct": base.get("share_pct", 0.0),
                "count": base.get("count", 0),
                "content_channel": base.get("content_channel") or CITATION_TO_CONTENT.get(key),
                "source": "router",
            }
        )

    for item in fallback:
        if len(merged) >= 3:
            break
        if item["channel"] in used:
            continue
        used.add(item["channel"])
        merged.append(dict(item))

    # 콘텐츠 채널이 비어 있으면(추천이 공식 웹·리뷰뿐이면) 비중이 높은 콘텐츠 채널로 메운다.
    for rank, item in enumerate(merged[:3], start=1):
        item["rank"] = rank
        item["priority_badge"] = f"{rank}순위"
        if not item.get("reason"):
            item["reason"] = _recommend_reason(
                {
                    "channel": item["channel"],
                    "count": item.get("count", 0),
                    "share_pct": item.get("share_pct", 0.0),
                },
                rank,
            )
    return merged[:3]


def apply_content_priority(contents: list[dict[str, Any]], recommendations: list[dict[str, Any]]) -> None:
    """추천 순위 중 콘텐츠로 만들 수 있는 첫 채널에 추천 배지를 달는다.

    1순위가 공식 웹·리뷰처럼 직접 만들 수 없는 채널이면, 그다음 추천 채널에
    `2순위 추천`처럼 실제 순위를 그대로 표기한다.
    """
    top = next(
        (
            item
            for item in sorted(recommendations, key=lambda entry: entry.get("rank", 99))
            if item.get("content_channel")
        ),
        None,
    )
    for content in contents:
        matched = bool(top and top["content_channel"] == content["channel"])
        content["priority_rank"] = top["rank"] if matched else None
        content["priority_badge"] = f"{top['rank']}순위 추천" if matched else ""
        content["priority_reason"] = top["reason"] if matched else ""


# --------------------------------------------------------------------------- #
# 공개 채널 자동 수집
# --------------------------------------------------------------------------- #


def build_collection_queries(audit: AuditInput) -> list[str]:
    """매장명·위치·업종만으로 공개 채널 노출 현황을 찾는 검색 쿼리 2개."""
    first = " ".join(part for part in (audit.location, audit.store_name or audit.industry) if part).strip()
    second = " ".join(
        part
        for part in (
            audit.store_name or audit.location,
            audit.industry,
            "네이버 플레이스 인스타그램 블로그",
        )
        if part
    ).strip()
    queries: list[str] = []
    for query in (first, second):
        if query and query not in queries:
            queries.append(query)
    return queries


def summarize_collected_channels(
    sources: list[dict[str, Any]], queries: list[str], *, demo: bool
) -> str:
    """자동 수집 결과를 진단 입력으로 쓸 수 있는 한 문단 요약으로 만든다."""
    if not sources:
        return ""
    citation = build_citation_analysis(sources)
    parts = [
        f"공개 웹 검색({', '.join(queries)})으로 출처 {citation['total']}건을 수집했습니다."
    ]
    cited = [item for item in citation["channels"] if item["count"]]
    if cited:
        parts.append(
            "채널 분포: " + ", ".join(f"{item['label']} {item['count']}건" for item in cited) + "."
        )
        domains = [domain for item in cited for domain in item["domains"]][:6]
        if domains:
            parts.append("주요 도메인: " + ", ".join(dict.fromkeys(domains)) + ".")
    titles = [str(source.get("title") or "") for source in sources[:5]]
    titles = [title for title in titles if title]
    if titles:
        parts.append("주요 노출: " + " / ".join(titles) + ".")
    if demo:
        parts.append("(데모 모드: 가상 상권 검색 결과로 구성한 요약입니다.)")
    return " ".join(parts)


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
    '  "recommended_channels": [\n'
    '    {"rank": 1, "channel": "naver_blog|naver_place|instagram_sns|official_web|review_media",'
    ' "label": "채널 한글명", "reason": "인용 비중과 상권 수요를 근거로 왜 이 채널부터 채워야 하는지",'
    ' "priority_badge": "1순위"}\n'
    "  ],\n"
    '  "contents": [\n'
    '    {"channel": "instagram", "title": "카드뉴스 제목", "summary": "기획 의도",'
    ' "body": "1장부터 5장까지 장별 카피를 포함한 완성 원고", "hashtags": ["#태그"], "caution": "주의 문구 또는 빈 문자열"},\n'
    '    {"channel": "naver_place", "title": "새소식 제목", "summary": "기획 의도", "body": "완성 원고", "hashtags": [], "caution": ""},\n'
    '    {"channel": "blog_faq", "title": "FAQ 제목", "summary": "기획 의도", "body": "Q/A 3개 이상 완성 원고", "hashtags": [], "caution": ""}\n'
    "  ]\n"
    "}\n\n"
    "규칙: gaps는 정확히 3개, recommended_channels는 정확히 3개(인용 비중이 높은 채널부터 rank 1~3), "
    "contents는 instagram·naver_place·blog_faq 각 1개. "
    "recommended_channels의 reason에는 반드시 제공된 인용 비중 수치를 포함한다. "
    "인용 비중은 Liner Search·Liner Search Agent가 상권 질문에 실제로 인용한 출처를 채널별로 집계한 값이며, "
    "GPT·Perplexity·Gemini·Claude 등 다른 AI의 인용을 뜻하지 않는다. "
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
    citation: dict[str, Any] | None = None,
    collected: dict[str, Any] | None = None,
) -> str:
    source_lines = "\n".join(
        f"- {src['title']} | {src['url']}" for src in sources[:12]
    ) or "- (수집된 출처 없음)"
    citation = citation or {}
    cited = [item for item in citation.get("channels", []) if item.get("count")]
    citation_lines = "\n".join(
        f"- {item['label']} {item['share_pct']}% ({item['count']}건, {', '.join(item['domains']) or '도메인 미상'})"
        for item in cited
    ) or "- (인용 비중 계산 불가)"
    collected = collected or {}
    collected_block = (
        f"[{collected.get('label', '직접 입력')}] {collected.get('summary', '')}"
        if collected.get("summary")
        else "(수집된 공개 채널 정보 없음)"
    )
    return (
        f"[매장] {audit.display_name}\n"
        f"[상권 위치] {audit.location}\n"
        f"[업종] {audit.industry}\n"
        f"[상권 검색 쿼리] {', '.join(queries)}\n\n"
        f"[상권 고객 수요 요약]\n{demand_answer}\n\n"
        f"[Liner 인용 출처 비중 — Liner Search · Liner Search Agent {citation.get('total', 0)}건]\n{citation_lines}\n\n"
        f"[매장 공개 채널 현황]\n{collected_block}\n\n"
        f"[수집된 출처]\n{source_lines}\n\n"
        f"[현재 외부 채널 텍스트]\n{channel_text[:6000] or '(직접 입력 없음 — 공개 채널 자동 수집 결과로 대신합니다)'}\n\n"
        "위 정보를 근거로 결핍 진단, 채널 우선순위 추천(recommended_channels), 채널별 보완 콘텐츠를 JSON으로 작성해라."
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


def _flatten_demo_sources(demo_payload: dict[str, Any] | None) -> list[dict[str, Any]]:
    """데모 픽스처의 상권 검색 결과를 그대로 자동 수집 결과로 재사용한다."""
    sources: list[dict[str, Any]] = []
    for bundle in (demo_payload or {}).get("search", {}).values():
        if isinstance(bundle, list):
            sources.extend(bundle)
    return sources


async def run_audit(audit: AuditInput, emit: Emit | None = None) -> dict[str, Any]:
    """3단계 진단을 실행하고 UI가 그대로 렌더링할 수 있는 결과 dict를 돌려준다."""
    started = time.perf_counter()
    if not audit.location and not audit.industry:
        raise ValueError("상권 위치와 업종을 입력해 주세요.")

    client = LinerClient(api_key=audit.api_key)
    live = client.live
    using_demo = not live
    degraded = False
    warnings: list[str] = []

    manual_text = audit.channel_text.strip()
    collect_queries = (
        build_collection_queries(audit)
        if audit.auto_collect and len(manual_text) < MIN_CHANNEL_TEXT_CHARS
        else []
    )

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

    # ---------------- Step 1 : Liner Search (+ 공개 채널 자동 수집) ----------------
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

    # 채널 텍스트가 비어 있거나 짧으면 매장 공개 채널 노출 현황을 스스로 검색해 모은다.
    collected_raw: list[dict[str, Any]] = []
    if collect_queries:
        await _emit(emit, type="step", index=1, status="running", title=STEP_TITLES[0],
                    detail=f"채널 텍스트가 비어 있어 공개 채널을 자동 수집합니다(쿼리 {len(collect_queries)}개).")
        for query in collect_queries:
            results = []
            if using_demo:
                demo_results = (demo_payload or {}).get("search", {}).get(query) or _flatten_demo_sources(demo_payload)
                results = [s for s in (_normalize_source(r) for r in demo_results) if s]
                client.record("search.web", mode="demo", status="ok", latency_ms=0,
                              detail=f"collect query={query}")
            else:
                try:
                    response = await client.search(
                        query, max_results=min(audit.max_results, 6), detail=f"collect query={query}"
                    )
                except LinerAPIError as exc:
                    warnings.append(
                        f"공개 채널 자동 수집 검색이 실패했습니다('{query}'): {exc}"
                    )
                else:
                    results = [
                        s
                        for s in (
                            _normalize_source({**r, "origin": "collect"})
                            for r in response.get("results", [])
                        )
                        if s
                    ]
            for source in results:
                source["origin"] = "collect"
            collected_raw.extend(results)

    collected_sources: list[dict[str, Any]] = []
    collected_seen: set[str] = set()
    for source in collected_raw:
        if source["url"] in collected_seen:
            continue
        collected_seen.add(source["url"])
        collected_sources.append(source)

    collected_summary = summarize_collected_channels(
        collected_sources, collect_queries, demo=using_demo
    )
    if collect_queries and not collected_sources:
        warnings.append("공개 채널 자동 수집 결과가 비어 있습니다. 채널 텍스트를 직접 입력하면 진단 정확도가 올라갑니다.")

    collected_mode = "manual" if not collect_queries else ("hybrid" if manual_text else "auto")
    collected_breakdown = [
        {"channel": item["channel"], "label": item["label"], "count": item["count"]}
        for item in build_citation_analysis(collected_sources)["channels"]
        if item["count"]
    ]
    collected_channel: dict[str, Any] = {
        "mode": collected_mode,
        "label": COLLECTED_MODE_LABELS[collected_mode],
        "auto": bool(collect_queries),
        "input_chars": len(manual_text),
        "source_count": len(collected_sources),
        "queries": collect_queries,
        "breakdown": collected_breakdown,
        "sources": collected_sources[:8],
        "summary": collected_summary
        or (
            f"직접 입력한 채널 텍스트 {len(manual_text):,}자로 진단했습니다."
            if manual_text
            else "수집된 공개 채널 정보가 없습니다. 상권·업종 검색 결과만으로 진단했습니다."
        ),
    }
    if collected_mode == "hybrid":
        collected_channel["summary"] = (
            f"직접 입력 {len(manual_text):,}자에 공개 웹 자동 수집 {len(collected_sources)}건을 더해 진단했습니다. "
            + collected_summary
        )

    diagnosis_text = "\n\n".join(
        part
        for part in (
            manual_text,
            f"[공개 웹 자동 수집 채널 현황]\n{collected_summary}" if collected_summary else "",
        )
        if part
    )

    if using_demo:
        await _demo_pause()
    step1_ms = int((time.perf_counter() - step_started) * 1000)
    step1_detail = f"쿼리 {len(queries)}개 · 출처 {len(search_sources)}건 수집"
    if collect_queries:
        step1_detail += f" · 공개 채널 자동 수집 {len(collected_sources)}건"
    steps.append({
        "index": 1,
        "title": STEP_TITLES[0],
        "status": "done",
        "latency_ms": step1_ms,
        "detail": step1_detail,
        "queries": queries,
        "collected_queries": collect_queries,
        "counts": {
            "queries": len(queries),
            "sources": len(search_sources),
            "collected": len(collected_sources),
        },
    })
    await _emit(emit, type="step", index=1, status="done", title=STEP_TITLES[0],
                detail=f"출처 {len(search_sources)}건 수집 완료" + (
                    f" · 자동 수집 {len(collected_sources)}건" if collect_queries else ""
                ),
                latency_ms=step1_ms)

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
                    f"현재 채널 요약: {diagnosis_text[:1200] or '(직접 입력 없음 — 공개 채널 자동 수집 결과 참고)'}"
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

    # ---------------- 인용 출처 분석 ----------------
    all_sources: list[dict[str, Any]] = []
    seen: set[str] = set()
    for source in search_sources + agent_references + collected_sources:
        if source["url"] in seen:
            continue
        seen.add(source["url"])
        all_sources.append(source)
    known_urls = {s["url"]: s for s in all_sources}
    citation_analysis = build_citation_analysis(all_sources)

    # ---------------- Step 3 : Liner Router ----------------
    step_started = time.perf_counter()
    await _emit(emit, type="step", index=3, status="running", title=STEP_TITLES[2],
                detail="채널 텍스트와 상권 수요·인용 비중을 대조해 결핍과 처방을 생성합니다.")

    analysis: dict[str, Any] = {}
    if using_demo:
        analysis = dict((demo_payload or {}).get("analysis", {}))
        client.record("chat.completions", mode="demo", status="ok", latency_ms=0, detail=f"model={DEFAULT_MODEL}")
    else:
        messages = [
            {"role": "system", "content": ROUTER_SYSTEM_PROMPT},
            {
                "role": "user",
                "content": _router_user_prompt(
                    audit,
                    diagnosis_text,
                    agent_answer,
                    all_sources,
                    queries,
                    citation_analysis,
                    collected_channel,
                ),
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
    recommendations = _normalize_recommendations(analysis.get("recommended_channels"), citation_analysis)
    apply_content_priority(contents, recommendations)
    for gap in gaps:
        if not gap["sources"]:
            gap["sources"] = all_sources[:2]
            gap["source_urls"] = [s["url"] for s in gap["sources"]]

    if len(gaps) != 3:
        warnings.append(f"결핍이 3개가 아닌 {len(gaps)}개로 반환되었습니다.")
    if len(contents) != 3:
        warnings.append(f"콘텐츠가 3종이 아닌 {len(contents)}종으로 반환되었습니다.")
    if not citation_analysis["total"]:
        warnings.append("분석할 출처가 없어 인용 비중을 계산하지 못했습니다.")
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
        "detail": (
            f"결핍 {len(gaps)}건 · 콘텐츠 {len(contents)}종 · 추천 {len(recommendations)}채널 · 종합 {overall}점"
        ),
        "counts": {
            "gaps": len(gaps),
            "contents": len(contents),
            "recommendations": len(recommendations),
        },
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
            "channel_text_chars": len(manual_text),
            "auto_collect": audit.auto_collect,
            "api_key_source": client.key_source,
        },
        "queries": queries,
        "collected_channel": collected_channel,
        "citation_analysis": citation_analysis,
        "recommended_channels": recommendations,
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
