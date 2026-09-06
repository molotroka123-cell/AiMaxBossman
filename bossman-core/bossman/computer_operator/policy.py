from __future__ import annotations
import re
from dataclasses import dataclass
from urllib.parse import urlparse
from .applist import canonical_app
from .models import ActionKind,ComputerAction,Observation,TaskMode

CONSEQUENTIAL=frozenset({
 "submit","send","pay","purchase","transfer","delete","uninstall","git_push","merge",
 "release","deploy","security_change","external_upload","secret_entry","account_change"
})
MIN_VISION_CONFIDENCE=.72
MAX_TYPE_CHARS=20000
BOSSMAN_SURFACE_TOKENS=("bossman","approval","approve","confirm action","emergency")
SECRET_REF_TOKENS=("credential","secret")

# Лексикон последствий. Ищется в УЛИКАХ НАБЛЮДЕНИЯ — подписи элемента, по
# которому идёт действие (для source="vision" target это распознанный текст
# кнопки), и в приложении/заголовке окна переднего плана из Observation.
# Ни одна из этих улик не является полем args["semantic"]: раньше именно оно
# было ЕДИНСТВЕННЫМ источником решения «нужно подтверждение», то есть модель
# одним пропущенным словом снимала человека с оплаты/деплоя/удаления.
# Ключ = вид последствия из CONSEQUENTIAL, он же уходит в approval_kind,
# чтобы владелец видел, ЧТО именно подтверждает.
CONSEQUENCE_LEXICON=(
 ("account_change",("change password","смена пароля","сменить пароль","change email",
   "смена почты","delete account","удалить аккаунт","удаление аккаунта","close account",
   "two-factor","двухфактор","2fa")),
 ("security_change",("firewall","брандмауэр","антивирус","antivirus","defender",
   "disable protection","отключить защиту","gatekeeper","setenforce","права доступа")),
 ("pay",("pay","оплат","заплат","purchase","checkout","buy now","купить","оформить заказ",
   "invoice","счёт на оплату","billing")),
 ("transfer",("transfer","перевод","перевести","wire transfer","сбербанк","sberbank",
   "тинькофф","tinkoff","онлайн-банк","online bank","paypal")),
 ("uninstall",("uninstall","деинсталл","удалить программу","remove program")),
 ("deploy",("deploy","деплой","выкат","release","релиз","публикация","publish","опубликовать")),
 ("git_push",("git push","force push","push to origin","запушить")),
 ("merge",("merge request","merge pull","merge branch","слить ветку")),
 # Только ЯВНАЯ отправка наружу. Голое «Upload» — обычная подпись у любого
 # выбора файла (в т.ч. локального) и в лексикон не годится: приложение с
 # такой кнопкой заставляло бы владельца подтверждать каждый шаг.
 ("external_upload",("share publicly","make public","опубликовать публично",
   "выложить в интернет","upload to cloud","отправить наружу")),
 ("delete",("delete","удал","erase","стереть","wipe","форматир","drop database","drop table")),
)
# (?<!\w) — токен обязан начинать слово: "pay" ловит "Pay"/"payment", но не
# "display"/"repay"; кириллические токены заодно работают как префиксы
# («удал» → «удалить», «удаление»).
_LEXICON_RX=tuple((kind,re.compile(r"(?<!\w)(?:"+"|".join(re.escape(t) for t in toks)+r")",re.I))
                  for kind,toks in CONSEQUENCE_LEXICON)
