"""Регрессионный прогон на золотом наборе. Запускается на каждое изменение промпта/порога/каталога.

    python -m eval.run_eval                     # сравнить с baseline, exit 1 при регрессии
    python -m eval.run_eval --update-baseline   # зафиксировать текущие цифры как baseline

Ключевые метрики:
- auto_precision   — среди автозакрытых доля с ПРАВИЛЬНОЙ инструкцией (ошибка = пользователю ушла ерунда);
- critical_errors  — нетиповые заявки (инцидент/оплата/безопасность), которые система закрыла сама;
- auto_coverage    — доля типовых заявок, закрытых автоматически (польза);
- category_acc     — точность категории (важна и при эскалации: по ней маршрутизируют).
"""
from __future__ import annotations

import argparse
import asyncio
import json
import sys
from collections import defaultdict
from pathlib import Path

from app.catalog import Catalog
from app.config import get_settings
from app.llm import build_llm
from app.prompts import PROMPT_VERSION
from app.schemas import TicketIn
from app.triage import TriageService

HERE = Path(__file__).parent
TOLERANCE = 0.02


async def evaluate(golden_path: Path, verbose: bool = False) -> dict:
    s = get_settings()
    llm = build_llm(s)
    svc = TriageService(s, Catalog.load(s.catalog_path), llm)
    rows = [json.loads(l) for l in golden_path.read_text(encoding="utf-8").splitlines() if l.strip()]

    n_typ = sum(1 for r in rows if r["instruction_id"])
    cat_ok = auto = auto_ok = crit = typ_auto = 0
    per_cat = defaultdict(lambda: [0, 0])
    failures = []
    for r in rows:
        res, _ = await svc.process(TicketIn(ticket_id=r["id"], user_id="eval", body=r["text"]))
        ok_cat = res.category.value == r["category"]
        cat_ok += ok_cat
        per_cat[r["category"]][0] += ok_cat
        per_cat[r["category"]][1] += 1
        if res.decision.value == "auto_resolved":
            auto += 1
            if res.instruction_id == r["instruction_id"]:
                auto_ok += 1
            else:
                failures.append((r["id"], "WRONG_AUTO", r["instruction_id"], res.instruction_id, r["text"]))
            if r["instruction_id"] is None:
                crit += 1
            else:
                typ_auto += res.instruction_id == r["instruction_id"]
        elif not ok_cat:
            failures.append((r["id"], "WRONG_CAT", r["category"], res.category.value, r["text"]))
        if verbose:
            print(f"{r['id']}: {res.decision.value:15s} {res.category.value:13s} "
                  f"{res.instruction_id or '-':7s} conf={res.confidence:.2f} [{res.reason}]")
    if hasattr(llm, "aclose"):
        await llm.aclose()

    return {
        "prompt_version": PROMPT_VERSION,
        "model": getattr(llm, "model", "?"),
        "threshold": s.confidence_threshold,
        "n": len(rows),
        "category_acc": round(cat_ok / len(rows), 3),
        "auto_precision": round(auto_ok / auto, 3) if auto else 1.0,
        "auto_coverage": round(typ_auto / n_typ, 3) if n_typ else 0.0,
        "critical_errors": crit,
        "per_category_acc": {k: round(a / b, 3) for k, (a, b) in sorted(per_cat.items())},
        "failures": failures,
    }


def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("--golden", default=str(HERE / "golden.jsonl"))
    ap.add_argument("--baseline", default=str(HERE / "baseline.json"))
    ap.add_argument("--update-baseline", action="store_true")
    ap.add_argument("-v", "--verbose", action="store_true")
    a = ap.parse_args()

    rep = asyncio.run(evaluate(Path(a.golden), a.verbose))
    print(json.dumps({k: v for k, v in rep.items() if k != "failures"}, ensure_ascii=False, indent=2))
    for f in rep["failures"]:
        print("  FAIL", *f)

    bpath = Path(a.baseline)
    if a.update_baseline:
        bpath.write_text(json.dumps({k: v for k, v in rep.items() if k != "failures"},
                                    ensure_ascii=False, indent=2), encoding="utf-8")
        print(f"baseline updated -> {bpath}")
        return
    if not bpath.exists():
        print("no baseline yet; run with --update-baseline")
        return

    base = json.loads(bpath.read_text(encoding="utf-8"))
    problems = []
    if rep["critical_errors"] > 0:
        problems.append(f"critical_errors={rep['critical_errors']} (нетиповая заявка закрыта автоматически)")
    for m in ("auto_precision", "category_acc", "auto_coverage"):
        if rep[m] < base[m] - TOLERANCE:
            problems.append(f"{m}: {base[m]} -> {rep[m]}")
    for cat, acc in rep["per_category_acc"].items():
        if acc < base["per_category_acc"].get(cat, 0) - 0.1:
            problems.append(f"категория {cat}: {base['per_category_acc'][cat]} -> {acc}")
    if problems:
        print("\nREGRESSION:\n  " + "\n  ".join(problems))
        sys.exit(1)
    print("\nOK: регрессий относительно baseline нет")


if __name__ == "__main__":
    main()
