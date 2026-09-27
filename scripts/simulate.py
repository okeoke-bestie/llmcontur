"""Прогон исторических заявок через контур («пилот на истории»): сравнение «до/после».

    python -m scripts.simulate --n 500

Время ответа инженера по эскалированным берётся из истории (как было до автоматизации).
"""
from __future__ import annotations

import argparse
import asyncio
import dataclasses
import json
import tempfile
from datetime import datetime, timedelta
from pathlib import Path
from statistics import mean

from app import metrics
from app.catalog import Catalog
from app.config import get_settings
from app.llm import build_llm
from app.schemas import TicketIn
from app.storage import Storage
from app.triage import TriageService
from app.worker import Worker


async def run(n: int, history: Path) -> None:
    rows = [json.loads(l) for l in history.read_text(encoding="utf-8").splitlines() if l.strip()][-n:]
    tmp = Path(tempfile.mkdtemp()) / "sim.db"
    s = dataclasses.replace(get_settings(), db_path=str(tmp))
    llm = build_llm(s)
    storage = Storage(s.db_path)
    worker = Worker(TriageService(s, Catalog.load(s.catalog_path), llm), storage, s.worker_concurrency)
    await worker.start()
    for r in rows:
        if storage.enqueue(TicketIn(ticket_id=r["ticket_id"], user_id=r["user_id"], body=r["body"],
                                    created_at=datetime.fromisoformat(r["created_at"]))):
            worker.submit(r["ticket_id"])
    await worker.drain()
    await worker.stop()

    wrong_auto = 0
    by_id = {r["ticket_id"]: r for r in rows}
    for tid, r in by_id.items():
        res = storage.get(tid)
        created = datetime.fromisoformat(r["created_at"])
        if res["decision"] == "auto_resolved":
            # ответ уходит сразу после обработки; закладываем 1 минуту на очередь и инференс
            storage.override_first_response(tid, created + timedelta(minutes=1))
            # «автозакрытие» считаем ошибочным, если по истории проблему решала другая инструкция/инженер
            if res["instruction_id"] != r["resolution"]:
                wrong_auto += 1
        else:
            storage.mark_engineer_reply(tid, created + timedelta(minutes=r["first_response_minutes"]))

    m = metrics.compute(storage.rows_for_metrics(), s.return_window_hours)
    before = mean(r["first_response_minutes"] for r in rows)
    after = m["time_to_first_response"]["all"]["mean_min"]
    auto_n = m["decisions"].get("auto_resolved", 0)
    print(json.dumps(m, ensure_ascii=False, indent=2))
    print(f"\nПровайдер: {s.resolved_provider} ({getattr(llm, 'model', '?')}), заявок: {len(rows)}")
    print(f"Автозакрыто: {m['auto_resolution_rate']:.1%}; из них неверная инструкция "
          f"(по истории): {wrong_auto}/{auto_n} = {wrong_auto / max(auto_n, 1):.1%}")
    print(f"Среднее время до первого ответа: {before:.0f} мин -> {after:.0f} мин")
    if hasattr(llm, "aclose"):
        await llm.aclose()
    storage.close()


def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("--n", type=int, default=500)
    ap.add_argument("--history", default="data/history.jsonl")
    a = ap.parse_args()
    asyncio.run(run(a.n, Path(a.history)))


if __name__ == "__main__":
    main()
