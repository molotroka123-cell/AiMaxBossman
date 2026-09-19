from __future__ import annotations
import asyncio,time
from .models import Observation,new_id

class Observer:
    """Собирает одно наблюдение рабочего стола.

    Раньше три источника опрашивались строго последовательно (`foreground` ->
    `ui_tree` -> `screenshot`), а на Windows `foreground` и `ui_tree` вдобавок
    КАЖДЫЙ заново разрешали окно переднего плана через UIA. Итог: задержка =
    сумма трёх операций плюс лишний COM-обход, и фрагменты наблюдения разъезжались
    во времени тем сильнее, чем медленнее хост.

    Теперь:
      * структурный провайдер может отдать `snapshot()` — foreground и ui_tree за
        ОДИН вызов и ОДНО разрешение окна (WindowsDesktop это умеет);
      * структурная часть и скриншот снимаются параллельно.
    Одновременный старт также СОКРАЩАЕТ временной разброс между фрагментами —
    это ровно то, чего требует контракт визуального наблюдения.
    """
    def __init__(self,structured,screenshot,summarizer=None):
        self.structured=structured; self.screenshot=screenshot; self.summarizer=summarizer
    async def _structured(self):
        snapshot=getattr(self.structured,"snapshot",None)
        if snapshot is not None:
            fg,tree=await snapshot()
            return fg,tree
        fg=await self.structured.foreground()
        tree=await self.structured.ui_tree()
        return fg,tree
    async def probe(self,*,generation:int):
        """Дешёвая перепроверка применимости экрана (AT-03).

        Полное наблюдение дополнительно платит за PNG и за вызов summarizer, а
        подпись применимости считается только по foreground и ui_tree — на границе
        эффекта это чистая переплата. Здесь берём ровно структуру.
        """
        fg,tree=await self._structured()
        return Observation(new_id("obs"),time.time(),fg or {},"",tree,None,False,generation)

    async def observe(self,*,generation:int):
        (fg,tree),(ref,sensitive)=await asyncio.gather(self._structured(),self.screenshot.capture())
        if self.summarizer:
            summary=await self.summarizer(foreground=fg,ui_tree=tree,screenshot_ref=ref,sensitive=sensitive)
        else:
            summary=f"foreground={fg!r}; ui_tree={'available' if tree is not None else 'unavailable'}"
        return Observation(new_id("obs"),time.time(),fg or {},str(summary)[:12000],
                           tree,ref,bool(sensitive),generation)
