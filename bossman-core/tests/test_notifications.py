import asyncio
import pytest
from bossman.notifications.models import Notification,Severity
from bossman.notifications.policy import NotificationPolicy,sanitize
from bossman.notifications.store import SQLiteNotificationStore
from bossman.notifications.dispatcher import NotificationDispatcher

def test_policy_maps_budget_warning_without_secret():
    p=NotificationPolicy()
    n=p.from_event({"kind":"budget.warning","scope":"daily_global","subject":"2026-08-29",
                    "projected_usd":"0.80","limit_usd":"1.00","token":"ignored"})
    assert n and "0.80" in n.body

def test_sanitizer_redacts_token_like_values():
    out=sanitize("api_key=sk-proj-THISISNOTAREALSECRET123")  # ci-secret-scan: allow
    assert "THISISNOTAREALSECRET123" not in out

def test_queue_dedupes(tmp_path):
    s=SQLiteNotificationStore(tmp_path/"n.db")
    n=Notification.create("x",Severity.INFO,"t","b",dedupe_key="same")
    assert s.enqueue(n)
    assert not s.enqueue(Notification.create("x",Severity.INFO,"t","b",dedupe_key="same"))

class FakeTransport:
    def __init__(self,fail=False):self.sent=[];self.fail=fail
    async def send(self,n):
        if self.fail:raise RuntimeError("no network")
        self.sent.append(n.id)

@pytest.mark.asyncio
async def test_dispatcher_sends_and_marks_sent(tmp_path):
    s=SQLiteNotificationStore(tmp_path/"n.db");t=FakeTransport()
    s.enqueue(Notification.create("x",Severity.INFO,"t","b",dedupe_key="1"))
    d=NotificationDispatcher(s,t);await d.start()
    for _ in range(50):
        if s.counts().get("sent"):break
        await asyncio.sleep(.02)
    await d.stop()
    assert s.counts().get("sent")==1

@pytest.mark.asyncio
async def test_transport_failure_does_not_escape_worker(tmp_path):
    s=SQLiteNotificationStore(tmp_path/"n.db");t=FakeTransport(fail=True)
    s.enqueue(Notification.create("x",Severity.INFO,"t","b",dedupe_key="1"))
    d=NotificationDispatcher(s,t,max_attempts=1);await d.start();await asyncio.sleep(.1);await d.stop()
    assert s.counts().get("dead")==1
