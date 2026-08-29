from __future__ import annotations
import asyncio,random
from .store import SQLiteNotificationStore

class NotificationDispatcher:
    def __init__(self,store:SQLiteNotificationStore,transport,*,max_attempts:int=6):
        self.store=store;self.transport=transport;self.max_attempts=max(1,min(max_attempts,10))
        self._task=None;self._stop=asyncio.Event()

    async def start(self):
        self.store.recover_sending();self._stop.clear()
        if not self._task:self._task=asyncio.create_task(self._run())

    async def stop(self):
        self._stop.set()
        if self._task:
            self._task.cancel()
            try:await self._task
            except asyncio.CancelledError:pass
            self._task=None

    async def _run(self):
        while not self._stop.is_set():
            n=self.store.claim_next()
            if not n:
                try:await asyncio.wait_for(self._stop.wait(),timeout=1.0)
                except asyncio.TimeoutError:pass
                continue
            try:
                await self.transport.send(n)
            except Exception as exc:
                attempt=self.store.attempts(n.id)
                base=min(300.0,2.0**min(attempt,8))
                delay=base+random.uniform(0,min(3.0,base/4))
                # Persist only exception type; transport errors must not leak token-bearing URLs.
                self.store.mark_retry(n.id,type(exc).__name__,delay_s=delay,max_attempts=self.max_attempts)
            else:self.store.mark_sent(n.id)
