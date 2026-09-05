"""A restarted task must show current durable failure, never an old success."""
import asyncio
import pytest
import sqlalchemy as sa
from bcc.db import tasks, task_runs
from .browser_support import chromium_available, reason
from .test_ux2_thinking_pane import _launch, _login, live

pytestmark = [pytest.mark.timeout(90), pytest.mark.skipif(not chromium_available(), reason=reason())]


def test_task_card_respects_current_result_even_when_attempt_numbers_restart(live):
    from playwright.sync_api import sync_playwright, expect
    async def seed():
        async with live.svc.db.session() as session:
            task = await session.execute(sa.insert(tasks).values(title="Current rerun failure", prompt="read", status="failed"))
            tid = task.inserted_primary_key[0]
            await session.execute(sa.insert(task_runs).values(task_id=tid, attempt=3, status="completed", result="OLD SUCCESS MUST NOT APPEAR"))
            current = await session.execute(sa.insert(task_runs).values(task_id=tid, attempt=1, status="failed", error="CURRENT PROVIDER FAILURE"))
            rid = current.inserted_primary_key[0]
            await session.commit()
            return tid, rid
    tid, rid = asyncio.run_coroutine_threadsafe(seed(), live.loop).result(10)
    with sync_playwright() as pw:
        browser = _launch(pw)
        try:
            page = browser.new_page()
            _login(page, live)
            page.goto(f"{live.url}/#/tasks?task={tid}")
            body = page.locator(".task.open .task-body")
            expect(body).to_contain_text("CURRENT PROVIDER FAILURE", timeout=30000)
            expect(body).not_to_contain_text("OLD SUCCESS MUST NOT APPEAR")
            expect(body).to_contain_text(f"run #{rid}")
        finally:
            browser.close()
