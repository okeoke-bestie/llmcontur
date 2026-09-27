import dataclasses
import time

from fastapi.testclient import TestClient

from app.main import create_app

from .conftest import FakeLLM, ok


def wait_done(c, tid, timeout=5.0):
    t0 = time.time()
    while time.time() - t0 < timeout:
        r = c.get(f"/tickets/{tid}").json()
        if r["status"] == "done":
            return r
        time.sleep(0.02)
    raise AssertionError("not processed in time")


def test_end_to_end_with_mock(settings):
    with TestClient(create_app(settings)) as c:
        assert c.get("/health").json()["provider"] == "mock"
        r = c.post("/tickets", json={"ticket_id": "T-1", "user_id": "u1",
                                     "body": "не приходят уведомления на android"})
        assert r.status_code == 202 and r.json()["duplicate"] is False
        res = wait_done(c, "T-1")
        assert res["decision"] == "auto_resolved" and res["instruction_id"] == "KB-008"
        audit = c.get("/tickets/T-1/audit").json()
        assert len(audit) == 1 and audit[0]["prompt_version"] and audit[0]["messages"]


def test_idempotent_by_ticket_id(settings):
    llm = FakeLLM(ok())
    with TestClient(create_app(settings, llm=llm)) as c:
        body = {"ticket_id": "T-2", "user_id": "u1", "body": "показывает старые данные"}
        assert c.post("/tickets", json=body).json()["duplicate"] is False
        wait_done(c, "T-2")
        assert c.post("/tickets", json=body).json()["duplicate"] is True
        time.sleep(0.1)
        assert llm.calls == 1
        assert len(c.get("/tickets/T-2/audit").json()) == 1


def test_webhook_token_required(settings):
    s = dataclasses.replace(settings, webhook_token="secret")
    with TestClient(create_app(s)) as c:
        body = {"ticket_id": "T-3", "user_id": "u1", "body": "кэш"}
        assert c.post("/tickets", json=body).status_code == 401
        assert c.post("/tickets", json=body, headers={"X-Webhook-Token": "secret"}).status_code == 202


def test_metrics_and_engineer_reply(settings):
    with TestClient(create_app(settings)) as c:
        c.post("/tickets", json={"ticket_id": "A", "user_id": "u1", "body": "не приходят уведомления",
                                 "created_at": "2026-03-01T10:00:00+00:00"})
        c.post("/tickets", json={"ticket_id": "B", "user_id": "u1", "body": "не приходят пуши, не помогло",
                                 "created_at": "2026-03-01T12:00:00+00:00"})
        c.post("/tickets", json={"ticket_id": "C", "user_id": "u2", "body": "списали деньги дважды",
                                 "created_at": "2026-03-01T12:00:00+00:00"})
        for t in "ABC":
            wait_done(c, t)
        assert c.get("/tickets/C").json()["decision"] == "escalated"
        assert c.post("/tickets/C/engineer-reply",
                      json={"replied_at": "2026-03-01T13:00:00+00:00"}).json()["updated"] is True
        m = c.get("/metrics").json()
        assert m["total"] == 3
        assert m["return_rate"] is not None and m["return_rate"] > 0   # B — возврат по A
        assert m["time_to_first_response"]["escalated"]["n"] >= 1
