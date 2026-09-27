"""HTTP API. Хелпдеск шлёт вебхук -> заявка в очередь -> воркер -> результат читается по GET."""
from __future__ import annotations

import hmac
import logging
from contextlib import asynccontextmanager
from datetime import datetime

from fastapi import Depends, FastAPI, Header, HTTPException, status
from pydantic import BaseModel

from . import metrics as metrics_mod
from .catalog import Catalog
from .config import Settings, get_settings
from .llm import build_llm
from .prompts import PROMPT_VERSION
from .schemas import TicketIn
from .storage import Storage
from .triage import TriageService
from .worker import Worker

logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(name)s: %(message)s")


def create_app(settings: Settings | None = None, llm=None) -> FastAPI:
    settings = settings or get_settings()

    @asynccontextmanager
    async def lifespan(app: FastAPI):
        catalog = Catalog.load(settings.catalog_path)
        client = llm or build_llm(settings)
        storage = Storage(settings.db_path)
        service = TriageService(settings, catalog, client)
        worker = Worker(service, storage, settings.worker_concurrency)
        app.state.settings, app.state.storage, app.state.worker = settings, storage, worker
        app.state.catalog, app.state.llm = catalog, client
        await worker.start()
        logging.getLogger("app").info("started: provider=%s model=%s prompt=%s catalog=%d",
                                      settings.resolved_provider, getattr(client, "model", "?"),
                                      PROMPT_VERSION, len(catalog))
        try:
            yield
        finally:
            await worker.stop()
            if hasattr(client, "aclose"):
                await client.aclose()
            storage.close()

    app = FastAPI(title="LLM Ticket Triage", version="1.0.0", lifespan=lifespan)

    def check_token(x_webhook_token: str | None = Header(default=None)) -> None:
        if settings.webhook_token and not hmac.compare_digest(x_webhook_token or "", settings.webhook_token):
            raise HTTPException(status.HTTP_401_UNAUTHORIZED, "bad webhook token")

    @app.get("/health")
    async def health():
        return {"status": "ok", "provider": settings.resolved_provider,
                "model": getattr(app.state.llm, "model", "?"), "prompt_version": PROMPT_VERSION,
                "queue_size": app.state.worker.queue.qsize()}

    @app.post("/tickets", status_code=status.HTTP_202_ACCEPTED, dependencies=[Depends(check_token)])
    async def ingest(ticket: TicketIn):
        is_new = app.state.storage.enqueue(ticket)
        if is_new:
            app.state.worker.submit(ticket.ticket_id)
        return {"ticket_id": ticket.ticket_id, "accepted": True, "duplicate": not is_new}

    @app.get("/tickets/{ticket_id}")
    async def get_ticket(ticket_id: str):
        row = app.state.storage.get(ticket_id)
        if not row:
            raise HTTPException(404, "not found")
        return row

    @app.get("/tickets/{ticket_id}/audit")
    async def get_audit(ticket_id: str):
        return app.state.storage.audit(ticket_id)

    class EngineerReply(BaseModel):
        replied_at: datetime | None = None

    @app.post("/tickets/{ticket_id}/engineer-reply", dependencies=[Depends(check_token)])
    async def engineer_reply(ticket_id: str, body: EngineerReply | None = None):
        ok = app.state.storage.mark_engineer_reply(ticket_id, body.replied_at if body else None)
        return {"updated": ok}

    @app.get("/metrics")
    async def get_metrics():
        return metrics_mod.compute(app.state.storage.rows_for_metrics(), settings.return_window_hours)

    @app.get("/catalog")
    async def get_catalog():
        return [{"id": i.id, "category": i.category, "title": i.title} for i in app.state.catalog]

    return app


app = create_app()
