from __future__ import annotations

from typing import Protocol

from ..retrieval import Candidate


class ProviderUnavailable(Exception):
    """Временная ошибка провайдера (таймаут, 429, 5xx) — имеет смысл ретраить."""


class ProviderError(Exception):
    """Постоянная ошибка (400, 401, битый ответ) — ретраить бессмысленно."""


class LLMClient(Protocol):
    model: str

    async def complete(self, messages: list[dict], *, ticket_text: str, candidates: list[Candidate]) -> str:
        """Возвращает сырой JSON-текст ответа модели."""
        ...
