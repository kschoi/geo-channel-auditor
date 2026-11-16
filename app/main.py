"""FastAPI 엔트리포인트.

실행: `uvicorn app.main:app --reload` (또는 `uv run uvicorn app.main:app`)
"""

from __future__ import annotations

import asyncio
import json
from collections.abc import AsyncIterator
from pathlib import Path
from typing import Any

from fastapi import FastAPI, HTTPException, Request
from fastapi.responses import HTMLResponse, StreamingResponse
from fastapi.staticfiles import StaticFiles
from fastapi.templating import Jinja2Templates
from pydantic import BaseModel, Field

from . import __version__
from . import samples as sample_data
from .auditor import DIMENSION_LABELS, AuditInput, run_audit
from .liner_client import DEFAULT_MODEL, LinerAPIError, LinerClient

BASE_DIR = Path(__file__).resolve().parent
PROJECT_ROOT = BASE_DIR.parent

# .env 가 있으면 읽는다(없으면 환경변수만 사용). 키 자체는 어디에도 출력하지 않는다.
try:
    from dotenv import load_dotenv

    load_dotenv(PROJECT_ROOT / ".env", override=False)
except ImportError:  # pragma: no cover - python-dotenv 미설치 시 환경변수로만 동작
    pass

app = FastAPI(
    title="Geo Channel Auditor",
    description="상권 Geo 분석 기반 외부 채널 결핍 진단 · 콘텐츠 처방 (Liner .EXE-THON)",
    version=__version__,
)
app.mount("/static", StaticFiles(directory=BASE_DIR / "static"), name="static")
templates = Jinja2Templates(directory=str(BASE_DIR / "templates"))


class AuditRequest(BaseModel):
    location: str = Field(default="", description="상권 위치 (예: 서울 성동구 성수동2가)")
    industry: str = Field(default="", description="업종 (예: 스페셜티 카페)")
    store_name: str = Field(default="", description="매장명(선택)")
    channel_text: str = Field(default="", description="현재 외부 채널 텍스트")
    sample_id: str | None = Field(default=None, description="원클릭 샘플 ID")
    api_key: str | None = Field(default=None, repr=False, description="1회성 Liner API 키(저장·로깅하지 않음)")
    max_results: int = Field(default=8, ge=1, le=20)


class HealthResponse(BaseModel):
    status: str
    mode: str
    key_configured: bool
    key_source: str
    model: str
    version: str
    base_url: str


@app.get("/", response_class=HTMLResponse)
async def index(request: Request) -> HTMLResponse:
    client = LinerClient()
    return templates.TemplateResponse(
        request=request,
        name="index.html",
        context={
            "samples": sample_data.SAMPLES,
            "mode": client.mode,
            "version": __version__,
            "model": DEFAULT_MODEL,
            "dimension_labels": DIMENSION_LABELS,
        },
    )


@app.get("/api/health", response_model=HealthResponse)
async def health(probe: bool = False) -> dict[str, Any]:
    """헬스체크. `probe=true`이면 Liner 크레딧 잔액까지 확인한다."""
    client = LinerClient()
    status = "ok"
    if probe and client.live:
        try:
            await client.credits_balance()
        except LinerAPIError:
            status = "degraded"
    return {
        "status": status,
        "mode": client.mode,
        "key_configured": client.live,
        "key_source": client.key_source,
        "model": DEFAULT_MODEL,
        "version": __version__,
        "base_url": client.base_url,
    }


@app.get("/api/samples")
async def samples() -> dict[str, Any]:
    """원클릭 시연용 가상 매장 3종."""
    return {
        "samples": [
            {**sample_data.sample_brief(s), "channels": s["channels"], "queries": s["queries"]}
            for s in sample_data.SAMPLES
        ],
        "notice": "모든 샘플은 가상 매장·가상 채널 텍스트입니다. 실제 고객 데이터를 포함하지 않습니다.",
    }


@app.post("/api/audit")
async def audit(payload: AuditRequest) -> dict[str, Any]:
    """3단계 진단 실행. LINER_API_KEY가 없거나 호출이 실패하면 데모 데이터로 폴백한다."""
    audit_input = AuditInput.from_payload(payload.model_dump())
    try:
        return await run_audit(audit_input)
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc
    except LinerAPIError as exc:  # 폴백이 꺼진 경로에 대한 안전망
        raise HTTPException(status_code=502, detail=str(exc)) from exc


def _sse(event: str, data: dict[str, Any]) -> str:
    return f"event: {event}\ndata: {json.dumps(data, ensure_ascii=False)}\n\n"


@app.post("/api/audit/stream")
async def audit_stream(payload: AuditRequest) -> StreamingResponse:
    """단계별 진행 이벤트를 SSE로 흘려보내는 진단 실행.

    이벤트: `step`(단계 시작/완료) → `result`(최종 결과) 또는 `error`.
    """
    audit_input = AuditInput.from_payload(payload.model_dump())

    async def event_stream() -> AsyncIterator[str]:
        queue: asyncio.Queue[dict[str, Any]] = asyncio.Queue()

        async def emit(event: dict[str, Any]) -> None:
            await queue.put(event)

        task = asyncio.create_task(run_audit(audit_input, emit))
        yield _sse("open", {"mode": "live" if LinerClient().live else "demo"})
        try:
            while True:
                if task.done() and queue.empty():
                    break
                try:
                    event = await asyncio.wait_for(queue.get(), timeout=0.05)
                except asyncio.TimeoutError:
                    continue
                yield _sse("step", event)
            result = task.result()
        except ValueError as exc:
            yield _sse("error", {"detail": str(exc)})
        except LinerAPIError as exc:
            yield _sse("error", {"detail": str(exc)})
        except asyncio.CancelledError:  # 클라이언트가 연결을 끊은 경우
            task.cancel()
            raise
        except Exception as exc:  # noqa: BLE001 - 예기치 못한 오류도 사용자 메시지로 변환
            # pragma: no cover
            yield _sse("error", {"detail": f"진단 중 오류가 발생했습니다: {exc}"})
        else:
            yield _sse("result", result)

    return StreamingResponse(
        event_stream(),
        media_type="text/event-stream",
        headers={"Cache-Control": "no-cache", "X-Accel-Buffering": "no"},
    )
