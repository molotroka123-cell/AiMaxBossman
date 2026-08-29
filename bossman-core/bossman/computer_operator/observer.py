from __future__ import annotations
import time
from .models import Observation,new_id

class Observer:
    def __init__(self,structured,screenshot,summarizer=None):
        self.structured=structured; self.screenshot=screenshot; self.summarizer=summarizer
    async def observe(self,*,generation:int):
        fg=await self.structured.foreground()
        tree=await self.structured.ui_tree()
        ref,sensitive=await self.screenshot.capture()
        if self.summarizer:
            summary=await self.summarizer(foreground=fg,ui_tree=tree,screenshot_ref=ref,sensitive=sensitive)
        else:
            summary=f"foreground={fg!r}; ui_tree={'available' if tree is not None else 'unavailable'}"
        return Observation(new_id("obs"),time.time(),fg or {},str(summary)[:12000],
                           tree,ref,bool(sensitive),generation)
