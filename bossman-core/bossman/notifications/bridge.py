from __future__ import annotations
import asyncio,json
from .policy import NotificationPolicy

class EventBridge:
    """One subscriber on existing bossman.events; this is not a second EventBus."""
    def __init__(self,store,policy:NotificationPolicy|None=None):
        self.store=store;self.policy=policy or NotificationPolicy();self._task=None;self._queue=None

    async def start(self):
        from .. import events
        self._queue=events.subscribe()
        async def run():
            while True:
                raw=await self._queue.get()
                try:event=json.loads(raw)
                except Exception:continue
                n=self.policy.from_event(event)
                if n:self.store.enqueue(n)
        self._task=asyncio.create_task(run())

    async def stop(self):
        if self._queue is not None:
            try:
                from .. import events
                events.unsubscribe(self._queue)
            except Exception:pass
            self._queue=None
        if self._task:
            self._task.cancel()
            try:await self._task
            except asyncio.CancelledError:pass
            self._task=None
