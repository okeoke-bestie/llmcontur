"""Анализ до написания кода: какую долю заявок закрывают типовые инструкции -> верхняя граница эффекта,
и окупается ли инференс против сэкономленных часов поддержки.

    python -m scripts.analyze_history --history data/history.jsonl
"""
from __future__ import annotations

import argparse
import json
from collections import Counter
from pathlib import Path


def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("--history", default="data/history.jsonl")
    ap.add_argument("--engineer-rate", type=float, default=1500.0, help="стоимость часа инженера, ₽")
    ap.add_argument("--llm-cost-per-ticket", type=float, default=0.35, help="стоимость инференса на заявку, ₽")
    ap.add_argument("--expected-auto-share", type=float, default=0.6,
                    help="какую часть типовых реально закроем автоматически (консервативно)")
    a = ap.parse_args()

    rows = [json.loads(l) for l in Path(a.history).read_text(encoding="utf-8").splitlines() if l.strip()]
    n = len(rows)
    by_res = Counter(r["resolution"] for r in rows)
    typical = [(k, v) for k, v in by_res.most_common() if k != "engineer_fix"]

    print(f"Всего заявок: {n}\n")
    print("Кумулятивное покрытие типовыми инструкциями:")
    cum = 0
    for i, (k, v) in enumerate(typical, 1):
        cum += v
        print(f"  top-{i:<2} {k}  {v:5d}  ({v / n:5.1%})   накопленно: {cum / n:5.1%}")
    typical_share = cum / n
    print(f"\nНетиповые (engineer_fix): {by_res['engineer_fix'] / n:.1%}")

    months = 3
    per_month = n / months
    typ_minutes = sum(r["handle_minutes"] for r in rows if r["resolution"] != "engineer_fix") / months
    saved_hours = typ_minutes * a.expected_auto_share / 60
    saved_rub = saved_hours * a.engineer_rate
    llm_rub = per_month * a.llm_cost_per_ticket
    print("\nЭкономика в месяц (оценка):")
    print(f"  заявок в месяц:                         {per_month:,.0f}")
    print(f"  верхняя граница автозакрытия:           {typical_share:.1%}")
    print(f"  реалистично закроем (×{a.expected_auto_share}):            {typical_share * a.expected_auto_share:.1%}")
    print(f"  сэкономлено часов инженеров:            {saved_hours:,.0f} ч  (~{saved_rub:,.0f} ₽)")
    print(f"  стоимость инференса (все заявки):       ~{llm_rub:,.0f} ₽")
    print(f"  окупаемость: {'ДА' if saved_rub > llm_rub * 3 else 'сомнительно'} "
          f"(экономия / затраты = {saved_rub / max(llm_rub, 1):.0f}x)")


if __name__ == "__main__":
    main()
