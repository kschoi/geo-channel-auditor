"""Liner Platform API 클라이언트.

공식 스펙(https://platform.liner.com/api/v1)
-------------------------------------------
* 인증: `x-api-key: <LINER_API_KEY>` 헤더
* 검색:      POST /tools/search/web
* 검색 에이전트: POST /agents/search
* 라우터:    POST /chat/completions  (model=`liner-gpt-router`)
* 크레딧:    GET  /credits/balance

보안 원칙
---------
* API 키는 환경변수 `LINER_API_KEY` 또는 요청 본문의 1회성 값으로만 받는다.
* 키를 코드·로그·응답·디스크에 저장하지 않는다. 텔레메트리에는 엔드포인트/지연/상태만 남긴다.
"""

from __future__ import annotations

import json
import os
import re
import time
from typing import Any

import httpx

DEFAULT_BASE_URL = "https://platform.liner.com/api/v1"
DEFAULT_MODEL = "liner-gpt-router"
DEFAULT_TIMEOUT = 45.0
# Router는 세 채널 원고를 한 번에 생성하므로 검색보다 오래 걸린다(실측 45초 초과).
DEFAULT_ROUTER_TIMEOUT = 120.0

# 텔레메트리에는 경로 대신 짧은 엔드포인트 이름을 쓴다(대시보드·UI 표기 공통).
ENDPOINT_NAMES = {
    "tools/search/web": "search.web",
    "agents/search": "agents.search",
    "chat/completions": "chat.completions",
    "credits/balance": "credits.balance",
}


def endpoint_name(path: str) -> str:
    key = path.strip("/")
    return ENDPOINT_NAMES.get(key, key.replace("/", "."))


class LinerAPIError(RuntimeError):
    """Liner API 호출 실패. 메시지는 사용자에게 보여줄 수 있는 수준으로 유지한다."""

    def __init__(self, message: str, *, endpoint: str = "", status: int | None = None):
        super().__init__(message)
        self.endpoint = endpoint
        self.status = status


def _friendly_status(status: int) -> str:
    if status in (401, 403):
        return "Liner API 키가 유효하지 않거나 권한이 없습니다. LINER_API_KEY를 확인해 주세요."
    if status == 402:
        return "Liner 크레딧이 부족합니다. 대시보드에서 잔액을 확인해 주세요."
    if status == 429:
        return "Liner API 호출 한도를 초과했습니다. 잠시 후 다시 시도해 주세요."
    if 500 <= status:
        return f"Liner API 서버 오류({status})입니다. 잠시 후 다시 시도해 주세요."
    return f"Liner API 요청이 실패했습니다(HTTP {status})."


