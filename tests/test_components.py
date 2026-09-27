import httpx
import pytest

from app.llm import MistralClient, ProviderError, ProviderUnavailable
from app.metrics import compute
from app.retrieval import HybridRetriever


def test_retrieval_finds_right_instruction(catalog):
    r = HybridRetriever(catalog)
    assert r.search("пропал доступ к разделу, ошибка 403")[0].instruction.id == "KB-003"
    assert r.search("не приходят пуш уведомления")[0].instruction.id == "KB-008"
    assert r.search("крашится после апдейта")[0].instruction.id == "KB-010"


def _mistral(handler):
    return MistralClient("test-key", "https://api.mistral.ai/v1", "mistral-small-latest",
                         transport=httpx.MockTransport(handler))


@pytest.mark.asyncio
async def test_mistral_ok_and_request_shape():
    seen = {}

    def handler(req: httpx.Request):
        seen["auth"] = req.headers["authorization"]
        seen["body"] = req.read().decode()
        return httpx.Response(200, json={"choices": [{"message": {"content": '{"a": 1}'}}]})

    c = _mistral(handler)
    assert await c.complete([{"role": "user", "content": "x"}], ticket_text="x", candidates=[]) == '{"a": 1}'
    assert seen["auth"] == "Bearer test-key" and '"json_object"' in seen["body"]
    await c.aclose()


@pytest.mark.asyncio
@pytest.mark.parametrize("code,exc", [(503, ProviderUnavailable), (429, ProviderUnavailable),
                                      (401, ProviderError), (400, ProviderError)])
async def test_mistral_error_mapping(code, exc):
    c = _mistral(lambda req: httpx.Response(code, text="err"))
    with pytest.raises(exc):
        await c.complete([], ticket_text="", candidates=[])
    await c.aclose()


def test_return_rate_counts_repeat_in_same_category():
    rows = [
        {"ticket_id": "a", "user_id": "u1", "created_at": "2026-01-01T10:00:00+00:00", "decision": "auto_resolved",
         "category": "cache", "processed_at": "2026-01-01T10:00:05+00:00",
         "first_response_at": "2026-01-01T10:00:05+00:00"},
        {"ticket_id": "b", "user_id": "u1", "created_at": "2026-01-02T10:00:00+00:00", "decision": "escalated",
         "category": "cache", "processed_at": "2026-01-02T10:00:05+00:00", "first_response_at": None},
        {"ticket_id": "c", "user_id": "u2", "created_at": "2026-01-01T10:00:00+00:00", "decision": "auto_resolved",
         "category": "cache", "processed_at": "2026-01-01T10:00:03+00:00",
         "first_response_at": "2026-01-01T10:00:03+00:00"},
    ]
    m = compute(rows, return_window_hours=72)
    assert m["auto_resolution_rate"] == round(2 / 3, 3)
    assert m["return_rate"] == 0.5
    assert m["net_auto_resolution_rate"] == round(1 / 3, 3)
