"""Схемы данных. Ответ модели строго валидируется по TriageLLMOutput."""
from __future__ import annotations

from datetime import datetime
from enum import Enum
from typing import Optional

from pydantic import BaseModel, Field, field_validator


class Category(str, Enum):
    auth_session = "auth_session"
    permissions = "permissions"
    cache = "cache"
    client_config = "client_config"
    update_error = "update_error"
    unknown = "unknown"


class Decision(str, Enum):
    auto_resolved = "auto_resolved"      # пользователю ушла инструкция из каталога
    escalated = "escalated"              # ушло инженеру с переформулировкой
    manual_fallback = "manual_fallback"  # провайдер недоступен / невалидный ответ -> ручной режим


class TicketIn(BaseModel):
    """То, что присылает хелпдеск вебхуком."""
    ticket_id: str = Field(min_length=1, max_length=64)
    user_id: str = Field(min_length=1, max_length=64)
    subject: str = Field(default="", max_length=300)
    body: str = Field(min_length=1, max_length=8000)
    created_at: Optional[datetime] = None

    @property
    def text(self) -> str:
        return f"{self.subject}\n{self.body}".strip()


class TriageLLMOutput(BaseModel):
    """Контракт ответа модели. Всё, что не проходит валидацию, считается отказом модели."""
    category: Category
    confidence: float = Field(ge=0.0, le=1.0)
    technical_summary: str = Field(min_length=5, max_length=600)
    instruction_id: Optional[str] = None

    @field_validator("instruction_id")
    @classmethod
    def _empty_to_none(cls, v: Optional[str]) -> Optional[str]:
        if v is None:
            return None
        v = v.strip()
        return v or None


class TriageResult(BaseModel):
    ticket_id: str
    decision: Decision
    category: Category
    confidence: float
    technical_summary: str
    instruction_id: Optional[str] = None
    reply_to_user: Optional[str] = None
    reason: str
    prompt_version: str
    model: str
    processed_at: datetime