class LinerClient:
    """Liner API 3종 + 크레딧 조회를 감싼 얇은 클라이언트.

    텔레메트리(`self.calls`)는 API 파이프라인 투명성 패널에 그대로 쓰인다.
    """

    def __init__(
        self,
        api_key: str | None = None,
        *,
        base_url: str | None = None,
        timeout: float | None = None,
        transport: httpx.AsyncBaseTransport | None = None,
    ) -> None:
        key = (api_key or os.environ.get("LINER_API_KEY") or "").strip()
        self._api_key = key  # 절대 로깅/응답에 노출하지 않는다.
        self.base_url = (base_url or os.environ.get("LINER_API_BASE_URL") or DEFAULT_BASE_URL).rstrip("/")
        try:
            self.timeout = float(timeout or os.environ.get("LINER_TIMEOUT_SECONDS") or DEFAULT_TIMEOUT)
        except ValueError:
            self.timeout = DEFAULT_TIMEOUT
        try:
            router_timeout = float(
                os.environ.get("LINER_ROUTER_TIMEOUT_SECONDS") or DEFAULT_ROUTER_TIMEOUT
            )
        except ValueError:
            router_timeout = DEFAULT_ROUTER_TIMEOUT
        # Router 응답이 검색 타임아웃에 걸려 본문 생성이 통째로 폴백되는 일을 막는다.
        self.router_timeout = max(router_timeout, self.timeout)
        self._transport = transport
        self.calls: list[dict[str, Any]] = []

    # ------------------------------------------------------------------ #
    # 상태
    # ------------------------------------------------------------------ #
    @property
    def live(self) -> bool:
        return bool(self._api_key)

    @property
    def mode(self) -> str:
        return "live" if self.live else "demo"

    @property
    def key_source(self) -> str:
        if not self.live:
            return "none"
        return "env:LINER_API_KEY" if os.environ.get("LINER_API_KEY", "").strip() == self._api_key else "request"

    def masked_key(self) -> str:
        """키 존재 여부만 보여주기 위한 마스킹 문자열(끝 4자리도 노출하지 않는다)."""
        return "설정됨" if self.live else "없음"

    # ------------------------------------------------------------------ #
    # 텔레메트리
    # ------------------------------------------------------------------ #
    def record(
        self,
        endpoint: str,
        *,
        mode: str,
        status: str,
        latency_ms: int,
        http_status: int | None = None,
        request_id: str | None = None,
        detail: str | None = None,
    ) -> dict[str, Any]:
        entry = {
            "endpoint": endpoint,
            "mode": mode,
            "status": status,
            "latency_ms": latency_ms,
            "http_status": http_status,
            "request_id": request_id,
            "detail": detail,
        }
        self.calls.append(entry)
        return entry

    def totals(self) -> dict[str, int]:
        counts = {"search.web": 0, "agents.search": 0, "chat.completions": 0, "credits.balance": 0}
        for call in self.calls:
            counts[call["endpoint"]] = counts.get(call["endpoint"], 0) + 1
        counts["total"] = len(self.calls)
        counts["live"] = sum(1 for c in self.calls if c["mode"] == "live")
        counts["demo"] = sum(1 for c in self.calls if c["mode"] == "demo")
        return counts

    # ------------------------------------------------------------------ #
    # 저수준 HTTP
    # ------------------------------------------------------------------ #
    def _headers(self) -> dict[str, str]:
        return {
            "x-api-key": self._api_key,
            "Content-Type": "application/json",
            "Accept": "application/json",
            "User-Agent": "geo-channel-auditor/0.1 (Liner .EXE-THON)",
        }

    async def _request(
        self,
        method: str,
        path: str,
        payload: dict[str, Any] | None = None,
        *,
        detail: str | None = None,
        timeout: float | None = None,
    ) -> dict[str, Any]:
        endpoint = endpoint_name(path)
        url = f"{self.base_url}/{path.lstrip('/')}"
        started = time.perf_counter()
        try:
            async with httpx.AsyncClient(
                timeout=timeout or self.timeout, transport=self._transport
            ) as client:
                response = await client.request(method, url, json=payload, headers=self._headers())
        except httpx.HTTPError as exc:
            elapsed = int((time.perf_counter() - started) * 1000)
            self.record(
                endpoint, mode="live", status="error", latency_ms=elapsed, detail=detail or type(exc).__name__
            )
            raise LinerAPIError(
                "Liner API에 연결하지 못했습니다. 네트워크 상태를 확인해 주세요.", endpoint=endpoint
            ) from exc

        elapsed = int((time.perf_counter() - started) * 1000)
        try:
            data = response.json()
        except ValueError:
            data = {}

        if response.status_code >= 400:
            self.record(
                endpoint,
                mode="live",
                status="error",
                latency_ms=elapsed,
                http_status=response.status_code,
            )
            raise LinerAPIError(_friendly_status(response.status_code), endpoint=endpoint, status=response.status_code)

        if not isinstance(data, dict):
            self.record(
                endpoint, mode="live", status="error", latency_ms=elapsed, http_status=response.status_code
            )
            raise LinerAPIError("Liner API 응답 형식을 해석할 수 없습니다.", endpoint=endpoint)

        self.record(
            endpoint,
            mode="live",
            status="ok",
            latency_ms=elapsed,
            http_status=response.status_code,
            request_id=_request_id_of(data),
            detail=detail,
        )
        return data

    # ------------------------------------------------------------------ #
    # API 3종
    # ------------------------------------------------------------------ #
    async def search(
        self,
        query: str,
        *,
        country_code: str = "kr",
        lang: str = "ko",
        max_results: int = 8,
        include_knowledge: bool = False,
        detail: str | None = None,
    ) -> dict[str, Any]:
        payload = {
            "query": query,
            "country_code": country_code,
            "lang": lang,
            "max_results": max_results,
            "include_knowledge": include_knowledge,
        }
        data = await self._request("POST", "tools/search/web", payload, detail=detail or f"query={query}")
        data.setdefault("results", [])
        data["query"] = query
        return data

    async def search_agent(
        self,
        messages: list[dict[str, str]],
        *,
        lang: str = "ko",
        mode: str = "general",
        stream: bool = False,
    ) -> dict[str, Any]:
        payload = {"messages": messages, "lang": lang, "mode": mode, "stream": stream}
        data = await self._request("POST", "agents/search", payload)
        data.setdefault("answer", "")
        data.setdefault("references", [])
        return data

    async def chat_completions(
        self,
        messages: list[dict[str, str]],
        *,
        model: str = DEFAULT_MODEL,
        routing_strategy: str = "balanced",
        json_object: bool = True,
        temperature: float | None = None,
    ) -> dict[str, Any]:
        payload: dict[str, Any] = {
            "model": model,
            "routing_strategy": routing_strategy,
            "messages": messages,
        }
        if json_object:
            payload["response_format"] = {"type": "json_object"}
        if temperature is not None:
            payload["temperature"] = temperature
        return await self._request("POST", "chat/completions", payload, timeout=self.router_timeout)

    async def credits_balance(self) -> dict[str, Any]:
        return await self._request("GET", "credits/balance")

    # ------------------------------------------------------------------ #
    # 편의: 라우터 JSON 추출
    # ------------------------------------------------------------------ #
    @staticmethod
    def message_content(data: dict[str, Any]) -> str:
        try:
            return data["choices"][0]["message"]["content"] or ""
        except (KeyError, IndexError, TypeError):
            return ""

    @classmethod
    def parse_json_content(cls, data: dict[str, Any]) -> dict[str, Any]:
        """OpenAI 호환 응답에서 JSON 객체를 꺼낸다. 코드펜스가 붙어도 처리한다."""
        content = cls.message_content(data)
        if not content.strip():
            raise LinerAPIError("Liner Router 응답이 비어 있습니다.", endpoint="chat.completions")
        text = content.strip()
        fence = re.match(r"^```(?:json)?\s*(.*?)\s*```$", text, re.DOTALL)
        if fence:
            text = fence.group(1).strip()
        try:
            parsed = json.loads(text)
        except json.JSONDecodeError:
            start, end = text.find("{"), text.rfind("}")
            if start == -1 or end <= start:
                raise LinerAPIError(
                    "Liner Router 응답을 JSON으로 해석할 수 없습니다.", endpoint="chat.completions"
                ) from None
            try:
                parsed = json.loads(text[start : end + 1])
            except json.JSONDecodeError:
                raise LinerAPIError(
                    "Liner Router 응답을 JSON으로 해석할 수 없습니다.", endpoint="chat.completions"
                ) from None
        if not isinstance(parsed, dict):
            raise LinerAPIError("Liner Router 응답이 JSON 객체가 아닙니다.", endpoint="chat.completions")
        return parsed


def _request_id_of(data: dict[str, Any]) -> str | None:
    for key in ("requestId", "request_id", "id"):
        value = data.get(key)
        if isinstance(value, str) and value:
            return value
    return None
