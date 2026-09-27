"""Продуктовые метрики контура.

Главная — доля возвратов: пользователь, получивший автоответ, снова пишет по той же категории
в течение окна. Фиктивное автозакрытие хуже, чем отсутствие автоматизации.
"""
from __future__ import annotations

from collections import Counter, defaultdict
from datetime import datetime, timedelta
from statistics import mean, median
from typing import Any


def _dt(s: str | None) -> datetime | None:
    return datetime.fromisoformat(s) if s else None


def compute(rows: list[dict[str, Any]], return_window_hours: int = 72) -> dict[str, Any]:
    total = len(rows)
    if total == 0:
        return {"total": 0}

    decisions = Counter(r["decision"] for r in rows)
    auto = [r for r in rows if r["decision"] == "auto_resolved"]

    # возвраты
    by_user: dict[str, list[dict]] = defaultdict(list)
    for r in rows:
        by_user[r["user_id"]].append(r)
    window = timedelta(hours=return_window_hours)
    returned = 0
    for r in auto:
        t0 = _dt(r["created_at"])
        if any(o["ticket_id"] != r["ticket_id"] and o["category"] == r["category"]
               and t0 < _dt(o["created_at"]) <= t0 + window for o in by_user[r["user_id"]]):
            returned += 1

    # время до первого ответа (минуты) — только по заявкам, где ответ уже был
    def ttfr(rs):
        vals = [(_dt(r["first_response_at"]) - _dt(r["created_at"])).total_seconds() / 60
                for r in rs if r["first_response_at"]]
        return {"n": len(vals), "mean_min": round(mean(vals), 1) if vals else None,
                "median_min": round(median(vals), 1) if vals else None}

    per_cat = defaultdict(Counter)
    for r in rows:
        per_cat[r["category"]][r["decision"]] += 1

    return {
        "total": total,
        "decisions": dict(decisions),
        "auto_resolution_rate": round(len(auto) / total, 3),
        "return_rate": round(returned / len(auto), 3) if auto else None,
        "net_auto_resolution_rate": round((len(auto) - returned) / total, 3),
        "manual_fallback_rate": round(decisions.get("manual_fallback", 0) / total, 3),
        "time_to_first_response": {
            "all": ttfr(rows),
            "auto_resolved": ttfr(auto),
            "escalated": ttfr([r for r in rows if r["decision"] != "auto_resolved"]),
        },
        "by_category": {k: dict(v) for k, v in sorted(per_cat.items())},
    }
