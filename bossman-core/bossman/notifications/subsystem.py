from __future__ import annotations
from .runtime import BRIDGE,DISPATCHER,STORE

class NotificationSubsystem:
    name="notifications";critical=False
    async def validate(self):return None
    async def start(self):
        STORE.recover_sending()
        await BRIDGE.start()
        await DISPATCHER.start()
    async def stop(self):
        await BRIDGE.stop()
        await DISPATCHER.stop()
def build_subsystem():return NotificationSubsystem()
