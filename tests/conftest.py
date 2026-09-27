import dataclasses
import json

import pytest

from app.catalog import Catalog
from app.config import Settings
from app.llm import ProviderUnavailable


@pytest.fixture
def settings(tmp_path):
    return dataclasses.replace(Settings(), db_path=str(tmp_path / "t.db"), llm_provider="mock",
                               backoff_base_s=0.001, max_retries=3, webhook_token="")


@pytest.fixture
def catalog(settings):
    return Catalog.load(settings.catalog_path)


class FakeLLM:
    """Возвращает заранее заданные ответы; строка — JSON, исключение — будет выброшено."""
    model = "fake"

    def __init__(self, *responses):
        self.responses = list(responses)
        self.calls = 0

    async def complete(self, messages, *, ticket_text, candidates):
        self.calls += 1
        r = self.responses.pop(0) if len(self.responses) > 1 else self.responses[0]
        if isinstance(r, Exception):
            raise r
        if isinstance(r, dict):
            return json.dumps(r, ensure_ascii=False)
        return r


def ok(category="cache", instruction_id="KB-005", confidence=0.9):
    return {"category": category, "confidence": confidence, "instruction_id": instruction_id,
            "technical_summary": "Клиент показывает устаревшие данные после обновления."}


DOWN = ProviderUnavailable("HTTP 503")
