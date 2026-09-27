"""Настройки приложения. Всё берётся из переменных окружения (см. .env.example)."""
from __future__ import annotations

import os
from dataclasses import dataclass, field
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent


def _load_dotenv(path: Path) -> None:
    """Минимальная загрузка .env без внешних зависимостей. Не перетирает уже заданные переменные."""
    if not path.exists():
        return
    for line in path.read_text(encoding="utf-8").splitlines():
        line = line.strip()
        if not line or line.startswith("#") or "=" not in line:
            continue
        key, value = line.split("=", 1)
        os.environ.setdefault(key.strip(), value.strip().strip('"').strip("'"))


_load_dotenv(ROOT / ".env")


def _env(name: str, default: str) -> str:
    return os.environ.get(name, default)


@dataclass(frozen=True)
class Settings:
    # LLM-провайдер: "mistral" — реальный API, "mock" — офлайн-эвристика (для разработки, тестов, CI).
    # "auto" — mistral, если задан MISTRAL_API_KEY, иначе mock.
    llm_provider: str = field(default_factory=lambda: _env("LLM_PROVIDER", "auto"))
    mistral_api_key: str = field(default_factory=lambda: _env("MISTRAL_API_KEY", ""))
    mistral_base_url: str = field(default_factory=lambda: _env("MISTRAL_BASE_URL", "https://api.mistral.ai/v1"))
    mistral_model: str = field(default_factory=lambda: _env("MISTRAL_MODEL", "mistral-small-latest"))
    llm_timeout_s: float = field(default_factory=lambda: float(_env("LLM_TIMEOUT_S", "20")))

    # Порог уверенности: ниже — заявка уходит человеку.
    confidence_threshold: float = field(default_factory=lambda: float(_env("CONFIDENCE_THRESHOLD", "0.75")))
    # Сколько кандидатов из базы знаний отдаём модели.
    retrieval_top_k: int = field(default_factory=lambda: int(_env("RETRIEVAL_TOP_K", "4")))

    # Ретраи к провайдеру.
    max_retries: int = field(default_factory=lambda: int(_env("LLM_MAX_RETRIES", "3")))
    backoff_base_s: float = field(default_factory=lambda: float(_env("LLM_BACKOFF_BASE_S", "0.5")))

    # Окно, в котором повторное обращение по той же категории считается «возвратом».
    return_window_hours: int = field(default_factory=lambda: int(_env("RETURN_WINDOW_HOURS", "72")))

    db_path: str = field(default_factory=lambda: _env("DB_PATH", str(ROOT / "triage.db")))
    catalog_path: str = field(default_factory=lambda: _env("CATALOG_PATH", str(ROOT / "data" / "catalog.json")))
    # Общий секрет для вебхука хелпдеска (заголовок X-Webhook-Token). Пусто — без проверки (dev).
    webhook_token: str = field(default_factory=lambda: _env("WEBHOOK_TOKEN", ""))
    worker_concurrency: int = field(default_factory=lambda: int(_env("WORKER_CONCURRENCY", "4")))

    @property
    def resolved_provider(self) -> str:
        if self.llm_provider == "auto":
            return "mistral" if self.mistral_api_key else "mock"
        return self.llm_provider


def get_settings() -> Settings:
    return Settings()
