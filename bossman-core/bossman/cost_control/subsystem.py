from __future__ import annotations
import asyncio
from .runtime import GOVERNOR,seed_env_policies

class CostControlSubsystem:
    name="cost_control";critical=True
    def __init__(self):self._task=None
    async def validate(self):seed_env_policies()
    async def start(self):
        async def janitor():
            while True:
                GOVERNOR.cleanup_expired()
                await asyncio.sleep(60)
        self._task=asyncio.create_task(janitor())
    async def stop(self):
        if self._task:
            self._task.cancel()
            try:await self._task
            except asyncio.CancelledError:pass
            self._task=None
def build_subsystem():return CostControlSubsystem()
