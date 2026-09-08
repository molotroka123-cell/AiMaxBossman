class ActionRouter:
    def __init__(self,backends): self.backends=list(backends)
    def set_interrupt(self,event):
        """Раздать бэкендам кооперативный флаг отмены (A3-02).

        `asyncio.to_thread` неотменяем в принципе: отмена корутины роняет только
        ожидание, а поток, который жмёт клавиши, продолжает жить. Единственная
        настоящая отмена здесь — кооперативная, поэтому флаг передаётся тем
        бэкендам, которые умеют его читать; остальные его просто не получают.
        """
        for b in self.backends:
            setter=getattr(b,"set_interrupt",None)
            if setter is not None:setter(event)
    async def execute(self,action,observation):
        failed=[]
        for b in self.backends:
            # Упавшая проба одного бэкенда не должна отменять остальные: раньше
            # исключение из supports() поднималось наружу, и действие не пробовал
            # НИ ОДИН из следующих бэкендов, а причина нигде не называлась (A3-10).
            try: ok=await b.supports(action,observation)
            except Exception as e:
                failed.append(f"{b.name}: {type(e).__name__}: {e}"); continue
            if ok:
                await b.execute(action,observation)
                return b.name
        why=f"no backend supports {action.kind.value}"
        raise RuntimeError(f"{why} (probe failures: {'; '.join(failed)})" if failed else why)
