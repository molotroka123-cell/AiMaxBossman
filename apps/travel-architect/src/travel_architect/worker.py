
from __future__ import annotations
import threading, time

class PeriodicWorker:
    def __init__(self, fn, interval_seconds: float = 60.0):
        self.fn=fn; self.interval=max(float(interval_seconds),1.0)
        self.stop_event=threading.Event(); self.thread=None

    def start(self):
        if self.thread and self.thread.is_alive(): return
        def loop():
            while not self.stop_event.wait(self.interval):
                try: self.fn()
                except Exception: pass
        self.thread=threading.Thread(target=loop,daemon=True); self.thread.start()

    def stop(self):
        self.stop_event.set()
