"""합성 반응의 출처 연결, 실제 관측과의 분리, 중복 과금 방지·취소 회귀 검사."""
import asyncio
import copy
import json

import pytest
from fastapi import HTTPException

from app import scenarios as module
from app.liner_client import LinerAPIError, LinerClient


@pytest.fixture(autouse=True)
def empty_cache():
    module._REPORTS.clear()
    yield
    module._REPORTS.clear()


def report(*, scope="all_available"):
    return {"period": {"scope": scope}, "stores": [
        {"id": "target", "name": "모먼트", "summary": {"documents": 1, "positive": 1}, "evidence": [
            {"id": "E1", "store_id": "target", "source_id": "target-S1", "quote": "직접 먹어본 떡볶이는 맛있고 푸짐했다.",
             "url": "https://example.com/review", "date": None, "period_status": "unknown",
             "voice": "customer_experience", "sentiment": "positive", "aspect": "quality"},
        ]},
        {"id": "competitor-1", "name": "다른 가게", "evidence": [
            {"id": "E2", "store_id": "competitor-1", "quote": "경쟁점의 자료는 이 실험에 넣지 않는다."},
        ]},
    ]}


def raw_reaction(persona=None, **changes):
    persona = persona or module._DATA["personas"][0]
    result = {"persona_id": persona["id"], "sentiment": "positive",
              "reason": "이 취향이라면 메뉴에 관심을 가질 수 있어요.", "question": "메뉴 구성을 확인해 볼까요?",
              "preference_quote": module._preferences(persona)[:30], "evidence_ids": ["E1"]}
    result.update(changes)
    return result


class ConnectedRequest:
    async def is_disconnected(self):
        return False


def token_and_snapshot(**kwargs):
    token = module.remember_report(report(**kwargs))
    return token, module._REPORTS[token]


def test_cache_is_bounded_expiring_and_keeps_no_key_or_rival(monkeypatch):
    clock = [100.0]
    monkeypatch.setattr(module.time, "monotonic", lambda: clock[0])
    data = report()
    data["api_key"] = "never-store-this"
    data["input"] = {"api_key": "never-store-this"}
    initial = module.remember_report(data)
    snapshot = module._REPORTS[initial]
    assert len(initial) == 32
    assert snapshot.store == {"name": "모먼트"}
    assert [item["id"] for item in snapshot.evidence] == ["E1"]
    assert "never-store-this" not in repr(snapshot)
    for _ in range(module.MAX_REPORTS):
        module.remember_report(data)
    assert len(module._REPORTS) == module.MAX_REPORTS and initial not in module._REPORTS
    clock[0] += module.REPORT_TTL_SECONDS
    module._prune()
    assert not module._REPORTS


def test_cache_eligibility_uses_scope_and_copies_only_bounded_target_evidence():
    original = report()
    before = copy.deepcopy(original)
    token = module.remember_report(original)
    original["stores"][0]["evidence"][0]["quote"] = "mutated"
    assert module._REPORTS[token].evidence[0]["quote"] == before["stores"][0]["evidence"][0]["quote"]
    _, bounded = token_and_snapshot(scope="dated_period")
    assert not bounded.evidence
    data = report()
    one = data["stores"][0]["evidence"][0]
    data["stores"][0]["evidence"] = [{**one, "id": f"E{index}"} for index in range(60)]
    token = module.remember_report(data)
    assert len(module._REPORTS[token].evidence) == module.MAX_EVIDENCE


def test_prompt_does_not_pass_demographic_fields_or_synthetic_names():
    _, snapshot = token_and_snapshot()
    payload = json.loads(module.reaction_prompt(snapshot))
    assert all(set(p) == {"persona_id", "preference"} for p in payload["personas"])
    assert all("씨는" not in p["preference"] for p in payload["personas"])
    assert "경쟁점의 자료" not in json.dumps(payload, ensure_ascii=False)
    assert payload["store"] == {"name": "모먼트"}


@pytest.mark.parametrize("changes", [
    {"evidence_ids": ["E2"]}, {"evidence_ids": []}, {"sentiment": "verified"},
    {"sentiment": []}, {"preference_quote": "실제 취향 원문에 없는 문장"},
    {"preference_quote": "짧음"}, {"persona_id": "not-a-persona"},
    {"evidence_ids": [{}]}, {"question": ""},
])
def test_invalid_reactions_become_unknown_and_do_not_include_invented_evidence(changes):
    _, snapshot = token_and_snapshot()
    result = module._validated_result({"reactions": [raw_reaction(**changes)]}, snapshot)
    assert result["summary"] == {"personas": 10, "positive": 0, "negative": 0, "mixed": 0, "unknown": 10}
    assert all(not p["evidence"] for p in result["personas"])
    assert all(cell["count"] == cell["unknown"] == 1 for cell in result["cells"])


def test_missing_or_duplicate_persona_is_unknown_and_measured_report_is_unmodified():
    source = report()
    before = copy.deepcopy(source)
    token = module.remember_report(source)
    snapshot = module._REPORTS[token]
    result = module._validated_result({"reactions": [raw_reaction()]}, snapshot)
    assert source == before
    assert result["status"] == "synthetic_simulation"
    assert result["summary"]["positive"] == 1 and result["summary"]["unknown"] == 9
    assert result["personas"][0]["evidence"][0]["id"] == "E1"
    assert len(result["cells"]) == 10 and all(cell["count"] == 1 for cell in result["cells"])
    assert result["source"]["license"] == "CC BY 4.0"
    duplicated = module._validated_result({"reactions": [raw_reaction(), raw_reaction()]}, snapshot)
    assert duplicated["summary"]["unknown"] == 10


