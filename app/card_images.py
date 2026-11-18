"""Liner 이미지 생성 작업. 프로세스 안에서 중복 요청·메모리 사용을 제한한다."""

from __future__ import annotations

import asyncio
import base64
import binascii
import hashlib
import hmac
import json
import math
import secrets
import threading
import time
from dataclasses import dataclass, field
from typing import Any
from uuid import UUID

import httpx
from pydantic import BaseModel, ConfigDict, Field, field_validator

from .liner_client import LinerClient

IMAGE_MODEL = "qwen/qwen-image-2.1-turbo"
MAX_ACTIVE_JOBS = 3
MAX_TERMINAL_JOBS = 6
MAX_JOB_IDS = 4096
TERMINAL_TTL_SECONDS = 20 * 60
MAX_RESPONSE_BYTES = 20 * 1024 * 1024
MAX_IMAGE_BYTES = 15 * 1024 * 1024
MAX_CACHED_IMAGE_BYTES = 24 * 1024 * 1024
READ_TIMEOUT_SECONDS = 450.0
JOB_TIMEOUT_SECONDS = 460.0

VISUAL_INSTRUCTION = """[반드시 지킬 이미지 제작 조건]
4:5 세로 화면을 채우는 사진 스타일의 AI 연출 콘셉트를 만든다.
잡지의 음식·라이프스타일 화보처럼 자연스러운 빛과 질감, 절제된 색을 사용한다.
콜라주·테두리·단색 정보 박스·원형 장식·과한 광택·CGI 느낌을 피한다.
실제 매장을 촬영한 사진이 아니다. 실제 매장의 음식·공간·외관 모습이라고 주장하지 않는다.
사진 안에는 글자, 숫자, 로고, 간판 문구, 가격표, 워터마크를 넣지 않는다.
한글 제목과 설명은 편집기에서 나중에 얹는다. 요청한 구도에 맞춰 글자를 얹을 여백을 남긴다.
"""


class ImageJobError(RuntimeError):
    """본문·키가 포함되지 않은 공개 오류. 웹 계층에서 status를 HTTP 코드로 사용한다."""

    def __init__(self, message: str, status: int = 502):
        super().__init__(message)
        self.status = status


def _request_id(value: str) -> str:
    try:
        parsed = UUID(value)
    except (ValueError, TypeError, AttributeError):
        raise ValueError("이미지 요청 번호는 UUID4 형식이어야 해요.") from None
    if parsed.version != 4 or str(parsed) != value.lower():
        raise ValueError("이미지 요청 번호는 UUID4 형식이어야 해요.")
    return str(parsed)


class CardImageRequest(BaseModel):
    model_config = ConfigDict(extra="forbid", str_strip_whitespace=True)

    request_id: str = Field(strict=True, min_length=36, max_length=36)
    prompt: str = Field(strict=True, min_length=1, max_length=1800)
    api_key: str | None = Field(default=None, strict=True, max_length=4096, repr=False, exclude=True)

    @field_validator("request_id")
    @classmethod
    def valid_request_id(cls, value: str) -> str:
        return _request_id(value)


@dataclass
class _Job:
    request_id: str
    fingerprint: bytes = field(repr=False)
    status: str = "pending"
    image: dict[str, Any] | None = field(default=None, repr=False)
    detail: str | None = None
    finished_at: float | None = None
    forgotten: bool = False


# 원문 프롬프트·키는 작업 기록에 저장하지 않는다. 실행 중인 coroutine만 일시적으로 보유한다.
_jobs: dict[str, _Job] = {}
_tasks: set[asyncio.Task] = set()
_state_lock = threading.Lock()
_fingerprint_salt = secrets.token_bytes(32)


def _fingerprint(prompt: str, api_key: str) -> bytes:
    data = json.dumps([prompt, api_key], ensure_ascii=False, separators=(",", ":")).encode()
    return hmac.digest(_fingerprint_salt, data, hashlib.sha256)


def _forget(job: _Job) -> None:
    # 작은 tombstone은 남겨 둔다. 결과가 사라져도 같은 ID로 과금 호출을 다시 하지 않는다.
    job.image = None
    job.detail = None
    job.forgotten = True


def _prune(now: float) -> None:
    terminal = sorted(
        (job for job in _jobs.values() if job.finished_at is not None and not job.forgotten),
        key=lambda job: job.finished_at,
    )
    for job in terminal:
        if now - job.finished_at >= TERMINAL_TTL_SECONDS:
            _forget(job)
    terminal = [job for job in terminal if not job.forgotten]
    cached_bytes = sum(len(job.image["b64_json"]) for job in terminal if job.image)
    while terminal and (len(terminal) > MAX_TERMINAL_JOBS or cached_bytes > MAX_CACHED_IMAGE_BYTES):
        oldest = terminal.pop(0)
        if oldest.image:
            cached_bytes -= len(oldest.image["b64_json"])
        _forget(oldest)


