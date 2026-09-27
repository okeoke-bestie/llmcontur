"""Ядро: поиск кандидатов -> LLM -> валидация -> решение (автозакрытие / эскалация / ручной режим)."""
from __future__ import annotations

import asyncio
import json
import logging
import random
import time
from dataclasses import dataclass, field
from datetime import datetime, timezone

from pydantic import ValidationError

from .catalog import Catalog
from .config import Settings
from .guards import risk_flags
from .llm import LLMClient, ProviderError, ProviderUnavailable
from .prompts import PROMPT_VERSION, build_messages
from .retrieval import HybridRetriever
from .schemas import Category, Decision, TicketIn, TriageLLMOutput, TriageResult

log = logging.getLogger("triage")


@dataclass
class TriageTrace:
    """Всё, что нужно для разбора инцидента «почему клиенту пришла ерунда»."""
    messages: list[dict] = field(default_factory=list)
    candidates: list[str] = field(default_factory=list)
    raw_response: str | None = None
    attempts: int = 0
    error: str | None = None
    latency_ms: int = 0


class TriageService:
    def __init__(self, settings: Settings, catalog: Catalog, llm: LLMClient):
        self.s = settings
        self.catalog = catalog
        self.retriever = HybridRetriever(catalog)
        self.llm = llm

    async def _call_with_retries(self, messages, ticket_text, candidates, trace: TriageTrace) -> str:
        last: Exception | None = None
        for attempt in range(1, self.s.max_retries + 1):
            trace.attempts = attempt
            try:
                return await self.llm.complete(messages, ticket_text=ticket_text, candidates=candidates)
            except ProviderUnavailable as e:
                last = e
                if attempt < self.s.max_retries:
                    delay = self.s.backoff_base_s * (2 ** (attempt - 1)) * (1 + random.random() * 0.2)
                    log.warning("LLM unavailable (%s), retry %d in %.2fs", e, attempt, delay)
                    await asyncio.sleep(delay)
        raise ProviderUnavailable(f"exhausted {self.s.max_retries} retries: {last}")

    async def process(self, ticket: TicketIn) -> tuple[TriageResult, TriageTrace]:
        trace = TriageTrace()
        t0 = time.perf_counter()
        text = ticket.text
        candidates = self.retriever.search(text, top_k=self.s.retrieval_top_k)
        trace.candidates = [c.instruction.id for c in candidates]
        messages = build_messages(text, candidates)
        trace.messages = messages

        def result(decision: Decision, reason: str, out: TriageLLMOutput | None = None,
                   reply: str | None = None) -> TriageResult:
            return TriageResult(
                ticket_id=ticket.ticket_id,
                decision=decision,
                category=out.category if out else Category.unknown,
                confidence=out.confidence if out else 0.0,
                technical_summary=out.technical_summary if out else "Автоматическая обработка не выполнена; "
                                                                   "исходный текст заявки без изменений.",
                instruction_id=out.instruction_id if out and decision == Decision.auto_resolved else None,
                reply_to_user=reply,
                reason=reason,
                prompt_version=PROMPT_VERSION,
                model=getattr(self.llm, "model", "unknown"),
                processed_at=datetime.now(timezone.utc),
            )

        # 1. Вызов модели. Недоступность провайдера -> деградация в ручной режим, поддержка не встаёт.
        try:
            raw = await self._call_with_retries(messages, text, candidates, trace)
            trace.raw_response = raw
        except (ProviderUnavailable, ProviderError) as e:
            trace.error = str(e)
            trace.latency_ms = int((time.perf_counter() - t0) * 1000)
            return result(Decision.manual_fallback, f"provider_error: {e}"), trace

        # 2. Строгая валидация по схеме.
        try:
            out = TriageLLMOutput.model_validate(json.loads(raw))
        except (json.JSONDecodeError, ValidationError, TypeError) as e:
            trace.error = f"invalid_output: {e}"
            trace.latency_ms = int((time.perf_counter() - t0) * 1000)
            return result(Decision.manual_fallback, "invalid_model_output"), trace

        trace.latency_ms = int((time.perf_counter() - t0) * 1000)

        # 3. Защита от «галлюцинированных» инструкций: id должен быть среди кандидатов и в каталоге,
        #    а категория инструкции — совпадать с категорией модели.
        instr = self.catalog.get(out.instruction_id) if out.instruction_id else None
        if out.instruction_id and (instr is None or out.instruction_id not in trace.candidates):
            return result(Decision.escalated, "instruction_not_in_candidates", out), trace
        if instr and instr.category != out.category.value:
            return result(Decision.escalated, "category_mismatch", out), trace

        # 4. Жёсткие правила риска: инциденты, деньги, безопасность, интеграции — только человеку.
        if flags := risk_flags(text):
            return result(Decision.escalated, "risk_guard:" + ",".join(flags), out), trace

        # 5. Решение по порогу. Даже при эскалации инженер получает переформулировку.
        if out.category == Category.unknown:
            return result(Decision.escalated, "unknown_category", out), trace
        if instr is None:
            return result(Decision.escalated, "no_instruction_selected", out), trace
        if out.confidence < self.s.confidence_threshold:
            return result(Decision.escalated, f"low_confidence<{self.s.confidence_threshold}", out), trace

        return result(Decision.auto_resolved, "confident_match", out, reply=instr.render_reply()), trace
