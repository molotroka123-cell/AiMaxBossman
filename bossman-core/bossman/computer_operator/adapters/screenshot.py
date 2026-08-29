from __future__ import annotations
import asyncio,tempfile,time
from pathlib import Path
class LocalScreenshotProvider:
    def __init__(self,root=None):
        self.root=Path(root or Path(tempfile.gettempdir())/"bossman-computer")
        self.root.mkdir(parents=True,exist_ok=True)
    async def capture(self):
        def f():
            try: import pyautogui
            except ImportError: return None,False
            p=self.root/f"screen-{time.time_ns()}.png"
            pyautogui.screenshot().save(p)
            return str(p),False
        return await asyncio.to_thread(f)