def _public(job: _Job) -> dict[str, Any]:
    if job.forgotten:
        raise ImageJobError("이미지 보관 시간이 끝났거나 최근 결과에서 제외됐어요. 같은 요청은 다시 실행하지 않아요.", 410)
    result: dict[str, Any] = {"request_id": job.request_id, "status": job.status}
    if job.image is not None:
        result["image"] = dict(job.image)
    if job.detail is not None:
        result["detail"] = job.detail
    return result


def _status_error(status: int) -> ImageJobError:
    if status in (401, 403):
        return ImageJobError("Liner API 키가 유효하지 않거나 이미지 생성 권한이 없어요.")
    if status == 402:
        return ImageJobError("Liner 크레딧이 부족해 이미지를 만들지 못했어요.")
    if status == 429:
        return ImageJobError("Liner 이미지 생성 한도에 도달했어요. 자동으로 다시 요청하지 않아요.", 429)
    return ImageJobError("이미지 생성 요청이 처리되지 않았어요. API 설정과 요청 내용을 확인해 주세요.")


def _image_result(data: Any, client: LinerClient) -> dict[str, Any]:
    invalid = "이미지 응답 형식이 올바르지 않아요. 자동으로 다시 요청하지 않아요."
    if not isinstance(data, dict) or "error" in data:
        raise ImageJobError("이미지 생성에 실패했어요. 자동으로 다시 요청하지 않아요.")
    images = data.get("data")
    if not isinstance(images, list) or len(images) != 1 or not isinstance(images[0], dict):
        raise ImageJobError(invalid)
    encoded = images[0].get("b64_json")
    if not isinstance(encoded, str) or not encoded or len(encoded) > 4 * ((MAX_IMAGE_BYTES + 2) // 3):
        raise ImageJobError(invalid)
    if client._api_key and client._api_key in encoded:
        raise ImageJobError(invalid)
    try:
        decoded = base64.b64decode(encoded, validate=True)
    except (ValueError, binascii.Error):
        raise ImageJobError(invalid) from None
    if not decoded or len(decoded) > MAX_IMAGE_BYTES:
        raise ImageJobError("생성된 이미지가 크기 제한을 넘었어요. 자동으로 다시 요청하지 않아요.")
    if client._api_key and client._api_key.encode() in decoded:
        raise ImageJobError(invalid)
    if decoded.startswith(b"\x89PNG\r\n\x1a\n"):
        media_type = "image/png"
    elif decoded.startswith(b"\xff\xd8\xff"):
        media_type = "image/jpeg"
    elif len(decoded) >= 12 and decoded[:4] == b"RIFF" and decoded[8:12] == b"WEBP":
        media_type = "image/webp"
    else:
        raise ImageJobError(invalid)
    declared = images[0].get("media_type")
    if declared is not None and declared != media_type:
        raise ImageJobError(invalid)
    usage = data.get("usage")
    cost = usage.get("cost") if isinstance(usage, dict) else None
    if isinstance(cost, bool) or not isinstance(cost, (int, float)) or not math.isfinite(cost) or cost < 0:
        cost = None
    return {
        "b64_json": encoded, "media_type": media_type, "model": IMAGE_MODEL,
        "cost_usd": cost, "provenance": "ai_concept",
    }


async def _generate(client: LinerClient, prompt: str) -> dict[str, Any]:
    timeout = httpx.Timeout(READ_TIMEOUT_SECONDS, connect=15.0, write=30.0, pool=15.0)
    payload = {
        "model": IMAGE_MODEL, "prompt": prompt + "\n\n" + VISUAL_INSTRUCTION,
        "n": 1, "resolution": "1K", "aspect_ratio": "4:5",
    }
    async with (
        httpx.AsyncClient(timeout=timeout, transport=client._transport, follow_redirects=False) as http,
        http.stream("POST", f"{client.base_url}/images/generations",
                    json=payload, headers=client._headers()) as response,
    ):
        if response.status_code != 200:
            raise _status_error(response.status_code)
        length = response.headers.get("content-length", "")
        if length.isdecimal() and int(length) > MAX_RESPONSE_BYTES:
            raise ImageJobError("이미지 응답이 크기 제한을 넘었어요. 자동으로 다시 요청하지 않아요.")
        body = bytearray()
        async for chunk in response.aiter_bytes(chunk_size=64 * 1024):
            if len(body) + len(chunk) > MAX_RESPONSE_BYTES:
                raise ImageJobError("이미지 응답이 크기 제한을 넘었어요. 자동으로 다시 요청하지 않아요.")
            body.extend(chunk)
    try:
        data = json.loads(body)
    except (ValueError, UnicodeError):
        raise ImageJobError("이미지 응답을 읽지 못했어요. 자동으로 다시 요청하지 않아요.") from None
    return _image_result(data, client)


async def _run_job(job: _Job, client: LinerClient, prompt: str) -> None:
    image = None
    detail = None
    try:
        image = await asyncio.wait_for(_generate(client, prompt), timeout=JOB_TIMEOUT_SECONDS)
    except (asyncio.TimeoutError, httpx.TimeoutException):
        detail = "이미지 응답 대기 시간이 지났어요. 생성 비용이 발생할 수 있어 자동으로 다시 요청하지 않아요."
    except asyncio.CancelledError:
        detail = "이미지 응답 확인이 중단됐어요. 생성 비용이 발생할 수 있어 자동으로 다시 요청하지 않아요."
    except ImageJobError as exc:
        detail = str(exc)
    except httpx.HTTPError:
        detail = "이미지 API 연결이 끊겼어요. 생성 비용이 발생할 수 있어 자동으로 다시 요청하지 않아요."
    except Exception:  # noqa: BLE001 - 작업 예외 원문에 키가 섞여 로그·응답으로 새는 것을 막는다.
        detail = "이미지 결과를 처리하지 못했어요. 자동으로 다시 요청하지 않아요."
    with _state_lock:
        job.image = image
        job.detail = detail
        job.status = "complete" if image is not None else "error"
        job.finished_at = time.monotonic()
        _prune(job.finished_at)


def _job_done(task: asyncio.Task, job: _Job) -> None:
    # 실행 첫 줄에 도달하기 전에 취소돼도 pending 슬롯을 영구 점유하지 않는다.
    if not task.cancelled():
        task.exception()  # 예상하지 못한 예외가 이벤트 루프 로그에 원문으로 남지 않게 회수한다.
    with _state_lock:
        _tasks.discard(task)
        if job.status == "pending":
            job.status = "error"
            job.detail = "이미지 응답 확인이 중단됐어요. 자동으로 다시 요청하지 않아요."
            job.finished_at = time.monotonic()
            _prune(job.finished_at)


async def start_card_image(payload: CardImageRequest) -> dict[str, Any]:
    """즉시 pending을 반환한다. 같은 ID는 완료·실패·만료 후에도 다시 과금 호출하지 않는다."""
    client = LinerClient(api_key=payload.api_key)
    if not client.live:
        raise ImageJobError("이미지를 만들려면 Liner API 키가 필요해요. API 설정에서 키를 입력해 주세요.", 400)
    digest = _fingerprint(payload.prompt, client._api_key)
    with _state_lock:
        _prune(time.monotonic())
        existing = _jobs.get(payload.request_id)
        if existing is not None:
            if not hmac.compare_digest(existing.fingerprint, digest):
                raise ImageJobError("같은 이미지 요청 번호에 다른 내용이나 API 키를 사용할 수 없어요.", 409)
            return _public(existing)
        if sum(job.status == "pending" for job in _jobs.values()) >= MAX_ACTIVE_JOBS:
            raise ImageJobError("이미지 3장을 만들고 있어요. 완료된 뒤 새로 요청해 주세요.", 429)
        if len(_jobs) >= MAX_JOB_IDS:
            raise ImageJobError("이미지 요청 보관 한도에 도달했어요. 서버 관리자에게 알려 주세요.", 429)
        job = _Job(request_id=payload.request_id, fingerprint=digest)
        _jobs[job.request_id] = job
        task = asyncio.create_task(_run_job(job, client, payload.prompt))
        _tasks.add(task)
        task.add_done_callback(lambda done: _job_done(done, job))
        return _public(job)


async def get_card_image(request_id: str) -> dict[str, Any]:
    """추측하기 어려운 요청 ID만으로 조회한다. 키나 입력 프롬프트는 반환하지 않는다."""
    try:
        request_id = _request_id(request_id)
    except ValueError:
        raise ImageJobError("이미지 요청 번호를 확인해 주세요.", 422) from None
    with _state_lock:
        _prune(time.monotonic())
        job = _jobs.get(request_id)
        if job is None:
            raise ImageJobError("이미지 요청을 찾지 못했어요. 서버가 재시작됐을 수 있어요.", 404)
        return _public(job)


async def shutdown_card_images() -> None:
    """서버 종료 시 작업 참조를 정리한다. 연결 종료는 공급자 작업·과금 취소가 아니다."""
    tasks = tuple(_tasks)
    for task in tasks:
        task.cancel()
    if tasks:
        await asyncio.gather(*tasks, return_exceptions=True)
