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
from fastapi.exceptions import RequestValidationError
from fastapi.responses import HTMLResponse, JSONResponse, StreamingResponse
from fastapi.staticfiles import StaticFiles
from fastapi.templating import Jinja2Templates
from pydantic import BaseModel, Field

from . import __version__
from . import samples as sample_data
from .auditor import DIMENSION_LABELS, AuditInput, run_audit
from .card_studio import CardRequest, generate_cards
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


@app.exception_handler(RequestValidationError)
async def validation_error(_request: Request, exc: RequestValidationError) -> JSONResponse:
    """검증 오류에는 요청 원문이나 API 키를 돌려주지 않는다."""
    return JSONResponse(
        status_code=422,
        content={"detail": [
            {"loc": error["loc"], "type": error["type"], "msg": "입력 형식을 확인해 주세요."}
            for error in exc.errors()
        ]},
    )


class AuditRequest(BaseModel):
    query: str = Field(
        default="",
        description="동네와 실제 매장명 (예: 성수동 로우키 카페). 비우면 location·industry·store_name을 직접 받는다.",
    )
    location: str = Field(default="", description="상권 위치 (예: 서울 성동구 성수동2가)")
    industry: str = Field(default="", description="업종 (예: 스페셜티 카페)")
    store_name: str = Field(default="", description="실제 매장명(query에서 찾지 못하면 직접 입력)")
    channel_text: str = Field(
        default="", description="현재 외부 채널 텍스트 (선택 · 비우면 공개 채널 자동 수집)"
    )
    sample_id: str | None = Field(default=None, description="원클릭 샘플 ID")
    api_key: str | None = Field(default=None, repr=False, description="1회성 Liner API 키(저장·로깅하지 않음)")
    max_results: int = Field(default=8, ge=1, le=20)
    auto_collect: bool = Field(default=True, description="channel_text가 비어 있거나 짧으면 공개 채널을 자동 수집")


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
    """실제 매장 검색을 시작하는 예시 3종. 채널 정보는 진단 때 수집한다."""
    return {
        "samples": [
            {**sample_data.sample_brief(s), "channels": s["channels"], "queries": s["queries"]}
            for s in sample_data.SAMPLES
        ],
        "notice": "예시는 실제 매장의 이름과 상권만 담습니다. 채널 정보는 Liner API로 공개 출처를 검색해 수집하며, 확인하지 못한 정보는 만들지 않습니다.",
    }


@app.post("/api/audit")
async def audit(payload: AuditRequest) -> dict[str, Any]:
    """3단계 진단 실행.

    `query` 한 줄만으로도 실행된다(`parse_search_query`가 위치·업종·매장명을 추론).
    `channel_text`는 선택 입력이다. 비우면 매장 공개 채널을 자동 수집해 진단 입력으로 쓴다.
    API 키가 없거나 호출이 실패하면 오류를 알린다. 가상 결과로 대체하지 않는다.
    """
    audit_input = AuditInput.from_payload(payload.model_dump(exclude_unset=True))
    try:
        return await run_audit(audit_input)
    except ValueError:
        raise HTTPException(status_code=400, detail="동네와 실제 매장명을 함께 입력해 주세요. 예: 성수동 로우키 카페") from None
    except LinerAPIError as exc:  # 키 누락·상위 API 실패를 명확히 알린다.
        raise HTTPException(status_code=502, detail=str(exc)) from None
    except Exception:  # noqa: BLE001 - 예외 원문에는 키나 내부 정보가 포함될 수 있다.
        raise HTTPException(status_code=500, detail="진단 중 오류가 발생했습니다. 잠시 후 다시 시도해 주세요.") from None



@app.post("/api/cards")
async def create_cards(payload: CardRequest) -> dict[str, Any]:
    """검색 근거를 바탕으로 편집할 카드뉴스 3장 문구를 생성한다."""
    try:
        return await generate_cards(payload)
    except ValueError:
        raise HTTPException(status_code=400, detail="카드뉴스에 쓸 매장과 검색 자료를 확인해 주세요.") from None
    except LinerAPIError as exc:
        raise HTTPException(status_code=502, detail=str(exc)) from None
    except Exception:  # noqa: BLE001 - 내부 예외에 포함될 수 있는 민감정보를 숨긴다.
        raise HTTPException(status_code=500, detail="카드뉴스를 만들지 못했어요. 잠시 후 다시 시도해 주세요.") from None


def _sse(event: str, data: dict[str, Any]) -> str:
    return f"event: {event}\ndata: {json.dumps(data, ensure_ascii=False)}\n\n"


@app.post("/api/audit/stream")
async def audit_stream(payload: AuditRequest) -> StreamingResponse:
    """단계별 진행 이벤트를 SSE로 흘려보내는 진단 실행.

    이벤트: `step`(단계 시작/완료) → `result`(최종 결과) 또는 `error`.
    """
    audit_input = AuditInput.from_payload(payload.model_dump(exclude_unset=True))

    async def event_stream() -> AsyncIterator[str]:
        queue: asyncio.Queue[dict[str, Any]] = asyncio.Queue()

        async def emit(event: dict[str, Any]) -> None:
            await queue.put(event)

        task = asyncio.create_task(run_audit(audit_input, emit))
        try:
            yield _sse("open", {"mode": LinerClient(api_key=audit_input.api_key).mode})
            while True:
                if task.done() and queue.empty():
                    break
                try:
                    event = await asyncio.wait_for(queue.get(), timeout=0.05)
                except asyncio.TimeoutError:
                    continue
                yield _sse("step", event)
            result = task.result()
        except ValueError:
            yield _sse("error", {"detail": "동네와 실제 매장명을 함께 입력해 주세요. 예: 성수동 로우키 카페"})
        except LinerAPIError as exc:
            yield _sse("error", {"detail": str(exc)})
        except asyncio.CancelledError:  # 클라이언트가 연결을 끊은 경우
            task.cancel()
            raise
        except Exception:  # noqa: BLE001 - 예외 원문을 외부에 노출하지 않는다.
            yield _sse("error", {"detail": "진단 중 오류가 발생했습니다. 잠시 후 다시 시도해 주세요."})
        else:
            yield _sse("result", result)
        finally:
            if not task.done():
                task.cancel()
            await asyncio.gather(task, return_exceptions=True)

    return StreamingResponse(
        event_stream(),
        media_type="text/event-stream",
        headers={"Cache-Control": "no-cache", "X-Accel-Buffering": "no"},
    )
