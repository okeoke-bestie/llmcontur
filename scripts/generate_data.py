"""Генератор синтетической истории заявок (данные демонстрационные, не реальные).

Каждая заявка имеет итоговое решение (resolution): id инструкции из каталога или "engineer_fix"
(нетиповая проблема, чинил инженер). На этой истории считаем, какую долю закрывают типовые инструкции.

    python -m scripts.generate_data --n 3000 --out data/history.jsonl
"""
from __future__ import annotations

import argparse
import json
import random
from datetime import datetime, timedelta, timezone
from pathlib import Path

OPENERS = ["", "Здравствуйте! ", "Добрый день. ", "Помогите, ", "Опять проблема: ", "Срочно! ", "Привет, "]
CLOSERS = ["", " Спасибо.", " Что делать?", " Очень мешает работать.", " Жду ответа.", " Это уже второй раз."]
PLATFORMS = ["на android", "на iphone", "в chrome", "в браузере", "на windows", "в приложении", "на macos"]

TEMPLATES: dict[str, list[str]] = {
    "KB-001": ["постоянно выкидывает из аккаунта {p}", "каждый раз после перезапуска просит войти заново",
               "меня разлогинивает каждые полчаса {p}", "сессия истекла, хотя я только что входил",
               "вылетает из учётки, приходится заново вводить пароль"],
    "KB-002": ["после смены пароля ошибка 401 и не пускает", "пишет «Сессия недействительна» после смены пароля",
               "сменил пароль и теперь ошибка авторизации {p}"],
    "KB-003": ["пропал доступ к разделу отчёты, пишет 403", "кнопки стали неактивны после того как сменили роль",
               "слетели права, раздел недоступен", "доступ запрещён к настройкам, вчера всё было"],
    "KB-004": ["не вижу документы коллег в общей папке", "общая папка команды пустая, хотя там есть файлы",
               "не отображаются файлы команды"],
    "KB-005": ["показывает старые данные, изменения не видны", "обновил цены, а на странице висит старая версия",
               "не обновляется информация {p}, вижу вчерашнее", "белый экран после выкладки, видимо кэш"],
    "KB-006": ["бесконечная загрузка {p}", "крутится колесико и ничего не грузится",
               "страница не прогружается, висит спиннер", "очень долго загружается и зависает"],
    "KB-007": ["приложение не подключается к серверу", "ошибка соединения connection refused после смены настроек",
               "поменял прокси в настройках и теперь не подключается", "неверный адрес сервера, как вернуть"],
    "KB-008": ["не приходят уведомления {p}", "пуши перестали приходить", "нет оповещений о новых сообщениях"],
    "KB-009": ["не устанавливается обновление {p}", "ошибка обновления update failed",
               "прерывается установка новой версии, пишет недостаточно места"],
    "KB-010": ["после обновления приложение вылетает при запуске", "крашится сразу после апдейта {p}",
               "обновился до 4.2.1 и теперь закрывается при запуске"],
}
# Нетиповое: чинили инженеры (баги, инциденты, интеграции, вопросы по оплате)
ENGINEER = ["при экспорте в Excel теряются даты в столбце", "у всей команды не работает интеграция с 1С",
            "списали деньги дважды за подписку", "API возвращает 500 на /v2/orders при фильтре по дате",
            "в отчёте неправильно считается НДС", "пропали все данные за прошлую неделю",
            "вебхук не доходит до нашего сервера", "не работает SSO через Azure AD",
            "импорт CSV падает на файле больше 50 МБ", "кто-то зашёл в мой аккаунт с чужого IP"]

# Реалистичный перекос: несколько проблем составляют львиную долю потока.
WEIGHTS = {"KB-001": 14, "KB-002": 4, "KB-003": 10, "KB-004": 4, "KB-005": 13, "KB-006": 7,
           "KB-007": 5, "KB-008": 4, "KB-009": 5, "KB-010": 4, "engineer_fix": 30}
CATEGORY = {"KB-001": "auth_session", "KB-002": "auth_session", "KB-003": "permissions",
            "KB-004": "permissions", "KB-005": "cache", "KB-006": "cache", "KB-007": "client_config",
            "KB-008": "client_config", "KB-009": "update_error", "KB-010": "update_error",
            "engineer_fix": "unknown"}


def make_ticket(rng: random.Random, i: int, start: datetime) -> dict:
    res = rng.choices(list(WEIGHTS), weights=list(WEIGHTS.values()))[0]
    core = rng.choice(ENGINEER if res == "engineer_fix" else TEMPLATES[res]).format(p=rng.choice(PLATFORMS))
    body = rng.choice(OPENERS) + core + rng.choice(CLOSERS)
    created = start + timedelta(minutes=rng.randint(0, 60 * 24 * 90))
    handle_min = rng.randint(8, 40) if res != "engineer_fix" else rng.randint(30, 480)
    return {"ticket_id": f"H-{i:06d}", "user_id": f"u{rng.randint(1, 900):04d}", "subject": "",
            "body": body, "created_at": created.isoformat(), "resolution": res,
            "category": CATEGORY[res], "handle_minutes": handle_min,
            "first_response_minutes": rng.randint(20, 300)}


def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("--n", type=int, default=3000)
    ap.add_argument("--seed", type=int, default=42)
    ap.add_argument("--out", default="data/history.jsonl")
    a = ap.parse_args()
    rng = random.Random(a.seed)
    start = datetime(2026, 1, 1, tzinfo=timezone.utc)
    rows = sorted((make_ticket(rng, i, start) for i in range(a.n)), key=lambda r: r["created_at"])
    Path(a.out).parent.mkdir(parents=True, exist_ok=True)
    with open(a.out, "w", encoding="utf-8") as f:
        for r in rows:
            f.write(json.dumps(r, ensure_ascii=False) + "\n")
    print(f"wrote {len(rows)} tickets -> {a.out}")


if __name__ == "__main__":
    main()
