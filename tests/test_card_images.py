"""이미지 생성의 중복 과금·응답 경계·작업 수명 검증. 외부 API는 호출하지 않는다."""

from __future__ import annotations

import asyncio
import base64
import json
import time
from uuid import uuid1, uuid4

import httpx
import pytest
from pydantic import ValidationError

from app import card_images as images

SECRET = "synthetic-image-private-key"
PNG = "iVBORw0KGgoAAAANSUhEUgAAAAEAAAABCAQAAAC1HAwCAAAAC0lEQVR42mP8/x8AAwMCAO+/mWQAAAAASUVORK5CYII="


def payload(**changes):
    return images.CardImageRequest(request_id=changes.pop("request_id", str(uuid4())),
                                   prompt=changes.pop("prompt", "라볶이 연출 사진. 왼쪽 위 여백."), **changes)


def response_data(**changes):
    return {"data": [{"b64_json": PNG, "media_type": "image/png"}], "usage": {"cost": 0.0184}, **changes}


@pytest.fixture(autouse=True)
def isolated_jobs(monkeypatch):
    assert not images._tasks
    images._jobs.clear()
    monkeypatch.setenv("LINER_API_KEY", SECRET)
    yield
    assert not images._tasks
    images._jobs.clear()


def transport(monkeypatch, handler):
    original = images.LinerClient

    def build(api_key=None):
        return original(api_key=api_key, transport=httpx.MockTransport(handler))

    monkeypatch.setattr(images, "LinerClient", build)


async def finish():
    tasks = tuple(images._tasks)
    if tasks:
        await asyncio.gather(*tasks)
    await asyncio.sleep(0)


@pytest.mark.parametrize("changes", [
    {"request_id": str(uuid1())}, {"request_id": "not-a-uuid"}, {"request_id": uuid4()},
    {"prompt": " "}, {"prompt": 123}, {"prompt": "x" * 1801},
    {"api_key": {"value": SECRET}}, {"api_key": "x" * 4097}, {"model": "other-model"},
])
def test_request_is_strict_and_requires_uuid4(changes):
    with pytest.raises(ValidationError):
        payload(**changes)


def test_request_repr_and_serialization_exclude_key():
    request = payload(api_key=SECRET)
    assert SECRET not in repr(request)
    assert SECRET not in request.model_dump_json()


def test_lifecycle_deduplicates_pending_complete_and_uses_fixed_options(monkeypatch):
    requests = []

    async def handler(request):
        requests.append(request)
        await asyncio.sleep(0)
        return httpx.Response(200, json=response_data())

    transport(monkeypatch, handler)

    async def run():
        request = payload()
        first, second = await asyncio.gather(images.start_card_image(request), images.start_card_image(request))
        assert first == second == {"request_id": request.request_id, "status": "pending"}
        await finish()
        result = await images.get_card_image(request.request_id)
        assert result["status"] == "complete"
        assert result == await images.start_card_image(request)
        assert result["image"] == {
            "b64_json": PNG, "media_type": "image/png", "model": images.IMAGE_MODEL,
            "cost_usd": 0.0184, "provenance": "ai_concept",
        }
        result["image"]["b64_json"] = "mutated copy"
        assert (await images.get_card_image(request.request_id))["image"]["b64_json"] == PNG
        assert len(requests) == 1
        body = json.loads(requests[0].content)
        assert body["model"] == images.IMAGE_MODEL
        assert body["n"] == 1 and body["resolution"] == "1K" and body["aspect_ratio"] == "4:5"
        assert "실제 매장을 촬영한 사진이 아니다" in body["prompt"]
        assert "사진 안에는 글자" in body["prompt"]
        assert requests[0].headers["x-api-key"] == SECRET
        assert requests[0].extensions["timeout"]["read"] == 450
        assert requests[0].url.path.endswith("/images/generations")
        assert SECRET not in json.dumps(result)
        assert "prompt" not in result

    asyncio.run(run())


@pytest.mark.parametrize("change", [{"prompt": "다른 장면"}, {"api_key": "different-private-key"}])
def test_same_id_with_different_input_is_conflict(monkeypatch, change):
    transport(monkeypatch, lambda _: httpx.Response(200, json=response_data()))

    async def run():
        request = payload()
        await images.start_card_image(request)
        await finish()
        with pytest.raises(images.ImageJobError) as exc:
            await images.start_card_image(payload(request_id=request.request_id, **change))
        assert exc.value.status == 409
        assert SECRET not in str(exc.value)

    asyncio.run(run())


