"""SQLite-хранилище: заявки, результаты (идемпотентно по ticket_id) и аудит-лог каждого вызова модели.

Для прода меняется на PostgreSQL без изменения интерфейса класса.
"""
from __future__ import annotations

import json
import sqlite3
import threading
from datetime import datetime, timezone
from typing import Any

from .schemas import TicketIn, TriageResult
from .triage import TriageTrace

SCHEMA = """
CREATE TABLE IF NOT EXISTS tickets (
    ticket_id   TEXT PRIMARY KEY,
    user_id     TEXT NOT NULL,
    subject     TEXT NOT NULL,
    body        TEXT NOT NULL,
    created_at  TEXT NOT NULL,
    status      TEXT NOT NULL DEFAULT 'queued'   -- queued | processing | done
);
CREATE INDEX IF NOT EXISTS ix_tickets_user ON tickets(user_id, created_at);

CREATE TABLE IF NOT EXISTS results (
    ticket_id          TEXT PRIMARY KEY REFERENCES tickets(ticket_id),
    decision           TEXT NOT NULL,
    category           TEXT NOT NULL,
    confidence         REAL NOT NULL,
    technical_summary  TEXT NOT NULL,
    instruction_id     TEXT,
    reply_to_user      TEXT,
    reason             TEXT NOT NULL,
    prompt_version     TEXT NOT NULL,
    model              TEXT NOT NULL,
    processed_at       TEXT NOT NULL,
    first_response_at  TEXT            -- для автозакрытия = processed_at, для эскалации — ответ инженера
);

CREATE TABLE IF NOT EXISTS audit_log (
    id              INTEGER PRIMARY KEY AUTOINCREMENT,
    ticket_id       TEXT NOT NULL,
    logged_at       TEXT NOT NULL,
    prompt_version  TEXT NOT NULL,
    model           TEXT NOT NULL,
    candidates      TEXT NOT NULL,
    messages        TEXT NOT NULL,
    raw_response    TEXT,
    attempts        INTEGER NOT NULL,
    error           TEXT,
    latency_ms      INTEGER NOT NULL,
    decision        TEXT NOT NULL,
    reply_to_user   TEXT
);
"""


def _now() -> str:
    return datetime.now(timezone.utc).isoformat()


