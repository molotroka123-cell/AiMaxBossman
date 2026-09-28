"""RC19 audit regressions for the owner's «Пульт» companion (offline, no Telegram)."""
from __future__ import annotations

import asyncio
import os

from bcc.telegram_companion.store import Store, single_instance

from test_companion import GUEST, OWNER, FakeCore, FakeModels, cloud_settings, msg


def _app(tmp_path):
    from bcc.telegram_companion.service import Companion
    store = Store(tmp_path)
    return Companion(cloud_settings(), store, None, FakeCore(), FakeModels()), store


def test_guest_cannot_opt_into_paid_cloud(tmp_path):
    """Paid cloud spends the owner's budget: a guest's /cloud on is refused."""
    async def run():
        app, store = _app(tmp_path)
        try:
            reply = await app.handle(GUEST, msg(GUEST, text="/cloud on"))
            assert "только владелец" in reply
            assert not store.get("cloud:" + GUEST.key)
            assert "включён" in await app.handle(OWNER, msg(text="/cloud on"))
            assert store.get("cloud:" + OWNER.key) is True
            await app.handle(GUEST, msg(GUEST, text="/cloud off"))
            assert store.get("cloud:" + GUEST.key) is False
        finally:
            store.close()
    asyncio.run(run())


def test_single_instance_unlocks_before_close(monkeypatch, tmp_path):
    modes = []
    if os.name == "nt":
        import msvcrt
        real = msvcrt.locking
        monkeypatch.setattr(msvcrt, "locking", lambda fd, mode, n: (modes.append(mode), real(fd, mode, n))[1])
        unlock = msvcrt.LK_UNLCK
    else:
        import fcntl
        real = fcntl.flock
        monkeypatch.setattr(fcntl, "flock", lambda f, op: (modes.append(op), real(f, op))[1])
        unlock = fcntl.LOCK_UN
    with single_instance(tmp_path):
        pass
    assert modes[-1] == unlock