def test_concurrency_limit_keeps_three_jobs_and_allows_duplicate(monkeypatch):
    async def run():
        gate = asyncio.Event()
        requests = []

        async def handler(request):
            requests.append(request)
            await gate.wait()
            return httpx.Response(200, json=response_data())

        transport(monkeypatch, handler)
        inputs = [payload() for _ in range(4)]
        results = await asyncio.gather(*(images.start_card_image(item) for item in inputs), return_exceptions=True)
        assert sum(isinstance(result, dict) for result in results) == 3
        assert isinstance(results[3], images.ImageJobError) and results[3].status == 429
        assert (await images.start_card_image(inputs[0]))["status"] == "pending"
        gate.set()
        await finish()
        assert len(requests) == 3

    asyncio.run(run())


@pytest.mark.parametrize("data", [
    {"error": {"message": SECRET}}, response_data(error=None), [], {},
    response_data(data=[]), response_data(data=[{"url": "https://example.com/image.png"}]),
    response_data(data=[{"b64_json": "invalid*!", "media_type": "image/png"}]),
    response_data(data=[{"b64_json": base64.b64encode(b"<svg>bad</svg>").decode(), "media_type": "image/svg+xml"}]),
    response_data(data=[{"b64_json": PNG, "media_type": "image/jpeg"}]),
    response_data(data=[{"b64_json": base64.b64encode(b"\x89PNG\r\n\x1a\n" + SECRET.encode()).decode(), "media_type": "image/png"}]),
])
def test_invalid_200_response_never_exposes_error_or_retries(monkeypatch, data):
    calls = []

    def handler(request):
        calls.append(request)
        return httpx.Response(200, json=data)

    transport(monkeypatch, handler)

    async def run():
        request = payload()
        await images.start_card_image(request)
        await finish()
        result = await images.get_card_image(request.request_id)
        assert result["status"] == "error"
        assert "image" not in result
        assert SECRET not in json.dumps(result)
        assert result == await images.start_card_image(request)
        assert len(calls) == 1

    asyncio.run(run())


class Chunks(httpx.AsyncByteStream):
    def __init__(self, chunks):
        self.chunks = chunks

    async def __aiter__(self):
        for chunk in self.chunks:
            yield chunk


def test_heartbeat_spaces_before_json_are_supported(monkeypatch):
    transport(monkeypatch, lambda _: httpx.Response(200, stream=Chunks([
        b" ", b"  ", json.dumps(response_data()).encode(),
    ])))

    async def run():
        request = payload()
        await images.start_card_image(request)
        await finish()
        assert (await images.get_card_image(request.request_id))["status"] == "complete"

    asyncio.run(run())


@pytest.mark.parametrize("mode", ["declared", "streamed", "decoded"])
def test_oversized_responses_and_images_are_rejected(monkeypatch, mode):
    if mode == "decoded":
        monkeypatch.setattr(images, "MAX_IMAGE_BYTES", 20)
        handler = lambda _: httpx.Response(200, json=response_data())
    else:
        monkeypatch.setattr(images, "MAX_RESPONSE_BYTES", 64)
        headers = {"content-length": "65"} if mode == "declared" else {}
        handler = lambda _: httpx.Response(200, headers=headers, stream=Chunks([b" " * 65]))
    transport(monkeypatch, handler)

    async def run():
        request = payload()
        await images.start_card_image(request)
        await finish()
        result = await images.get_card_image(request.request_id)
        assert result["status"] == "error" and "image" not in result

    asyncio.run(run())


@pytest.mark.parametrize("status", [302, 400, 401, 402, 429, 503])
def test_http_failures_do_not_follow_redirects_or_expose_upstream_text(monkeypatch, status):
    calls = []

    def handler(request):
        calls.append(request)
        return httpx.Response(status, json={"error": SECRET}, headers={"location": "https://untrusted.example/"})

    transport(monkeypatch, handler)

    async def run():
        request = payload()
        await images.start_card_image(request)
        await finish()
        result = await images.get_card_image(request.request_id)
        assert result["status"] == "error"
        assert SECRET not in json.dumps(result)
        assert len(calls) == 1

    asyncio.run(run())


