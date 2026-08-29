class ActionRouter:
    def __init__(self,backends): self.backends=list(backends)
    async def execute(self,action,observation):
        for b in self.backends:
            if await b.supports(action,observation):
                await b.execute(action,observation)
                return b.name
        raise RuntimeError(f"no backend supports {action.kind.value}")
