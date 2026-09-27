"""Каталог выверенных инструкций. Его ведут инженеры; модель только выбирает из него, но не пишет текст."""
from __future__ import annotations

import json
from dataclasses import dataclass
from pathlib import Path


@dataclass(frozen=True)
class Instruction:
    id: str
    category: str
    title: str
    symptoms: str
    steps: tuple[str, ...]

    @property
    def search_text(self) -> str:
        return f"{self.title} {self.symptoms}"

    def render_reply(self) -> str:
        lines = [f"Похоже, это известная проблема: «{self.title}».", "", "Попробуйте, пожалуйста:"]
        lines += [f"{i}. {s}" for i, s in enumerate(self.steps, 1)]
        lines += ["", "Если не помогло — просто ответьте на это письмо, и заявку возьмёт инженер."]
        return "\n".join(lines)


class Catalog:
    def __init__(self, items: list[Instruction]):
        self._items = {i.id: i for i in items}

    @classmethod
    def load(cls, path: str | Path) -> "Catalog":
        raw = json.loads(Path(path).read_text(encoding="utf-8"))
        return cls([Instruction(id=r["id"], category=r["category"], title=r["title"],
                                symptoms=r["symptoms"], steps=tuple(r["steps"])) for r in raw])

    def get(self, instruction_id: str) -> Instruction | None:
        return self._items.get(instruction_id)

    def __iter__(self):
        return iter(self._items.values())

    def __len__(self) -> int:
        return len(self._items)
