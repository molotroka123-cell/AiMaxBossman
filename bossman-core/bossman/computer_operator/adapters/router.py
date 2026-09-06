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
        for b in self.backends:
            if await b.supports(action,observation):
                await b.execute(action,observation)
                return b.name
        raise RuntimeError(f"no backend supports {action.kind.value}")
