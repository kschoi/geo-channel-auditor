"""이미지 작업 HTTP 경계: 요청 검증·민감정보 숨김·캐시 방지."""
from uuid import uuid4

import pytest

from app.card_images import ImageJobError

SECRET = "synthetic-image-private-key"


def test_image_job_start_and_read_do_not_cache(client, monkeypatch):
    request_id = str(uuid4())

    async def start(payload):
        assert str(payload.request_id) == request_id
        return {"request_id": request_id, "status": "pending"}

    async def read(value):
        assert value == request_id
        return {"request_id": request_id, "status": "complete", "image": {"provenance": "ai_concept"}}

    monkeypatch.setattr("app.main.start_card_image", start)
    monkeypatch.setattr("app.main.get_card_image", read)
    response = client.post("/api/card-images", json={"request_id": request_id, "prompt": "음식 사진"})
    assert response.status_code == 202
    assert response.headers["cache-control"] == "no-store"
    response = client.get(f"/api/card-images/{request_id}")
    assert response.status_code == 200
    assert response.json()["status"] == "complete"
    assert response.headers["cache-control"] == "no-store"


@pytest.mark.parametrize("method", ["POST", "GET"])
def test_image_job_internal_failure_hides_secret(client, monkeypatch, method):
    request_id = str(uuid4())

    async def fail(_value):
        raise RuntimeError(SECRET)

    monkeypatch.setattr("app.main.start_card_image", fail)
    monkeypatch.setattr("app.main.get_card_image", fail)
    response = client.post("/api/card-images", json={"request_id": request_id, "prompt": "사진", "api_key": SECRET}) if method == "POST" else client.get(f"/api/card-images/{request_id}")
    assert response.status_code == 500
    assert SECRET not in response.text


@pytest.mark.parametrize("status", [409, 410, 429])
def test_image_job_known_failure_keeps_status(client, monkeypatch, status):
    async def fail(_value):
        raise ImageJobError("같은 요청의 결과를 확인해 주세요.", status=status)

    monkeypatch.setattr("app.main.start_card_image", fail)
    response = client.post("/api/card-images", json={"request_id": str(uuid4()), "prompt": "사진"})
    assert response.status_code == status


@pytest.mark.parametrize("payload", [
    {"request_id": "invalid", "prompt": "사진", "api_key": SECRET},
    {"request_id": str(uuid4()), "prompt": "사진", "api_key": {"secret": SECRET}},
    {"request_id": str(uuid4()), "prompt": "사진", "api_key": SECRET, "model": "arbitrary-model"},
])
def test_image_job_validation_hides_key_and_rejects_model_override(client, payload):
    response = client.post("/api/card-images", json=payload)
    assert response.status_code == 422
    assert SECRET not in response.text
    assert all("input" not in item for item in response.json()["detail"])
