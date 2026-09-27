"""Гибридный поиск кандидатов по базе знаний без внешних зависимостей.

- Sparse: BM25 по словам с грубым стеммингом (обрезка окончаний) — ловит точные термины («401», «кэш»).
- «Dense-lite»: косинус по TF-IDF символьных триграмм — устойчив к опечаткам и словоформам.
- Слияние через Reciprocal Rank Fusion.

В продовой версии второй сигнал заменяется на эмбеддинги + pgvector; интерфейс `search()` не меняется.
"""
from __future__ import annotations

import math
import re
from collections import Counter
from dataclasses import dataclass

from .catalog import Catalog, Instruction

_WORD = re.compile(r"[a-zа-яё0-9]+", re.IGNORECASE)
_STOP = {
    "и", "в", "во", "не", "на", "я", "что", "с", "со", "как", "а", "то", "все", "всё", "она", "так", "его",
    "но", "да", "ты", "к", "у", "же", "вы", "за", "бы", "по", "только", "ее", "её", "мне", "было", "вот",
    "от", "меня", "еще", "ещё", "нет", "о", "из", "ему", "теперь", "когда", "уже", "или", "ни", "быть",
    "был", "него", "до", "вас", "нибудь", "опять", "уж", "вам", "ведь", "там", "потом", "себя", "ничего",
    "ей", "может", "они", "тут", "где", "есть", "надо", "ней", "для", "мы", "тебя", "их", "чем", "была",
    "сам", "чтоб", "без", "будто", "чего", "раз", "тоже", "себе", "под", "будет", "ж", "тогда", "кто",
    "этот", "это", "здравствуйте", "добрый", "день", "помогите", "пожалуйста", "спасибо", "у", "меня",
}


def _stem(word: str) -> str:
    return word[:5] if len(word) > 5 else word


def tokenize(text: str) -> list[str]:
    return [_stem(w) for w in (m.group(0).lower().replace("ё", "е") for m in _WORD.finditer(text))
            if w not in _STOP]


def _trigrams(text: str) -> Counter:
    t = " " + " ".join(m.group(0).lower().replace("ё", "е") for m in _WORD.finditer(text)) + " "
    return Counter(t[i:i + 3] for i in range(len(t) - 2))


@dataclass(frozen=True)
class Candidate:
    instruction: Instruction
    score: float      # итоговый RRF-скор
    bm25: float       # сырой BM25, пригодится для эвристик и отладки
    cosine: float     # косинус по триграммам, 0..1


class HybridRetriever:
    def __init__(self, catalog: Catalog, k1: float = 1.5, b: float = 0.75, rrf_k: int = 60):
        self.items = list(catalog)
        self.k1, self.b, self.rrf_k = k1, b, rrf_k

        self._docs = [tokenize(i.search_text) for i in self.items]
        self._tf = [Counter(d) for d in self._docs]
        self._avgdl = sum(len(d) for d in self._docs) / max(len(self._docs), 1)
        n = len(self._docs)
        df = Counter(t for d in self._docs for t in set(d))
        self._idf = {t: math.log(1 + (n - f + 0.5) / (f + 0.5)) for t, f in df.items()}

        grams = [_trigrams(i.search_text) for i in self.items]
        gdf = Counter(g for c in grams for g in c)
        self._gidf = {g: math.log((1 + n) / (1 + f)) + 1 for g, f in gdf.items()}
        self._gvecs = [self._tfidf(c) for c in grams]

    def _tfidf(self, c: Counter) -> dict[str, float]:
        v = {g: tf * self._gidf.get(g, 0.0) for g, tf in c.items()}
        norm = math.sqrt(sum(x * x for x in v.values())) or 1.0
        return {g: x / norm for g, x in v.items()}

    def _bm25(self, q: list[str], idx: int) -> float:
        tf, dl = self._tf[idx], len(self._docs[idx])
        s = 0.0
        for t in q:
            f = tf.get(t, 0)
            if f:
                s += self._idf[t] * f * (self.k1 + 1) / (f + self.k1 * (1 - self.b + self.b * dl / self._avgdl))
        return s

    def search(self, text: str, top_k: int = 4) -> list[Candidate]:
        q = tokenize(text)
        qv = self._tfidf(_trigrams(text))
        bm = [self._bm25(q, i) for i in range(len(self.items))]
        cos = [sum(w * dv.get(g, 0.0) for g, w in qv.items()) for dv in self._gvecs]

        rank_bm = {i: r for r, i in enumerate(sorted(range(len(bm)), key=lambda i: -bm[i]))}
        rank_cos = {i: r for r, i in enumerate(sorted(range(len(cos)), key=lambda i: -cos[i]))}
        fused = [1 / (self.rrf_k + rank_bm[i] + 1) + 1 / (self.rrf_k + rank_cos[i] + 1)
                 for i in range(len(self.items))]
        order = sorted(range(len(self.items)), key=lambda i: -fused[i])[:top_k]
        return [Candidate(self.items[i], fused[i], bm[i], cos[i]) for i in order]
