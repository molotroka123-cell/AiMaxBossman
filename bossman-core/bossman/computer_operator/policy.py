from __future__ import annotations
import inspect
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
# Защищённая поверхность Bossman — это ОКНА и ЦЕЛИ, а не слова в тексте.
#
# До 2026-09-21 одна чёрная метка ("bossman"/"approve"/…) проверялась сразу по
# target, по ВВОДИМОМУ ТЕКСТУ и по semantic. Живой прогон показал цену:
# агент не мог напечатать в Блокноте путь `…\Bossman\…` или обычную фразу
# со словом «approve», хотя ни то ни другое не касается ни одной кнопки
# подтверждения. При этом та же проверка НЕ видела настоящую поверхность —
# окно Command Center, UAC, диалог учётных данных, — если модель назвала цель
# нейтрально («кнопка справа»).
#
# Теперь три слоя, каждый с отрицательным контролем в тестах:
#   1. PROTECTED_WINDOW_TOKENS — окно переднего плана из НАБЛЮДЕНИЯ (не из
#      слов модели). Любой ввод в такое окно запрещён: Bossman/Command Center
#      (самоодобрение через собственный UI), UAC/Windows Security/учётные
#      данные (повышение прав и секреты). Нет наблюдения — идентичность окна
#      неизвестна, и чувствительные цели ниже отказываются fail-closed.
#   2. SENSITIVE_TARGET_TOKENS — имя ЭЛЕМЕНТА-цели: «Approve», «Подтвердить»,
#      «Продолжить»/«Resume», «Emergency». Разрешено только когда окно
#      переднего плана известно И не защищено (например, «Продолжить» в
#      установщике). При неизвестной идентичности — отказ.
#   3. Слово «bossman» в имени цели или в semantic — всегда отказ: модель сама
#      называет поверхность Bossman целью действия.
# Вводимый текст (a.text) — содержимое документа владельца; он проверяется
# ТОЛЬКО когда идентичность окна неизвестна (fail-closed) или окно защищено.
PROTECTED_WINDOW_TOKENS=("bossman","command center","user account control",
 "контроль учетных записей","контроль учётных записей","windows security",
 "безопасность windows","credential manager","диспетчер учетных данных",
 "диспетчер учётных данных","bcc-desktop")
SENSITIVE_TARGET_TOKENS=("approve","approval","одобр","подтвердить","confirm action",
 "emergency","unlock","разблокир","resume","продолжить")
# Историческое имя: совместимость для внешних импортов. Семантика — см. выше.
BOSSMAN_SURFACE_TOKENS=("bossman",)+SENSITIVE_TARGET_TOKENS
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
    def foreground_identity(observation:Observation|None)->str|None:
        """Заголовок/приложение окна переднего плана из наблюдения, либо None
        (наблюдения нет или окно не названо) — «идентичность неизвестна»."""
        fg=getattr(observation,"foreground",None)
        if not isinstance(fg,dict):return None
        parts=[str(fg.get(k) or "") for k in FOREGROUND_FIELDS]
        text=" ".join(p for p in parts if p).strip().lower()
        return text or None
    @classmethod
    def protected_window(cls,observation:Observation|None)->bool:
        ident=cls.foreground_identity(observation)
        return bool(ident) and any(tok in ident for tok in PROTECTED_WINDOW_TOKENS)
    @classmethod
    def touches_bossman_surface(cls,a:ComputerAction,observation:Observation|None=None)->bool:
        """Действие касается защищённой поверхности (см. комментарий к токенам).

        Вход (без наблюдения) — не «всё разрешено», а «идентичность окна
        неизвестна»: чувствительная цель или чувствительный текст в неизвестном
        окне отвергаются. Обычный текст («hello world», путь к файлу без
        чувствительных слов) в неизвестном окне не отвергается — это и есть
        отделение содержимого от поверхности."""
        target=(a.target or "").lower()
        semantic=str(a.args.get("semantic") or "").lower()
        if "bossman" in target or "bossman" in semantic:return True
        # semantic — заявление модели о СВОЁМ намерении: «approve yourself»,
        # «emergency unlock» называют поверхность целью и отвергаются всегда.
        if any(tok in semantic for tok in SENSITIVE_TARGET_TOKENS):return True
        input_kinds={ActionKind.CLICK,ActionKind.DOUBLE_CLICK,ActionKind.UI_INVOKE,ActionKind.DRAG,
                     ActionKind.HOTKEY,ActionKind.TYPE,ActionKind.FOCUS,ActionKind.SCROLL}
        if a.kind in input_kinds and cls.protected_window(observation):return True
        identity_known=cls.foreground_identity(observation) is not None
        if any(tok in target for tok in SENSITIVE_TARGET_TOKENS):
            return not identity_known
        if a.kind is ActionKind.TYPE and not identity_known:
            text=(a.text or "").lower()
            if any(tok in text for tok in SENSITIVE_TARGET_TOKENS):return True
        return False
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
    @classmethod
    def ask_consequence(cls,args:dict)->str|None:
        """Последствие, которое effect-hook МОЖЕТ вынести владельцу на
        подтверждение из одного заявленного действия: объявленный semantic ЛИБО
        подпись названной цели/текста. Последствие, видимое лишь по переднему
        окну, сюда НЕ входит — оно ловится на границе эффекта (classify +
        переобзор), fail closed."""
        c=cls.declared_consequence(args)
        if c:return c
        blob=" ".join(str((args or {}).get(k) or "") for k in ("target","text")).lower().strip()
        if blob:
            for kind,rx in _LEXICON_RX:
                if rx.search(blob):return kind
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
        if self.touches_bossman_surface(a,observation):
            return PolicyDecision(False,reason="bossman security surface is not a desktop target")
        if mode is TaskMode.OBSERVE_ONLY and a.kind not in {
            ActionKind.NOOP,ActionKind.WAIT,ActionKind.TAKE_SCREENSHOT,ActionKind.COMPLETE,ActionKind.FAIL
        }:
            return PolicyDecision(False,reason="observe-only mode")
        if self._coordinate_guess(a):
            # CONTROL-001: raw x/y must never become the planner's lazy default.
            # A coordinate is accepted only as the final resolution of a named
            # target: either the visual grounding adapter marked source=vision,
            # or an explicit bounded coordinate fallback. The semantic name is
            # retained for audit/verification and the manager re-observes both
            # before and after the effect.
            args=a.args or {}
            if not (a.target or "").strip():
                return PolicyDecision(False,reason="coordinate fallback requires a named target")
            explicit_fallback=bool(args.get("coordinate_fallback"))
            if a.source!="vision" and not explicit_fallback:
                return PolicyDecision(False,reason="raw coordinates are not a primary target; use semantic/accessibility/visual resolution first")
            if a.confidence<MIN_VISION_CONFIDENCE:
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


