from __future__ import annotations
from dataclasses import dataclass
from .models import ComputerAction,Observation,ActionKind

@dataclass(slots=True,frozen=True)
class Verification:
    ok:bool; reason:str

class Verifier:
    def verify(self,a:ComputerAction,after:Observation)->Verification:
        if a.kind is ActionKind.COMPLETE: return Verification(True,"planner completed")
        e=a.expected
        if e.is_empty():
            if a.kind in {ActionKind.WAIT,ActionKind.NOOP,ActionKind.TAKE_SCREENSHOT}:
                return Verification(True,"non-mutating")
            return Verification(False,"mutating action missing postcondition")
        blob=(after.summary or "").lower()
        title=str(after.foreground.get("title","")).lower()
        app=str(after.foreground.get("app","")).lower()
        url=str(after.foreground.get("url","")).lower()
        checks=[]
        if e.contains_text: checks.append(e.contains_text.lower() in blob)
        if e.absent_text: checks.append(e.absent_text.lower() not in blob)
        if e.window_title_contains: checks.append(e.window_title_contains.lower() in title)
        if e.foreground_app_contains: checks.append(e.foreground_app_contains.lower() in app)
        if e.url_contains: checks.append(e.url_contains.lower() in url)
        return Verification(all(checks),"verified" if all(checks) else "postcondition failed")