@pytest.mark.parametrize("error", [httpx.ReadTimeout, httpx.ConnectError, RuntimeError])
def test_transport_and_unexpected_errors_are_safe(monkeypatch, error):
    def handler(_request):
        raise error(SECRET)

    transport(monkeypatch, handler)

    async def run():
        request = payload()
        await images.start_card_image(request)
        await finish()
        result = await images.get_card_image(request.request_id)
        assert result["status"] == "error"
        assert SECRET not in json.dumps(result)

    asyncio.run(run())


def test_absolute_deadline_finishes_error_even_with_heartbeat_connection(monkeypatch):
    monkeypatch.setattr(images, "JOB_TIMEOUT_SECONDS", 0.01)

    async def handler(_request):
        await asyncio.Event().wait()

    transport(monkeypatch, handler)

    async def run():
        request = payload()
        await images.start_card_image(request)
        await finish()
        result = await images.get_card_image(request.request_id)
        assert result["status"] == "error"
        assert "대기 시간" in result["detail"]
        assert "비용" in result["detail"]

    asyncio.run(run())


@pytest.mark.parametrize("started", [False, True])
def test_shutdown_releases_pending_job_without_retry(monkeypatch, started):
    async def handler(_request):
        await asyncio.Event().wait()

    transport(monkeypatch, handler)

    async def run():
        request = payload()
        await images.start_card_image(request)
        if started:
            await asyncio.sleep(0)
        await images.shutdown_card_images()
        result = await images.get_card_image(request.request_id)
        assert result["status"] == "error"
        assert result == await images.start_card_image(request)
        assert not images._tasks

    asyncio.run(run())


@pytest.mark.parametrize("mode", ["ttl", "count", "bytes"])
def test_expired_or_evicted_job_is_tombstoned_and_never_regenerated(monkeypatch, mode):
    calls = []

    def handler(request):
        calls.append(request)
        return httpx.Response(200, json=response_data())

    transport(monkeypatch, handler)
    if mode == "count":
        monkeypatch.setattr(images, "MAX_TERMINAL_JOBS", 1)
    elif mode == "bytes":
        monkeypatch.setattr(images, "MAX_CACHED_IMAGE_BYTES", len(PNG))

    async def run():
        request = payload()
        await images.start_card_image(request)
        await finish()
        if mode == "ttl":
            images._jobs[request.request_id].finished_at = time.monotonic() - images.TERMINAL_TTL_SECONDS - 1
        else:
            await images.start_card_image(payload())
            await finish()
        call_count = len(calls)
        with pytest.raises(images.ImageJobError) as exc:
            await images.get_card_image(request.request_id)
        assert exc.value.status == 410
        with pytest.raises(images.ImageJobError) as exc:
            await images.start_card_image(request)
        assert exc.value.status == 410
        assert len(calls) == call_count
        assert images._jobs[request.request_id].image is None

    asyncio.run(run())


def test_ledger_bound_refuses_new_id_instead_of_forgetting_billed_id(monkeypatch):
    monkeypatch.setattr(images, "MAX_JOB_IDS", 1)
    transport(monkeypatch, lambda _: httpx.Response(200, json=response_data()))

    async def run():
        request = payload()
        await images.start_card_image(request)
        await finish()
        with pytest.raises(images.ImageJobError) as exc:
            await images.start_card_image(payload())
        assert exc.value.status == 429
        assert (await images.start_card_image(request))["status"] == "complete"

    asyncio.run(run())


def test_missing_key_and_unknown_job_have_clear_status(monkeypatch):
    monkeypatch.setenv("LINER_API_KEY", "")

    async def run():
        with pytest.raises(images.ImageJobError) as exc:
            await images.start_card_image(payload())
        assert exc.value.status == 400
        assert "API 키" in str(exc.value)
        with pytest.raises(images.ImageJobError) as exc:
            await images.get_card_image(str(uuid4()))
        assert exc.value.status == 404
        with pytest.raises(images.ImageJobError) as exc:
            await images.get_card_image(str(uuid1()))
        assert exc.value.status == 422
        assert not images._jobs

    asyncio.run(run())