# ---------------------------------------------------------------- A2-03 / A2-04
# Каноническая авторизация управления компьютером НА ГРАНИЦЕ ЭФФЕКТА.
#
# `access_check` исторически звался ровно один раз — в create_task. Между
# созданием строки и отправкой ввода проходит план модели, ожидание
# подтверждения, пауза, перезапуск процесса. Разрешение, истинное при создании,
# к моменту эффекта может быть уже снято, и «проверено при создании» не
# означает «разрешено сейчас». Отказ обязан случиться ДО адаптера.
def authorize_computer_control(access_check,owner_device_id,source="local")->None:
    """Спросить текущее разрешение. Бросает PermissionError, если его нет.

    Fail-CLOSED: любая НЕОЖИДАННАЯ ошибка источника авторизации — это отказ, а
    не разрешение. Иначе достаточно уронить профильный gate, чтобы получить
    рабочий стол. `access_check is None` — гейт не сконфигурирован вовсе
    (как и раньше в manager.create_task): поведение не меняем.
    Совместимость со старыми одно-аргументными колбэками сохранена, но
    повторная попытка делается ТОЛЬКО если сам вызов не принял второй аргумент.
    """
    if access_check is None:return
    try:
        _invoke_gate(access_check,owner_device_id,source)
    except PermissionError:
        raise
    except Exception as exc:  # noqa: BLE001 — неизвестный источник = отказ
        raise PermissionError(
            f"computer control authorization unavailable ({type(exc).__name__}: {exc})") from exc


def _invoke_gate(access_check,owner_device_id,source):
    """Позвать гейт в той форме, которую он объявляет своей подписью.

    A2-04: раньше форма выяснялась через `except TypeError` — и он ловил ЛЮБОЙ
    TypeError, включая поднятый ВНУТРИ гейта на боевом пути. Проверка тогда
    переспрашивалась БЕЗ источника, а источник по умолчанию "local": внутренняя
    ошибка сервиса профилей молча повышала удалённый вход до локального.
    Одноаргументный повтор допустим ТОЛЬКО когда вызов не вошёл в тело функции —
    у такого TypeError нет следующего кадра трассировки.
    """
    try:
        takes_source=len(inspect.signature(access_check).parameters)>=2
    except (TypeError,ValueError):
        takes_source=None
    if takes_source is True:
        access_check(owner_device_id,source);return
    if takes_source is False:
        access_check(owner_device_id);return
    try:
        access_check(owner_device_id,source)
    except TypeError as exc:
        if exc.__traceback__ is not None and exc.__traceback__.tb_next is not None:
            raise
        access_check(owner_device_id)