# Виды, для которых улики вообще осмысленны: NOOP/WAIT/скриншот ничего не
# меняют, и подпись рядом с ними не повод будить владельца.
CONSEQUENCE_EVIDENCE_KINDS=frozenset({
 ActionKind.CLICK,ActionKind.DOUBLE_CLICK,ActionKind.UI_INVOKE,ActionKind.DRAG,
 ActionKind.HOTKEY,ActionKind.APP_CLOSE,ActionKind.BROWSER,ActionKind.TYPE
})
# Поля Observation.foreground, в которых наблюдатель называет приложение/окно.
FOREGROUND_FIELDS=("app","name","process","title","window_title")

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
    def _observed_text(a:ComputerAction,observation:Observation|None)->str:
        """Улики, которых у модели нет: подпись цели и переднее приложение.

        Вводимый текст (a.text) сюда НЕ входит: это содержимое, а не подпись
        элемента, и слова из чужого документа/страницы не должны сами по себе
        поднимать approval — иначе инъекция в текст управляет тем, как часто
        владельца дёргают (A4)."""
        parts=[a.target or ""]
        fg=getattr(observation,"foreground",None)
        if isinstance(fg,dict):
            parts+=[str(fg.get(k) or "") for k in FOREGROUND_FIELDS]
        return " ".join(p for p in parts if p).lower()
    @classmethod
    def consequence_of(cls,a:ComputerAction,observation:Observation|None=None)->str|None:
        """Вид последствия по уликам наблюдения. args["semantic"] не читается."""
        if a.kind not in CONSEQUENCE_EVIDENCE_KINDS:return None
        hay=cls._observed_text(a,observation)
        if not hay:return None
        for kind,rx in _LEXICON_RX:
            if rx.search(hay):return kind
        return None
    @staticmethod
    def declared_consequence(args:dict)->str|None:
        """Что о последствии сказала САМА модель — только для ЭСКАЛАЦИИ.

        Значение может добавить подтверждение (модель честно назвала «pay»),
        но никогда его не снимает: отсутствующее, пустое или «пониженное»
        (`click`, `noop`) значение просто ничего не добавляет — решение по
        уликам остаётся в силе (fail closed)."""
        s=str((args or {}).get("semantic","")).lower().strip()
        if not s:return None
        if s in CONSEQUENTIAL:return s
        for kind,rx in _LEXICON_RX:
            if rx.search(s):return kind
        return None
    @staticmethod
    def _coordinate_guess(a:ComputerAction)->bool:
        """Тычок в координату, порог уверенности к которому обязан применяться.

        Ключ на СТРУКТУРЕ действия, а не на самозаявленном `a.source`: source
        приходил из вывода модели (planner.parse_action), и модель снимала с себя
        порог одним словом "planner", продолжая слать CLICK по пиксельным x/y.
        source=="vision" остаётся вторым признаком: у vision-адаптера цель — это
        распознанный текст, координат в args может и не быть.
        """
        if a.kind not in {ActionKind.CLICK,ActionKind.DOUBLE_CLICK,ActionKind.DRAG,ActionKind.UI_INVOKE}:
            return False
        if a.source=="vision":return True
        args=a.args or {}
        return isinstance(args.get("x"),int) and isinstance(args.get("y"),int)
    @staticmethod
    def refs_secret_args(args:dict)->bool:
        for k,v in (args or {}).items():
            blob=f"{k} {v}".lower()
            if any(tok in blob for tok in SECRET_REF_TOKENS):return True
        return False
    def classify(self,a:ComputerAction,*,mode:TaskMode,locked:bool=False,
                 observation:Observation|None=None)->PolicyDecision:
        if locked: return PolicyDecision(False,reason="operator locked")
        if self.touches_bossman_surface(a):
            return PolicyDecision(False,reason="bossman security surface is not a desktop target")
        if mode is TaskMode.OBSERVE_ONLY and a.kind not in {
            ActionKind.NOOP,ActionKind.WAIT,ActionKind.TAKE_SCREENSHOT,ActionKind.COMPLETE,ActionKind.FAIL
        }:
            return PolicyDecision(False,reason="observe-only mode")
        if self._coordinate_guess(a) and a.confidence<MIN_VISION_CONFIDENCE:
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
        # Последствие сначала по уликам (наблюдаемая подпись цели/OCR, вид
        # действия, приложение переднего плана), и только потом — по тому, что
        # заявила модель. Порядок важен: заявление модели может ДОБАВИТЬ
        # подтверждение, но не отменить выведенное из улик.
        observed=self.consequence_of(a,observation)
        semantic=observed or self.declared_consequence(a.args)
        if semantic:
            return PolicyDecision(True,True,
                f"consequential:{semantic}"+(" (observed)" if observed else ""),
                f"computer_{semantic}")
        if a.kind is ActionKind.BROWSER and a.args.get("op")=="navigate":
            u=urlparse(str(a.args.get("url","")))
            if u.scheme not in {"http","https"}:
                return PolicyDecision(False,reason="unsupported URL scheme")
        return PolicyDecision(True)
