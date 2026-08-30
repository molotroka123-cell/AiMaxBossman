from __future__ import annotations
from dataclasses import dataclass
from urllib.parse import urlparse
from .applist import canonical_app
from .models import ActionKind,ComputerAction,TaskMode

CONSEQUENTIAL=frozenset({
 "submit","send","pay","purchase","transfer","delete","uninstall","git_push","merge",
 "release","deploy","security_change","external_upload","secret_entry","account_change"
})
MIN_VISION_CONFIDENCE=.72
MAX_TYPE_CHARS=20000
BOSSMAN_SURFACE_TOKENS=("bossman","approval","approve","confirm action","emergency")
SECRET_REF_TOKENS=("credential","secret")

@dataclass(slots=True,frozen=True)
class PolicyDecision:
    allow:bool; requires_approval:bool=False; reason:str=""; approval_kind:str|None=None

class ComputerPolicy:
    @staticmethod
    def _surface_text(a:ComputerAction)->str:
        return " ".join(x for x in (a.target,a.text,str(a.args.get("semantic") or "")) if x).lower()
    @classmethod
    def touches_bossman_surface(cls,a:ComputerAction)->bool:
        hay=cls._surface_text(a)
        return any(tok in hay for tok in BOSSMAN_SURFACE_TOKENS)
    @staticmethod
    def refs_secret_args(args:dict)->bool:
        for k,v in (args or {}).items():
            blob=f"{k} {v}".lower()
            if any(tok in blob for tok in SECRET_REF_TOKENS):return True
        return False
    def classify(self,a:ComputerAction,*,mode:TaskMode,locked:bool=False)->PolicyDecision:
        if locked: return PolicyDecision(False,reason="operator locked")
        if self.touches_bossman_surface(a):
            return PolicyDecision(False,reason="bossman security surface is not a desktop target")
        if mode is TaskMode.OBSERVE_ONLY and a.kind not in {
            ActionKind.NOOP,ActionKind.WAIT,ActionKind.TAKE_SCREENSHOT,ActionKind.COMPLETE,ActionKind.FAIL
        }:
            return PolicyDecision(False,reason="observe-only mode")
        if a.source=="vision" and a.kind in {ActionKind.CLICK,ActionKind.DOUBLE_CLICK,ActionKind.DRAG,ActionKind.UI_INVOKE} and a.confidence<MIN_VISION_CONFIDENCE:
            return PolicyDecision(False,reason="low vision confidence")
        if a.kind is ActionKind.TYPE and len(a.text or "")>MAX_TYPE_CHARS:
            return PolicyDecision(False,reason="typed text too long")
        if a.kind is ActionKind.TYPE and self.refs_secret_args(a.args):
            return PolicyDecision(True,True,"credential entry requires approval","computer_secret_entry")
        # APP_LAUNCH — deny-by-default: запускается только логическое имя из
        # allowlist. Путь/аргументы/подстановки от модели отсекаются здесь, до
        # роутера, чтобы запуск приложения не превратился в произвольный exec.
        if a.kind is ActionKind.APP_LAUNCH and canonical_app(a.target) is None:
            return PolicyDecision(False,reason="app is not in launch allowlist")
        semantic=str(a.args.get("semantic","")).lower().strip()
        if semantic in CONSEQUENTIAL:
            return PolicyDecision(True,True,f"consequential:{semantic}",f"computer_{semantic}")
        if a.kind is ActionKind.BROWSER and a.args.get("op")=="navigate":
            u=urlparse(str(a.args.get("url","")))
            if u.scheme not in {"http","https"}:
                return PolicyDecision(False,reason="unsupported URL scheme")
        return PolicyDecision(True)
