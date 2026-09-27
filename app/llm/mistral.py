"""Клиент Mistral Chat Completions API (https://docs.mistral.ai/api/)."""
from __future__ import annotations

import httpx

from ..retrieval import Candidate
from .base import ProviderError, ProviderUnavailable


class MistralClient:
    def __init__(self, api_key: str, base_url: str, model: str, timeout_s: float = 20.0,
                 transport: httpx.AsyncBaseTransport | None = None):
        if not api_key:
            raise ValueError("MISTRAL_API_KEY не задан")
        self.model = model
        self._client = httpx.AsyncClient(
            base_url=base_url,
            timeout=timeout_s,
            headers={"Authorization": f"Bearer {api_key}", "Content-Type": "application/json"},
            transport=transport,
        )

    async def complete(self, messages: list[dict], *, ticket_text: str, candidates: list[Candidate]) -> str:
        payload = {
            "model": self.model,
            "messages": messages,
            "temperature": 0.0,
            "max_tokens": 400,
            "response_format": {"type": "json_object"},
        }
        try:
            resp = await self._client.post("/chat/completions", json=payload)
        except (httpx.TimeoutException, httpx.TransportError) as e:
            raise ProviderUnavailable(f"transport: {e!r}") from e

        if resp.status_code == 429 or resp.status_code >= 500:
            raise ProviderUnavailable(f"HTTP {resp.status_code}")
        if resp.status_code >= 400:
            raise ProviderError(f"HTTP {resp.status_code}: {resp.text[:300]}")
        try:
            return resp.json()["choices"][0]["message"]["content"]
        except (KeyError, IndexError, ValueError) as e:
            raise ProviderError(f"unexpected response shape: {resp.text[:300]}") from e

    async def aclose(self) -> None:
        await self._client.aclose()
