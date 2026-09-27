"""Детерминированные правила поверх модели: заявки с признаками инцидента, денег, безопасности
или интеграций НИКОГДА не закрываются автоматически, что бы ни ответила модель."""
from __future__ import annotations

import re

RISK_PATTERNS: dict[str, re.Pattern] = {
    "mass_incident": re.compile(r"(у всех|у всей команды|весь отдел|всей компании|массов|никто не может)", re.I),
    "data_loss": re.compile(r"(пропал\w*\s+(все\s+)?данн|удалил\w*\s+(все|данн)|потерял\w*\s+данн)", re.I),
    "money": re.compile(r"(списал|оплат|деньг|возврат средств|двойн\w* списан|чек|счёт на оплату)", re.I),
    "security": re.compile(r"(взлом|чуж\w+\s+(устройств|ip|аккаунт)|не я заходил|утечк|фишинг|зашёл в мой)", re.I),
    "integration": re.compile(r"(\bapi\b|вебхук|webhook|\bsso\b|интеграц|1с\b|\b5\d\d\b)", re.I),
}


def risk_flags(text: str) -> list[str]:
    return [name for name, rx in RISK_PATTERNS.items() if rx.search(text)]
