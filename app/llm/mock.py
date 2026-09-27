"""Офлайн-заглушка LLM: детерминированная эвристика поверх результатов поиска.

Нужна, чтобы приложение, тесты и eval работали без ключа и без сети. Возвращает JSON
в том же формате, что и настоящая модель, поэтому проходит тот же путь валидации.
"""
from __future__ import annotations

import json
import re

from ..guards import risk_flags
from ..retrieval import Candidate, tokenize
_CODES = re.compile(r"\b(?:[45]\d\d|0x[0-9a-f]+|v?\d+\.\d+(?:\.\d+)?)\b", re.IGNORECASE)
_PLATFORM = re.compile(r"(android|iphone|ios|windows|mac(?:os)?|chrome|firefox|safari|браузер|телефон)",
                       re.IGNORECASE)

_CAT_HUMAN = {
    "auth_session": "проблема с сессией/авторизацией",
    "permissions": "проблема с правами доступа",
    "cache": "устаревшие данные / кэш клиента",
    "client_config": "проблема конфигурации клиента",
    "update_error": "ошибка при обновлении клиента",
    "unknown": "нетиповая проблема",
}


class MockLLM:
    model = "mock-heuristic-1"

    async def complete(self, messages: list[dict], *, ticket_text: str, candidates: list[Candidate]) -> str:
        details = []
        if codes := _CODES.findall(ticket_text):
            details.append("коды/версии: " + ", ".join(dict.fromkeys(codes)))
        if plats := _PLATFORM.findall(ticket_text):
            details.append("платформа: " + ", ".join(dict.fromkeys(p.lower() for p in plats)))

        if risk_flags(ticket_text) or not candidates:
            return json.dumps({
                "category": "unknown", "confidence": 0.3, "instruction_id": None,
                "technical_summary": self._summary("unknown", ticket_text, details, incident=True),
            }, ensure_ascii=False)

        top = candidates[0]
        second_bm = candidates[1].bm25 if len(candidates) > 1 else 0.0
        margin = (top.bm25 - second_bm) / (top.bm25 + 1e-9) if top.bm25 > 0 else 0.0
        # покрытие: какая доля значимых слов заявки встречается в описании инструкции
        q = set(tokenize(ticket_text))
        coverage = len(q & set(tokenize(top.instruction.search_text))) / max(len(q), 1)
        conf = min(0.97, 0.25 + 0.55 * coverage + 0.2 * margin + 0.2 * top.cosine)
        category = top.instruction.category if (top.bm25 > 1.0 and coverage >= 0.25) else "unknown"
        instruction_id = top.instruction.id if category != "unknown" else None
        if category == "unknown":
            conf = min(conf, 0.4)
        return json.dumps({
            "category": category, "confidence": round(conf, 3), "instruction_id": instruction_id,
            "technical_summary": self._summary(category, ticket_text, details),
        }, ensure_ascii=False)

    @staticmethod
    def _summary(category: str, text: str, details: list[str], incident: bool = False) -> str:
        first = re.split(r"(?<=[.!?])\s+|\n", text.strip(), maxsplit=1)[0].strip()[:160]
        parts = [f"{_CAT_HUMAN[category].capitalize()}."]
        if incident:
            parts.append("Есть признаки инцидента/массового сбоя — нужна проверка инженером.")
        parts.append(f"Со слов пользователя: «{first}».")
        if details:
            parts.append("; ".join(details).capitalize() + ".")
        return " ".join(parts)[:600]
