from __future__ import annotations
import json
from dataclasses import dataclass
from .models import ComputerAction,Observation,ActionKind,expected_value

@dataclass(slots=True,frozen=True)
class Verification:
    ok:bool; reason:str

def screen_text(after:Observation)->str:
    """Текст экрана, по которому проверяются contains_text/absent_text.

    Не только `summary`: в проде Observer собирается БЕЗ summarizer
    (subsystem.build_manager), и summary там — это repr переднего окна, то есть
    заголовок. Осмысленное постусловие по содержимому ("Report saved") не могло
    совпасть ни с чем и валило верификацию корректно исполненного шага, выжигая
    replan-бюджет до FAILED. `ui_tree` — это подписи элементов того же экрана,
    снятые тем же наблюдением: для absent_text расширение haystack к тому же
    строже, а не мягче.
    """
    tree=getattr(after,"ui_tree",None)
    blob=(after.summary or "")
    if tree is not None:
        # Дерево приходит от стороннего провайдера: непредставимая структура не
        # должна ронять верификацию — это уронило бы шаг, а не проверку.
        try: text=json.dumps(tree,ensure_ascii=False,default=str)
        except (TypeError,ValueError): text=repr(tree)
        blob=f"{blob}\n{text}"
    return blob.lower()

class Verifier:
    def verify(self,a:ComputerAction,after:Observation)->Verification:
        # AT-01: COMPLETE здесь не привилегирован. Заявление планировщика «цель
        # достигнута» обязано пройти те же проверки постусловия, что и любое
        # другое действие; без постусловия это «mutating action missing
        # postcondition», с ложным постусловием — «postcondition failed».
        e=a.expected
        if e.is_empty():
            if a.kind in {ActionKind.WAIT,ActionKind.NOOP,ActionKind.TAKE_SCREENSHOT}:
                return Verification(True,"non-mutating")
            return Verification(False,"mutating action missing postcondition")
        blob=screen_text(after)
        title=str(after.foreground.get("title","")).lower()
        app=str(after.foreground.get("app","")).lower()
        url=str(after.foreground.get("url","")).lower()
        checks=[]
        # expected_value отсеивает вырожденные поля (" ", "a"): такое поле
        # совпадает с любым экраном и молча делало проверку тождественно истинной.
        contains=expected_value(e.contains_text)
        absent=expected_value(e.absent_text)
        window=expected_value(e.window_title_contains)
        foreground=expected_value(e.foreground_app_contains)
        location=expected_value(e.url_contains)
        if contains: checks.append(contains.lower() in blob)
        if absent: checks.append(absent.lower() not in blob)
        if window: checks.append(window.lower() in title)
        if foreground: checks.append(foreground.lower() in app)
        if location: checks.append(location.lower() in url)
        return Verification(all(checks),"verified" if all(checks) else "postcondition failed")
