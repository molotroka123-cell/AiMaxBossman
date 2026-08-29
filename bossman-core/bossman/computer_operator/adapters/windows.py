from __future__ import annotations
import asyncio,platform
from ..models import ActionKind

class WindowsDesktop:
    name="windows"
    def __init__(self): self.is_windows=platform.system().lower()=="windows"
    def _req(self):
        if not self.is_windows: raise RuntimeError("Windows backend requires Windows")
    async def foreground(self):
        self._req()
        def f():
            try:
                from pywinauto import Desktop
                w=Desktop(backend="uia").get_active()
                return {"title":w.window_text(),"app":str(getattr(w.element_info,"name","") or ""),"handle":int(w.handle)}
            except Exception as e: return {"title":"","app":"","error":type(e).__name__}
        return await asyncio.to_thread(f)
    async def ui_tree(self):
        self._req()
        def f():
            try:
                from pywinauto import Desktop
                w=Desktop(backend="uia").get_active()
                out=[]
                for c in w.descendants()[:500]:
                    e=c.element_info
                    out.append({"name":str(getattr(e,"name","") or "")[:300],
                                "control_type":str(getattr(e,"control_type","") or "")[:80],
                                "automation_id":str(getattr(e,"automation_id","") or "")[:200]})
                return {"elements":out}
            except Exception: return None
        return await asyncio.to_thread(f)
    async def supports(self,a,o):
        return self.is_windows and a.kind in {ActionKind.FOCUS,ActionKind.CLICK,ActionKind.DOUBLE_CLICK,
          ActionKind.TYPE,ActionKind.HOTKEY,ActionKind.SCROLL,ActionKind.DRAG,ActionKind.UI_INVOKE}
    async def execute(self,a,o):
        self._req()
        if a.kind in {ActionKind.FOCUS,ActionKind.UI_INVOKE} and a.target and await self._uia(a): return
        await self._input(a)
    async def _uia(self,a):
        def f():
            try:
                from pywinauto import Desktop
                w=Desktop(backend="uia").get_active()
                cands=w.descendants(title=a.target)
                if not cands: return False
                c=cands[0]
                if a.kind is ActionKind.FOCUS: c.set_focus()
                else:
                    try: c.invoke()
                    except Exception: c.click_input()
                return True
            except Exception: return False
        return await asyncio.to_thread(f)
    async def _input(self,a):
        def f():
            try: import pyautogui
            except ImportError as e: raise RuntimeError("pyautogui missing") from e
            pyautogui.FAILSAFE=True
            if a.kind is ActionKind.CLICK: pyautogui.click(*self._xy(a))
            elif a.kind is ActionKind.DOUBLE_CLICK: pyautogui.doubleClick(*self._xy(a))
            elif a.kind is ActionKind.TYPE: pyautogui.write(a.text or "",interval=min(.2,max(0,float(a.args.get("interval",.01)))))
            elif a.kind is ActionKind.HOTKEY:
                keys=[str(x) for x in a.args.get("keys",[])][:8]
                if not keys: raise ValueError("keys required")
                pyautogui.hotkey(*keys)
            elif a.kind is ActionKind.SCROLL: pyautogui.scroll(int(a.args.get("clicks",0)))
            elif a.kind is ActionKind.DRAG: pyautogui.dragTo(*self._xy(a),duration=min(5,max(0,float(a.args.get("duration",.5)))))
            else: raise RuntimeError("unsupported input")
        await asyncio.to_thread(f)
    @staticmethod
    def _xy(a):
        x,y=a.args.get("x"),a.args.get("y")
        if not isinstance(x,int) or not isinstance(y,int): raise ValueError("integer x/y required")
        if not (-10000<=x<=100000 and -10000<=y<=100000): raise ValueError("coordinate bounds")
        return x,y
