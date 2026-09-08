from __future__ import annotations
import asyncio,tempfile,time
from pathlib import Path

# Каждое наблюдение писало новый PNG и НИКОГДА ничего не удаляло: длинная сессия
# управления компом оставляла в temp гигабайты кадров. Наблюдения ссылаются на
# последние кадры, поэтому храним ограниченное окно и подчищаем более старые.
DEFAULT_RETENTION = 64

# Окно хранения ведётся В ПАМЯТИ, поэтому кадры ПРЕДЫДУЩЕГО процесса не удалял
# никто: temp копил их между перезапусками вечно. Чистим на старте только
# заведомо чужое старьё — рядом может работать другой процесс bossman, и его
# свежие кадры трогать нельзя.
STALE_AFTER_S = 3600.0


class LocalScreenshotProvider:
    def __init__(self,root=None,retention:int=DEFAULT_RETENTION):
        self.root=Path(root or Path(tempfile.gettempdir())/"bossman-computer")
        self.root.mkdir(parents=True,exist_ok=True)
        self.retention=max(1,int(retention))
        self._written:list[Path]=[]
        self._sweep_stale()
    def _sweep_stale(self)->None:
        """Удалить кадры прошлых запусков. Недоступный каталог наблюдение не роняет."""
        cutoff=time.time()-STALE_AFTER_S
        try: leftovers=list(self.root.glob("screen-*.png"))
        except OSError: return
        for p in leftovers:
            try:
                if p.stat().st_mtime<cutoff: p.unlink()
            except OSError: pass
    def _prune(self)->None:
        """Удалить кадры вне окна хранения. Ошибка удаления не роняет наблюдение."""
        while len(self._written)>self.retention:
            stale=self._written.pop(0)
            try: stale.unlink()
            except OSError: pass
    async def capture(self):
        def f():
            try: import pyautogui
            except ImportError: return None,False
            p=self.root/f"screen-{time.time_ns()}.png"
            # Полный диск или антивирус ломали ВСЁ наблюдение: исключение отсюда
            # уходило наружу и валило шаг целиком, хотя без кадра наблюдение
            # деградирует, а не умирает.
            try: pyautogui.screenshot().save(p)
            except OSError: return None,False
            return str(p),False
        ref,sensitive=await asyncio.to_thread(f)
        if ref:
            self._written.append(Path(ref)); self._prune()
        return ref,sensitive
