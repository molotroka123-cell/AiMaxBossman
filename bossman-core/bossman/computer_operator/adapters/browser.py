from ..models import ActionKind
class ExistingBrowserAdapter:
    name="browser"
    def __init__(self,dispatch): self.dispatch=dispatch
    async def supports(self,a,o): return a.kind is ActionKind.BROWSER
    async def execute(self,a,o): await self.dispatch(a,o)
