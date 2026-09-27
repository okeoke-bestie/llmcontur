"""Очередь и воркеры. In-process asyncio.Queue; в проде меняется на Redis/RabbitMQ без изменения логики."""
from __future__ import annotations

import asyncio
import logging

from .storage import Storage
from .triage import TriageService

log = logging.getLogger("worker")


class Worker:
    def __init__(self, service: TriageService, storage: Storage, concurrency: int = 4):
        self.service = service
        self.storage = storage
        self.queue: asyncio.Queue[str] = asyncio.Queue()
        self.concurrency = concurrency
        self._tasks: list[asyncio.Task] = []

    async def start(self) -> None:
        for tid in self.storage.pending_ids():  # добиваем то, что не успели до рестарта
            self.queue.put_nowait(tid)
        self._tasks = [asyncio.create_task(self._loop(i)) for i in range(self.concurrency)]

    async def stop(self) -> None:
        for t in self._tasks:
            t.cancel()
        await asyncio.gather(*self._tasks, return_exceptions=True)

    def submit(self, ticket_id: str) -> None:
        self.queue.put_nowait(ticket_id)

    async def drain(self) -> None:
        await self.queue.join()

    async def handle(self, ticket_id: str) -> None:
        ticket = self.storage.claim(ticket_id)
        if ticket is None:  # уже обработана или в работе у другого воркера
            return
        result, trace = await self.service.process(ticket)
        self.storage.save_result(result, trace)
        log.info("ticket=%s decision=%s category=%s conf=%.2f reason=%s",
                 result.ticket_id, result.decision.value, result.category.value,
                 result.confidence, result.reason)

    async def _loop(self, n: int) -> None:
        while True:
            tid = await self.queue.get()
            try:
                await self.handle(tid)
            except Exception:  # воркер не должен умирать из-за одной заявки
                log.exception("worker %d failed on %s", n, tid)
            finally:
                self.queue.task_done()
