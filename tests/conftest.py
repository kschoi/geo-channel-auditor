"""pytest 공통 설정 — 외부 네트워크·API 키에 의존하지 않도록 환경을 고정한다."""

from __future__ import annotations

import os
import sys
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))


@pytest.fixture(autouse=True)
def clean_env(monkeypatch: pytest.MonkeyPatch) -> None:
    """테스트는 항상 데모 기본값에서 시작한다.

    변수를 지우는 대신 빈 값으로 고정한다. `app.main`을 import할 때
    `load_dotenv(override=False)`가 로컬 `.env`를 다시 읽어 지워진 키를 되살리기 때문에,
    빈 값(=설정됨)으로 두어야 개발 머신에서도 데모 모드가 유지된다.
    """
    monkeypatch.setenv("LINER_API_KEY", "")
    monkeypatch.setenv("DEMO_LATENCY_MS", "0")
    yield


@pytest.fixture()
def client():
    from fastapi.testclient import TestClient

    from app.main import app

    with TestClient(app) as test_client:
        yield test_client


def has_env_key() -> bool:
    return bool(os.environ.get("LINER_API_KEY", "").strip())
