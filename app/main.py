"""FastAPI 엔트리포인트.

실행: `uvicorn app.main:app --reload` (또는 `uv run uvicorn app.main:app`)
"""

from __future__ import annotations

import asyncio
import json
from collections.abc import AsyncIterator
from contextlib import asynccontextmanager
from pathlib import Path
from typing import Any

from fastapi import FastAPI, HTTPException, Request
from fastapi.exceptions import RequestValidationError
from fastapi.responses import HTMLResponse, JSONResponse, StreamingResponse
from fastapi.staticfiles import StaticFiles
from fastapi.templating import Jinja2Templates
from pydantic import UUID4, BaseModel, Field

from . import __version__
from . import samples as sample_data
from .auditor import DIMENSION_LABELS, AuditInput, run_audit
from .card_images import CardImageRequest, ImageJobError, get_card_image, shutdown_card_images, start_card_image
from .card_studio import CardRequest, generate_cards
from .liner_client import DEFAULT_MODEL, LinerAPIError, LinerClient
from .perception import PerceptionRequest, run_perception

BASE_DIR = Path(__file__).resolve().parent
PROJECT_ROOT = BASE_DIR.parent

# .env 가 있으면 읽는다(없으면 환경변수만 사용). 키 자체는 어디에도 출력하지 않는다.
try:
    from dotenv import load_dotenv

    load_dotenv(PROJECT_ROOT / ".env", override=False)
except ImportError:  # pragma: no cover - python-dotenv 미설치 시 환경변수로만 동작
    pass

@asynccontextmanager
async def lifespan(_app: FastAPI) -> AsyncIterator[None]:
    try:
        yield
    finally:
        await shutdown_card_images()


app = FastAPI(
    title="Geo Channel Auditor",
    description="공개 후기·웹문서와 상권 자료로 살펴보는 고객 인식과 경쟁점 비교",
    version=__version__,
    lifespan=lifespan,
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


@app.get("/source-audit", response_class=HTMLResponse)
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



@app.post("/api/card-images", status_code=202)
async def create_card_image(payload: CardImageRequest) -> JSONResponse:
    """사용자가 요청한 이미지 한 장을 시작한다. 같은 요청 ID로는 다시 생성하지 않는다."""
    try:
        result = await start_card_image(payload)
    except ImageJobError as exc:
        raise HTTPException(status_code=exc.status, detail=str(exc)) from None
    except Exception:  # noqa: BLE001 - 인증 정보와 상위 응답을 외부에 노출하지 않는다.
        raise HTTPException(status_code=500, detail="이미지 요청을 확인하지 못했어요. 같은 요청의 결과를 다시 확인해 주세요.") from None
    return JSONResponse(status_code=202, content=result, headers={"Cache-Control": "no-store"})


@app.get("/api/card-images/{request_id}")
async def card_image_result(request_id: UUID4) -> JSONResponse:
    """추측하기 어려운 요청 ID로 진행 상태와 결과만 확인한다. 생성 API를 다시 호출하지 않는다."""
    try:
        result = await get_card_image(str(request_id))
    except ImageJobError as exc:
        raise HTTPException(status_code=exc.status, detail=str(exc)) from None
    except Exception:  # noqa: BLE001
        raise HTTPException(status_code=500, detail="이미지 결과를 확인하지 못했어요. 잠시 후 같은 결과를 다시 확인해 주세요.") from None
    return JSONResponse(content=result, headers={"Cache-Control": "no-store"})


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


@app.get("/", response_class=HTMLResponse)
async def perception_home(request: Request) -> HTMLResponse:
    return templates.TemplateResponse(
        request=request, name="perception.html",
        context={"version": __version__, "key_configured": LinerClient().live, "mode": LinerClient().mode,
                 "samples": [{"query": "산본 디델리 분식", "label": "산본 디델리"},
                             {"query": "성수동 로우키 카페", "label": "성수 로우키"},
                             {"query": "홍대 테이스티버거", "label": "홍대 테이스티버거"}]},
    )


@app.post("/api/perception")
async def perception(payload: PerceptionRequest, request: Request) -> JSONResponse:
    async def disconnected() -> None:
        while not await request.is_disconnected():
            await asyncio.sleep(0.1)

    task = asyncio.create_task(asyncio.wait_for(run_perception(payload), timeout=360))
    watcher = asyncio.create_task(disconnected())
    try:
        done, _ = await asyncio.wait({task, watcher}, return_when=asyncio.FIRST_COMPLETED)
        if task not in done:
            raise HTTPException(status_code=499, detail="연결이 끝나 분석을 중단했어요.")
        result = task.result()
        return JSONResponse(result, headers={"Cache-Control": "no-store"})
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from None
    except LinerAPIError as exc:
        raise HTTPException(status_code=502, detail=str(exc)) from None
    except asyncio.TimeoutError:
        raise HTTPException(status_code=504, detail="분석 시간이 길어져 중단했어요. 잠시 후 다시 시도해 주세요.") from None
    except HTTPException:
        raise
    except Exception:  # noqa: BLE001
        raise HTTPException(status_code=500, detail="분석을 마치지 못했어요. 잠시 후 다시 시도해 주세요.") from None
    finally:
        for pending in (task, watcher):
            if not pending.done():
                pending.cancel()
        await asyncio.gather(task, watcher, return_exceptions=True)


@app.post("/api/perception/stream")
async def perception_stream(payload: PerceptionRequest) -> StreamingResponse:
    async def events() -> AsyncIterator[str]:
        queue: asyncio.Queue[dict[str, Any]] = asyncio.Queue()

        async def emit(event: dict[str, Any]) -> None:
            await queue.put(event)

        task = asyncio.create_task(asyncio.wait_for(run_perception(payload, emit), timeout=360))
        try:
            yield _sse("open", {"message": "공개 자료 검색을 시작해요"})
            while not task.done() or not queue.empty():
                try:
                    event = await asyncio.wait_for(queue.get(), timeout=1)
                except asyncio.TimeoutError:
                    yield ": heartbeat\n\n"
                    continue
                yield _sse("step", event)
            yield _sse("result", task.result())
        except (ValueError, LinerAPIError) as exc:
            yield _sse("error", {"detail": str(exc)})
        except asyncio.TimeoutError:
            yield _sse("error", {"detail": "분석 시간이 길어져 중단했어요. 잠시 후 다시 시도해 주세요."})
        except asyncio.CancelledError:
            raise
        except Exception:  # noqa: BLE001
            yield _sse("error", {"detail": "분석을 마치지 못했어요. 잠시 후 다시 시도해 주세요."})
        finally:
            if not task.done():
                task.cancel()
            await asyncio.gather(task, return_exceptions=True)

    return StreamingResponse(events(), media_type="text/event-stream",
                             headers={"Cache-Control": "no-store", "X-Accel-Buffering": "no"})