def test_completed_result_is_reused_and_response_never_echoes_key(monkeypatch):
    calls = []
    secret = "scenario-secret-not-persisted"

    class FakeClient(LinerClient):
        async def chat_completions(self, messages, **kwargs):
            calls.append(messages)
            assert kwargs["json_object"] is False
            return {"choices": [{"message": {"content": json.dumps({"reactions": [
                raw_reaction(reason=f"이 취향이라면 관심이 생길 수 있어요. {secret}")
            ]})}}]}

    monkeypatch.setattr(module, "LinerClient", FakeClient)
    token, snapshot = token_and_snapshot()

    async def run():
        first = await module.react(module.ScenarioRequest(report_token=token, api_key=secret), ConnectedRequest())
        # 완료 결과는 API 키 없이 다시 받아도 외부 호출을 하지 않는다.
        second = await module.react(module.ScenarioRequest(report_token=token), ConnectedRequest())
        assert first.body == second.body
        assert secret not in first.body.decode()
        assert "[REDACTED]" in first.body.decode()
        assert secret not in repr(snapshot)
        assert first.headers["cache-control"] == "no-store"
    asyncio.run(run())
    assert len(calls) == 1 and snapshot.state == "complete"


def test_expired_empty_and_missing_key_fail_without_model_call():
    async def run():
        for token, status in [("a" * 32, 410), (token_and_snapshot(scope="dated_period")[0], 422),
                              (token_and_snapshot()[0], 422)]:
            with pytest.raises(HTTPException) as error:
                await module.react(module.ScenarioRequest(report_token=token), ConnectedRequest())
            assert error.value.status_code == status
    asyncio.run(run())


def test_concurrent_duplicate_returns_409_and_disconnect_cancels_generation(monkeypatch):
    calls, canceled = [], []
    started, disconnected = asyncio.Event(), asyncio.Event()

    class SlowClient(LinerClient):
        async def chat_completions(self, *args, **kwargs):
            calls.append(1)
            started.set()
            try:
                await asyncio.Event().wait()
            finally:
                canceled.append(1)

    class DisconnectRequest:
        async def is_disconnected(self):
            return disconnected.is_set()

    monkeypatch.setattr(module, "LinerClient", SlowClient)
    token, snapshot = token_and_snapshot()
    payload = module.ScenarioRequest(report_token=token, api_key="test-only")

    async def run():
        task = asyncio.create_task(module.react(payload, DisconnectRequest()))
        await started.wait()
        with pytest.raises(HTTPException) as duplicate:
            await module.react(payload, ConnectedRequest())
        assert duplicate.value.status_code == 409
        disconnected.set()
        with pytest.raises(HTTPException) as stopped:
            await task
        assert stopped.value.status_code == 499
        with pytest.raises(HTTPException) as repeated:
            await module.react(payload, ConnectedRequest())
        assert repeated.value.status_code == 409
        assert not [t for t in asyncio.all_tasks() if t is not asyncio.current_task() and not t.done()]
    asyncio.run(run())
    assert calls == canceled == [1] and snapshot.state == "canceled"


@pytest.mark.parametrize("exception", [LinerAPIError("upstream secret-value"), RuntimeError("upstream secret-value")])
def test_failure_never_retries_and_does_not_expose_error_body(monkeypatch, exception):
    calls = []

    class FailedClient(LinerClient):
        async def chat_completions(self, *args, **kwargs):
            calls.append(1)
            raise exception

    monkeypatch.setattr(module, "LinerClient", FailedClient)
    token, snapshot = token_and_snapshot()
    payload = module.ScenarioRequest(report_token=token, api_key="test-only")

    async def run():
        with pytest.raises(HTTPException) as error:
            await module.react(payload, ConnectedRequest())
        assert error.value.status_code in {500, 502}
        assert "secret-value" not in error.value.detail
        with pytest.raises(HTTPException) as repeat:
            await module.react(payload, ConnectedRequest())
        assert repeat.value.status_code == 409
    asyncio.run(run())
    assert calls == [1] and snapshot.state == "failed"


def test_timeout_cancels_once_and_never_leaves_pending_model_task(monkeypatch):
    canceled = []

    class SlowClient(LinerClient):
        async def chat_completions(self, *args, **kwargs):
            try:
                await asyncio.Event().wait()
            finally:
                canceled.append(1)

    monkeypatch.setattr(module, "LinerClient", SlowClient)
    monkeypatch.setattr(module, "REACTION_TIMEOUT_SECONDS", 0.01)
    token, snapshot = token_and_snapshot()

    async def run():
        with pytest.raises(HTTPException) as error:
            await module.react(module.ScenarioRequest(report_token=token, api_key="test-only"), ConnectedRequest())
        assert error.value.status_code == 504
        assert not [t for t in asyncio.all_tasks() if t is not asyncio.current_task() and not t.done()]
    asyncio.run(run())
    assert canceled == [1] and snapshot.state == "failed"
