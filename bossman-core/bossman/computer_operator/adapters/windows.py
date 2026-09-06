from __future__ import annotations
import asyncio,platform
from ..models import ActionKind

# Верхняя граница снимка UI-дерева. `descendants()` МАТЕРИАЛИЗУЕТ всё поддерево
# окна, и только потом вызывающий его обрезал: на окне браузера это тысячи
# межпроцессных COM-обращений ради 500 узлов, которые реально используются.
MAX_TREE_NODES=500
MAX_TREE_DEPTH=12


class WindowsDesktop:
    name="windows"
    def __init__(self): self.is_windows=platform.system().lower()=="windows"
    def _req(self):
        if not self.is_windows: raise RuntimeError("Windows backend requires Windows")
    @staticmethod
    def _active_window():
        # pywinauto 0.6.x lacks an "active window" helper; resolve the OS
        # foreground window handle via user32 and wrap it with the uia backend.
        import ctypes
        from pywinauto import Desktop
        hwnd=ctypes.windll.user32.GetForegroundWindow()
        if hwnd:
            w=Desktop(backend="uia").window(handle=hwnd)
            if w is not None: return w
        return Desktop(backend="uia").top_window()
    async def foreground(self):
        self._req()
        def f():
            try:
                w=self._active_window()
                return {"title":w.window_text(),"app":str(getattr(w.element_info,"name","") or ""),"handle":int(w.handle)}
            except Exception as e: return {"title":"","app":"","error":type(e).__name__}
        return await asyncio.to_thread(f)
    @staticmethod
    def _element(c):
        e=c.element_info
        return {"name":str(getattr(e,"name","") or "")[:300],
                "control_type":str(getattr(e,"control_type","") or "")[:80],
                "automation_id":str(getattr(e,"automation_id","") or "")[:200]}

    @classmethod
    def _walk(cls,w,limit=MAX_TREE_NODES,max_depth=MAX_TREE_DEPTH):
        """Ограниченный обход в ширину: останавливается НА пределе, а не после него.

        Ширина даёт самые значимые для планирования (верхние) узлы; глубокий
        хвост, который всё равно отбрасывался срезом, больше не оплачивается.
        Провайдер без `children()` честно откатывается на `descendants()`.
        """
        children=getattr(w,"children",None)
        if children is None:
            return [cls._element(c) for c in w.descendants()[:limit]]
        out=[]; frontier=[(w,0)]
        while frontier and len(out)<limit:
            node,depth=frontier.pop(0)
            if depth>=max_depth: continue
            try: kids=node.children()
            except Exception: continue
            for c in kids:
                if len(out)>=limit: break
                out.append(cls._element(c)); frontier.append((c,depth+1))
        return out

    async def ui_tree(self):
        self._req()
        def f():
            try: return {"elements":self._walk(self._active_window())}
            except Exception: return None
        return await asyncio.to_thread(f)

    async def snapshot(self):
        """foreground + ui_tree за ОДИН поток и ОДНО разрешение окна.

        Раздельные вызовы разрешали окно переднего плана дважды (GetForegroundWindow
        + оболочка UIA Desktop — самая дорогая часть наблюдения) и наблюдали в
        два разных момента времени. Здесь оба фрагмента описывают одно окно.
        """
        self._req()
        def f():
            try: w=self._active_window()
            except Exception as e: return {"title":"","app":"","error":type(e).__name__},None
            try:
                fg={"title":w.window_text(),"app":str(getattr(w.element_info,"name","") or ""),
                    "handle":int(w.handle)}
            except Exception as e:
                fg={"title":"","app":"","error":type(e).__name__}
            try: tree={"elements":self._walk(w)}
            except Exception: tree=None
            return fg,tree
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
                w=self._active_window()
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