class Storage:
    def __init__(self, path: str):
        self._conn = sqlite3.connect(path, check_same_thread=False, isolation_level=None)
        self._conn.row_factory = sqlite3.Row
        self._conn.execute("PRAGMA journal_mode=WAL")
        self._conn.executescript(SCHEMA)
        self._lock = threading.Lock()

    def close(self) -> None:
        self._conn.close()

    # --- заявки -------------------------------------------------------------------------------
    def enqueue(self, t: TicketIn) -> bool:
        """True — новая заявка; False — дубль (вебхук пришёл повторно), повторно не обрабатываем."""
        created = (t.created_at or datetime.now(timezone.utc)).isoformat()
        with self._lock:
            cur = self._conn.execute(
                "INSERT OR IGNORE INTO tickets(ticket_id,user_id,subject,body,created_at) VALUES (?,?,?,?,?)",
                (t.ticket_id, t.user_id, t.subject, t.body, created))
            return cur.rowcount == 1

    def claim(self, ticket_id: str) -> TicketIn | None:
        """Атомарно забираем заявку в работу; второй воркер получит None."""
        with self._lock:
            cur = self._conn.execute(
                "UPDATE tickets SET status='processing' WHERE ticket_id=? AND status='queued'", (ticket_id,))
            if cur.rowcount != 1:
                return None
            row = self._conn.execute("SELECT * FROM tickets WHERE ticket_id=?", (ticket_id,)).fetchone()
        return TicketIn(ticket_id=row["ticket_id"], user_id=row["user_id"], subject=row["subject"],
                        body=row["body"], created_at=datetime.fromisoformat(row["created_at"]))

    def pending_ids(self) -> list[str]:
        """Для восстановления после рестарта: всё, что не доехало до done."""
        with self._lock:
            self._conn.execute("UPDATE tickets SET status='queued' WHERE status='processing'")
            return [r[0] for r in self._conn.execute(
                "SELECT ticket_id FROM tickets WHERE status='queued' ORDER BY created_at")]

    # --- результаты ---------------------------------------------------------------------------
    def save_result(self, r: TriageResult, trace: TriageTrace) -> None:
        first_resp = r.processed_at.isoformat() if r.decision.value == "auto_resolved" else None
        with self._lock:
            self._conn.execute("BEGIN")
            try:
                self._conn.execute(
                    """INSERT OR IGNORE INTO results(ticket_id,decision,category,confidence,technical_summary,
                       instruction_id,reply_to_user,reason,prompt_version,model,processed_at,first_response_at)
                       VALUES (?,?,?,?,?,?,?,?,?,?,?,?)""",
                    (r.ticket_id, r.decision.value, r.category.value, r.confidence, r.technical_summary,
                     r.instruction_id, r.reply_to_user, r.reason, r.prompt_version, r.model,
                     r.processed_at.isoformat(), first_resp))
                self._conn.execute(
                    """INSERT INTO audit_log(ticket_id,logged_at,prompt_version,model,candidates,messages,
                       raw_response,attempts,error,latency_ms,decision,reply_to_user)
                       VALUES (?,?,?,?,?,?,?,?,?,?,?,?)""",
                    (r.ticket_id, _now(), r.prompt_version, r.model, json.dumps(trace.candidates),
                     json.dumps(trace.messages, ensure_ascii=False), trace.raw_response, trace.attempts,
                     trace.error, trace.latency_ms, r.decision.value, r.reply_to_user))
                self._conn.execute("UPDATE tickets SET status='done' WHERE ticket_id=?", (r.ticket_id,))
                self._conn.execute("COMMIT")
            except Exception:
                self._conn.execute("ROLLBACK")
                raise

    def mark_engineer_reply(self, ticket_id: str, at: datetime | None = None) -> bool:
        with self._lock:
            cur = self._conn.execute(
                "UPDATE results SET first_response_at=? WHERE ticket_id=? AND first_response_at IS NULL",
                ((at or datetime.now(timezone.utc)).isoformat(), ticket_id))
            return cur.rowcount == 1

    def override_first_response(self, ticket_id: str, at: datetime) -> None:
        """Только для симуляции на истории: время ответа привязываем к историческому created_at."""
        with self._lock:
            self._conn.execute("UPDATE results SET first_response_at=? WHERE ticket_id=?",
                               (at.isoformat(), ticket_id))

    def get(self, ticket_id: str) -> dict[str, Any] | None:
        with self._lock:
            row = self._conn.execute(
                """SELECT t.ticket_id, t.user_id, t.subject, t.body, t.created_at, t.status, r.decision,
                          r.category, r.confidence, r.technical_summary, r.instruction_id, r.reply_to_user,
                          r.reason, r.prompt_version, r.model, r.processed_at, r.first_response_at
                   FROM tickets t LEFT JOIN results r USING(ticket_id) WHERE t.ticket_id=?""",
                (ticket_id,)).fetchone()
        return dict(row) if row else None

    def audit(self, ticket_id: str) -> list[dict[str, Any]]:
        with self._lock:
            rows = self._conn.execute("SELECT * FROM audit_log WHERE ticket_id=? ORDER BY id",
                                      (ticket_id,)).fetchall()
        out = []
        for r in rows:
            d = dict(r)
            d["candidates"] = json.loads(d["candidates"])
            d["messages"] = json.loads(d["messages"])
            out.append(d)
        return out

    def rows_for_metrics(self) -> list[dict[str, Any]]:
        with self._lock:
            return [dict(r) for r in self._conn.execute(
                """SELECT t.ticket_id, t.user_id, t.created_at, r.decision, r.category,
                          r.processed_at, r.first_response_at
                   FROM tickets t JOIN results r USING(ticket_id) ORDER BY t.created_at""")]
