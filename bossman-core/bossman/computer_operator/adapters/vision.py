from ..models import ActionKind
class VisionInputAdapter:
    name="vision-input"
    def __init__(self,desktop): self.desktop=desktop
    async def supports(self,a,o):
        return a.source=="vision" and a.kind in {ActionKind.CLICK,ActionKind.DOUBLE_CLICK,
            ActionKind.DRAG,ActionKind.TYPE,ActionKind.HOTKEY,ActionKind.SCROLL}
    async def execute(self,a,o): await self.desktop.execute(a,o)
