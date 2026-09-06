"""Мини-DOM визуального веб-дизайнера: парсинг HTML, точечные правки, инжект пикера.

Задача модуля — дать дизайнеру возможность менять ОДИН элемент живой страницы
на сервере, без браузера и без внешних зависимостей (только stdlib
html.parser). Три способности, на которых держится всё остальное:

* разбор документа в дерево и сборка обратно так, чтобы код оставался
  узнаваемым для владельца (текст, комментарии и doctype сохраняются дословно);
* детерминированная нумерация элементов `data-bd-id="bd-N"` в порядке
  обхода в глубину — один и тот же код даёт одни и те же номера, поэтому
  элемент, выбранный кликом в превью, находится на сервере в текущем коде;
* точечные операции над найденным элементом: текст, inline-стили, атрибуты,
  замена фрагмента, удаление.

Нумерация живёт ТОЛЬКО в отданном превью. Хранимый код владелец не должен
видеть замусоренным служебными атрибутами — они вносятся при отдаче и живут
ровно одну отдачу.

ГЛАВНОЕ СВОЙСТВО МОДУЛЯ (и причина, по которой он выглядит так, а не проще).
Панель применяет точечную правку через полный цикл `parse → serialize`: любая
неточность сборки — это не артефакт превью, а необратимая порча сайта
владельца. Поэтому узел помнит ДОСЛОВНЫЙ текст своего открывающего тега и
отдаёт его обратно байт в байт, пока сам не изменён. Регистр тега и атрибута
(`viewBox`, `linearGradient`), кавычки, порядок и пробелы внутри тега,
самозакрытие `<br/>` — всё это переживает правку соседнего элемента.
Пересобирается из полей ТОЛЬКО тот узел, который правку и получил.

Разбор терпим к тому, что html.parser молча выбрасывает: инструкции обработки
(`<?php ... ?>`), маркированные секции (`<![CDATA[...]]>`) и оборванная в конце
файла разметка сохраняются отдельными узлами и возвращаются как есть.

Обход и сборка ИТЕРАТИВНЫ: рекурсия падала на ~350 вложений (≈4 КБ), то есть
на документе, который панель обязана и показать, и сохранить.
"""
from __future__ import annotations

import re
from dataclasses import dataclass, field
from html import escape
from html.parser import HTMLParser

VOID_TAGS = frozenset({
    "area", "base", "br", "col", "embed", "hr", "img", "input",
    "link", "meta", "param", "source", "track", "wbr",
})

MAX_HTML_CHARS = 2_000_000

# Имя атрибута, которое панель готова ПОСТАВИТЬ по просьбе клиента. Всё, что
# шире, — это не атрибут, а инъекция: `x" onmouseover="alert(1)` при сборке
# закрывает кавычку и превращается в живой обработчик события, который уезжает
# в экспортированный сайт и там уже не в песочнице.
_SETTABLE_ATTR_NAME = re.compile(r"^[A-Za-z_:][-A-Za-z0-9_:.]*$")
# Имя, которое нельзя ВЫПУСТИТЬ в разметку ни при каких условиях: эти символы
# рвут тег. Вторая линия обороны — на случай имени, пришедшего не через op_set_attrs.
_UNSAFE_IN_ATTR_NAME = re.compile(r"""[\s"'<>=/`]""")
# Имя css-свойства: буквы, цифры, дефис, подчёркивание (в т.ч. `--custom`).
_SETTABLE_STYLE_PROP = re.compile(r"^-{0,2}[A-Za-z_][-A-Za-z0-9_]*$")

_TAG_NAME_RE = re.compile(r"<\s*([^\s/>]+)")

_FRAGMENT_WRAP = "bd-fragment-root"


# ---------------------------------------------------------------- дерево

@dataclass
class Node:
    """Узел дерева.

    kind: element | text | comment | doctype | pi | marked | raw

    * `pi` — инструкция обработки (`<?php ... ?>`, `<?xml ... ?>`);
    * `marked` — маркированная секция (`<![CDATA[...]]>`);
    * `raw` — хвост, который парсер не смог разобрать (оборванный тег в конце
      файла): отдаётся дословно, потому что это текст владельца.

    `raw_starttag` — дословный открывающий тег из исходника. Пока `dirty`
    ложно, сборка отдаёт именно его: это и есть гарантия, что правка одного
    элемента не переписывает остальной документ.
    """
    kind: str = "element"
    tag: str | None = None                  # только для element, в нижнем регистре
    attrs: dict[str, str | None] = field(default_factory=dict)
    raw: str = ""                           # дословное содержимое text/comment/doctype/pi/marked/raw
    children: list["Node"] = field(default_factory=list)
    parent: "Node | None" = field(default=None, repr=False, compare=False)
    raw_starttag: str = ""                  # дословный открывающий тег
    tag_case: str = ""                      # написание тега в исходнике
    attr_case: dict[str, str] = field(default_factory=dict)  # нижний регистр → написание
    self_closing: bool = False              # <br/> — отдаётся так же
    dirty: bool = False                     # узел правился: собирается из полей
    bd_id: str = ""                         # служебный номер; в attrs НЕ живёт

    def is_element(self, *tags: str) -> bool:
        return self.kind == "element" and (not tags or self.tag in tags)

    def touch(self) -> None:
        """Пометить узел как изменённый: дальше он собирается из полей."""
        self.dirty = True


def _spellings(raw_starttag: str) -> dict[str, str]:
    """Написание имён атрибутов в исходном теге: {нижний регистр: как написано}.

    html.parser отдаёт имена уже в нижнем регистре, а инлайновому SVG регистр
    важен (`viewBox`, `clipPathUnits`). Разбираем текст тега сами.
    """
    out: dict[str, str] = {}
    i = raw_starttag.find(" ")
    if i < 0:
        i = raw_starttag.find("\t")
    if i < 0:
        return out
    n = len(raw_starttag)
    if raw_starttag.endswith(">"):
        n -= 1
    space = " \t\r\n\f"
    while i < n:
        while i < n and raw_starttag[i] in space + "/":
            i += 1
        j = i
        while j < n and raw_starttag[j] not in space + "/=>":
            j += 1
        name = raw_starttag[i:j]
        if name:
            out.setdefault(name.lower(), name)
        i = j
        while i < n and raw_starttag[i] in space:
            i += 1
        if i < n and raw_starttag[i] == "=":
            i += 1
            while i < n and raw_starttag[i] in space:
                i += 1
            if i < n and raw_starttag[i] in "\"'":
                quote = raw_starttag[i]
                i += 1
                while i < n and raw_starttag[i] != quote:
                    i += 1
                i += 1
            else:
                while i < n and raw_starttag[i] not in space + ">":
                    i += 1
    return out


class _TreeBuilder(HTMLParser):
    """Терпимый сборщик дерева: незакрытые теги не роняют разбор."""

    def __init__(self) -> None:
        super().__init__(convert_charrefs=False)
        self.root = Node(kind="element", tag="#root")
        self._stack: list[Node] = [self.root]
        self.unmatched_end_tags: list[str] = []

    # -- служебное -----------------------------------------------------

    def _append(self, node: Node) -> None:
        node.parent = self._stack[-1]
        self._stack[-1].children.append(node)

    def _append_text(self, raw: str) -> None:
        if not raw:
            return
        last = self._stack[-1].children[-1] if self._stack[-1].children else None
        if last is not None and last.kind == "text":
            last.raw += raw
        else:
            self._append(Node(kind="text", raw=raw))

    def _element(self, tag: str, attrs) -> Node:
        raw = self.get_starttag_text() or ""
        match = _TAG_NAME_RE.match(raw)
        return Node(
            tag=tag.lower(),
            attrs={str(k).lower(): v for k, v in attrs},
            raw_starttag=raw,
            tag_case=(match.group(1) if match else tag),
            attr_case=_spellings(raw),
        )

    def absorb(self, leftover: str) -> None:
        """Хвост, который парсер не разобрал, — дословно в дерево, а не в мусор.

        Оборванный в конце файла тег (`<div class="x"`) html.parser выбрасывает
        молча. Для панели это чужой текст: потерять его при правке кегля нельзя.
        """
        if leftover:
            self._append(Node(kind="raw", raw=leftover))

    # -- HTMLParser ----------------------------------------------------

    def handle_starttag(self, tag, attrs):
        node = self._element(tag, attrs)
        self._append(node)
        if node.tag not in VOID_TAGS:
            self._stack.append(node)

    def handle_startendtag(self, tag, attrs):
        node = self._element(tag, attrs)
        node.self_closing = (node.raw_starttag or "").rstrip().endswith("/>")
        self._append(node)

    def handle_endtag(self, tag):
        tag = tag.lower()
        # ищем свой тег вверх по стеку; незакрытые попутные закрываем
        for i in range(len(self._stack) - 1, 0, -1):
            if self._stack[i].tag == tag:
                del self._stack[i:]
                return
        if tag not in VOID_TAGS:
            self.unmatched_end_tags.append(tag)

    def handle_data(self, data):
        self._append_text(data)

    def handle_entityref(self, name):
        self._append_text(f"&{name};")

    def handle_charref(self, name):
        self._append_text(f"&#{name};")

    def handle_comment(self, data):
        self._append(Node(kind="comment", raw=data))

    def handle_decl(self, decl):
        self._append(Node(kind="doctype", raw=decl))

    def handle_pi(self, data):
        # `<?php echo 1; ?>` → data == 'php echo 1; ?' → обратно один в один.
        self._append(Node(kind="pi", raw=data))

    def unknown_decl(self, data):
        # `<![CDATA[x]]>` → data == 'CDATA[x]' → обратно `<![` + data + `]]>`.
        self._append(Node(kind="marked", raw=data))


def _feed(builder: _TreeBuilder, html: str) -> None:
    """Скормить документ и подобрать неразобранный хвост.

    После `feed()` в `rawdata` остаётся ровно то, что парсер не смог признать
    завершённой конструкцией. `close()` такой хвост выбрасывает, поэтому мы
    забираем его раньше и кладём в дерево дословно.
    """
    builder.feed(html)
    leftover = ""
    try:
        leftover = builder.rawdata or ""
        builder.rawdata = ""
    except AttributeError:               # чужая реализация парсера — ведём себя как раньше
        leftover = ""
    builder.close()
    builder.absorb(leftover)


def parse_document(html: str) -> Node:
    """HTML-документ → корень (#root) с доктайпом, html и комментариями."""
    builder = _TreeBuilder()
    _feed(builder, html)
    return builder.root


def parse_fragment(html: str) -> list[Node]:
    """Фрагмент (внешний HTML элемента) → список узлов верхнего уровня.

    Обёртка с уникальным именем: `</div>` внутри фрагмента больше не закрывает
    её и не выбрасывает всё, что идёт следом. Несбалансированный фрагмент —
    честная ошибка, а не молчаливая потеря половины разметки.
    """
    builder = _TreeBuilder()
    _feed(builder, f"<{_FRAGMENT_WRAP}>{html}</{_FRAGMENT_WRAP}>")
    if builder.unmatched_end_tags:
        stray = builder.unmatched_end_tags[0]
        raise ValueError(
            f"фрагмент не сбалансирован: лишний закрывающий тег </{stray}> — "
            "он бы разрушил разметку вокруг элемента")
    wrapper = next((n for n in builder.root.children if n.is_element(_FRAGMENT_WRAP)), None)
    return list(wrapper.children) if wrapper is not None else []


# ---------------------------------------------------------------- сборка

def _start_tag(node: Node, *, bd_ids: bool) -> str:
    if node.raw_starttag and not node.dirty:
        raw = node.raw_starttag
        if bd_ids and node.bd_id:
            marker = f' data-bd-id="{node.bd_id}"'
            if raw.rstrip().endswith("/>"):
                cut = raw.rstrip()[:-2]
                raw = f"{cut}{marker}/>"
            elif raw.endswith(">"):
                raw = f"{raw[:-1]}{marker}>"
        return raw
    parts = []
    for key, value in node.attrs.items():
        name = node.attr_case.get(key, key)
        if not name or _UNSAFE_IN_ATTR_NAME.search(name):
            # имя, разрывающее тег, не выпускаем в разметку никогда
            continue
        parts.append(f" {name}" if value is None
                     else f' {name}="{escape(str(value), quote=True)}"')
    if bd_ids and node.bd_id:
        parts.append(f' data-bd-id="{node.bd_id}"')
    tag = node.tag_case or node.tag or "div"
    close = "/>" if node.self_closing else ">"
    return f"<{tag}{''.join(parts)}{close}"


def _end_tag(node: Node) -> str:
    if node.self_closing or node.tag in VOID_TAGS:
        return ""
    return f"</{node.tag_case or node.tag}>"


def serialize(node: Node, *, bd_ids: bool = False) -> str:
    """Дерево → HTML. Итеративно: глубокий документ не роняет панель.

    Неизменённый элемент отдаётся дословным исходным тегом, поэтому правка
    одного элемента не переписывает соседей.
    """
    out: list[str] = []
    stack: list[object] = [node]
    while stack:
        item = stack.pop()
        if isinstance(item, str):
            out.append(item)
            continue
        current: Node = item                      # type: ignore[assignment]
        kind = current.kind
        if kind == "text" or kind == "raw":
            out.append(current.raw)
            continue
        if kind == "comment":
            out.append(f"<!--{current.raw}-->")
            continue
        if kind == "doctype":
            out.append(f"<!{current.raw}>")
            continue
        if kind == "pi":
            out.append(f"<?{current.raw}>")
            continue
        if kind == "marked":
            out.append(f"<![{current.raw}]]>")
            continue
        if current.tag == "#root":                # служебный корень тегом не печатается
            stack.extend(reversed(current.children))
            continue
        out.append(_start_tag(current, bd_ids=bd_ids))
        end = _end_tag(current)
        if end:
            stack.append(end)
        stack.extend(reversed(current.children))
    return "".join(out)


# ---------------------------------------------------------------- обход

def walk_elements(root: Node):
    """Все элементы документа в порядке обхода в глубину (сам #root не считается).

    Итеративно: рекурсивная версия падала RecursionError на 350 вложений.
    """
    stack: list[Node] = [c for c in reversed(root.children) if c.kind == "element"]
    while stack:
        current = stack.pop()
        yield current
        for child in reversed(current.children):
            if child.kind == "element":
                stack.append(child)


def _find_body(root: Node) -> Node | None:
    return next((n for n in walk_elements(root) if n.tag == "body"), None)


# ---------------------------------------------------------------- нумерация и поиск

def assign_bd_ids(root: Node) -> int:
    """bd-N по всему документу, детерминированно. Возвращает количество.

    Номер живёт в поле узла, а НЕ в attrs: иначе нумерация помечала бы каждый
    элемент изменённым и правка кегля переписывала бы весь документ. Заодно
    собственный `data-bd-id` владельца остаётся нетронутым.
    """
    count = 0
    for element in walk_elements(root):
        count += 1
        element.bd_id = f"bd-{count}"
    return count


def find_by_bd_id(root: Node, bd_id: str) -> Node | None:
    return next((n for n in walk_elements(root) if n.bd_id == str(bd_id)), None)


def find_by_path(root: Node, path: str) -> Node | None:
    """Путь вида 'html > body > div:nth-of-type(2) > h1' или 'div#hero > p'.

    Короткий путь без '>' ('div', 'h1#title') ищется по всему документу —
    так человек и модель могут указать элемент одной строкой.
    """
    segments = [s.strip() for s in path.split(">") if s.strip()]
    if not segments:
        return None
    current: Node | None = None
    pool = root.children
    for i, segment in enumerate(segments):
        tag, nth, want_id = _parse_segment(segment)
        matches = [n for n in pool if n.is_element(tag)]
        if want_id:
            matches = [n for n in matches if n.attrs.get("id") == want_id]
        elif nth:
            matches = matches[nth - 1:nth] if nth >= 1 else []
        if not matches and i == 0:
            # короткий путь: первый сегмент ищем в глубину по всему документу
            for candidate in walk_elements(root):
                if candidate.is_element(tag) and (not want_id or candidate.attrs.get("id") == want_id):
                    current = candidate
                    pool = candidate.children
                    break
            else:
                return None
            continue
        if not matches:
            return None
        current = matches[0]
        pool = current.children
    return current


def _parse_segment(segment: str) -> tuple[str, int, str]:
    tag, nth, want_id = segment, 0, ""
    if "#" in segment:
        tag, want_id = segment.split("#", 1)
        want_id = want_id.split(".")[0].split(":")[0]
    if ":nth-of-type(" in tag:
        tag, _, rest = tag.partition(":nth-of-type(")
        digits = "".join(ch for ch in rest if ch.isdigit())
        nth = int(digits) if digits else 0
    tag = tag.split(".")[0].strip().lower()
    return tag, nth, want_id


def resolve_element(root: Node, bd_id: str | None, path: str | None) -> Node | None:
    if bd_id:
        found = find_by_bd_id(root, bd_id)
        if found is not None:
            return found
    if path:
        return find_by_path(root, path)
    return None


# ---------------------------------------------------------------- операции

def _parse_style(raw: str) -> dict[str, str]:
    style: dict[str, str] = {}
    for part in (raw or "").split(";"):
        if ":" not in part:
            continue
        prop, _, value = part.partition(":")
        prop, value = prop.strip().lower(), value.strip()
        if prop and value:
            style[prop] = value
    return style


def _dump_style(style: dict[str, str]) -> str:
    return "; ".join(f"{k}: {v}" for k, v in style.items())


def has_element_children(element: Node) -> bool:
    return any(child.kind == "element" for child in element.children)


def op_set_text(element: Node, text: str, *, replace_children: bool = False) -> None:
    """Заменить содержимое элемента одним текстовым узлом.

    Инспектор подставляет в поле «Текст» весь textContent поддерева, поэтому
    «Применить» без единой правки схлопывало `<section>` с сотней потомков в
    одну строку — и это уже было сохранено. Элемент с вложенными тегами
    очищается только по явному подтверждению.
    """
    if has_element_children(element) and not replace_children:
        raise ValueError(
            "внутри элемента есть вложенные теги — правка текста удалила бы их; "
            "выберите вложенный элемент или подтвердите замену содержимого")
    element.children = [Node(kind="text", raw=escape(text, quote=False), parent=element)]


def op_set_style(element: Node, props: dict[str, str]) -> None:
    style = _parse_style(str(element.attrs.get("style") or ""))
    for prop, value in (props or {}).items():
        name = str(prop).strip().lower()
        if not _SETTABLE_STYLE_PROP.match(name):
            raise ValueError(f"недопустимое имя css-свойства: {prop!r}")
        style[name] = str(value).strip()
    element.attrs["style"] = _dump_style(style)
    element.attr_case.pop("style", None)
    element.touch()


def op_set_attrs(element: Node, attrs: dict[str, str]) -> None:
    """Поставить атрибуты. Имя проверяется: имя — это не значение.

    Значения экранируются при сборке, а имена — нет и не могут быть: имя с
    кавычкой (`x" onmouseover="alert(1)`) закрывает тег и превращается в живой
    обработчик события, который переживает правку и уезжает в экспорт.
    """
    for key, value in (attrs or {}).items():
        name = str(key).strip().lower()
        if not _SETTABLE_ATTR_NAME.match(name):
            raise ValueError(
                f"недопустимое имя атрибута: {key!r} — имя может состоять только "
                "из букв, цифр, дефиса, подчёркивания, точки и двоеточия")
        element.attrs[name] = str(value)
        element.attr_case.pop(name, None)
    element.touch()


def op_delete(element: Node) -> None:
    parent = element.parent
    if parent is None:
        raise ValueError("элемент без родителя нельзя удалить")
    parent.children.remove(element)
    element.parent = None


def op_replace(element: Node, html: str) -> None:
    parent = element.parent
    if parent is None:
        raise ValueError("элемент без родителя нельзя заменить")
    nodes = parse_fragment(html)
    if not nodes:
        raise ValueError("замена пуста — нечем заменить элемент")
    index = parent.children.index(element)
    parent.children[index:index + 1] = nodes
    for node in nodes:
        node.parent = parent
    element.parent = None


def describe(element: Node) -> dict:
    """Что сервер знает об элементе — для инспектора в UI."""
    classes = [c for c in str(element.attrs.get("class") or "").split() if c]
    text = " ".join("".join(n.raw for n in element.children if n.kind == "text").split())
    return {
        "bd_id": element.bd_id,
        "tag": element.tag,
        "id": str(element.attrs.get("id") or ""),
        "classes": classes,
        "text": text[:200],
        "style": _parse_style(str(element.attrs.get("style") or "")),
        "children": sum(1 for n in element.children if n.kind == "element"),
    }


# ---------------------------------------------------------------- точечная правка

OPS = {"text", "style", "attrs", "replace", "delete"}


def apply_edit(html: str, edit: dict) -> tuple[str, dict]:
    """Одна точечная правка текущего кода. Возвращает (новый код, описание элемента).

    Цель: bd_id (нумерация из превью) с откатом на CSS-путь, если код уже
    переверстывался и номера ушли. Ошибки цели — LookupError, операции —
    ValueError, чтобы API переводил их в честные 400/404, а не 500.

    Если клиент прислал `tag` — ожидаемое имя тега выбранного элемента, — оно
    сверяется с найденным: устаревший номер, показывающий на ЧУЖОЙ элемент,
    отклоняется, а не правится молча.
    """
    if not isinstance(edit, dict) or edit.get("op") not in OPS:
        raise ValueError(f"неизвестная операция: {edit.get('op')!r}, доступно: {', '.join(sorted(OPS))}")
    root = parse_document(html)
    # Нумерация детерминирована (обход в глубину), поэтому bd-id, назначенный
    # при отдаче превью, находит элемент и в хранимом коде без маркеров —
    # пока код с момента превью не переверстан.
    assign_bd_ids(root)
    element = resolve_element(root, edit.get("bd_id"), edit.get("path"))
    if element is None:
        raise LookupError("элемент не найден в текущем коде — обновите превью и выберите заново")
    expect_tag = str(edit.get("tag") or "").strip().lower()
    if expect_tag and element.tag != expect_tag:
        raise LookupError(
            f"выделение устарело: номер указывает на <{element.tag}>, а выбран был "
            f"<{expect_tag}> — обновите превью и выберите заново")
    op = edit["op"]
    if op == "text":
        op_set_text(element, str(edit.get("text") or ""),
                    replace_children=bool(edit.get("replace_children")))
    elif op == "style":
        props = edit.get("props") or {}
        if not isinstance(props, dict) or not props:
            raise ValueError("для style нужны props: {свойство: значение}")
        op_set_style(element, props)
    elif op == "attrs":
        attrs = edit.get("attrs") or {}
        if not isinstance(attrs, dict) or not attrs:
            raise ValueError("для attrs нужны attrs: {атрибут: значение}")
        op_set_attrs(element, attrs)
    elif op == "replace":
        op_replace(element, str(edit.get("html") or ""))
    else:
        op_delete(element)
    described = describe(element)
    _strip_bd_ids(root)
    return serialize(root), described


def _strip_bd_ids(root: Node) -> None:
    """Служебные номера не попадают в хранимый код — только в отданное превью."""
    for element in walk_elements(root):
        element.bd_id = ""


# ---------------------------------------------------------------- превью с пикером

PICKER_JS = r"""(function () {
  if (window.__bdPicker) return;
  window.__bdPicker = true;
  var ENABLED = true;
  var hoverEl = null, selEl = null;
  var css = document.createElement('style');
  css.textContent = [
    '[data-bd-hover]{outline:2px solid #4f8cff !important;outline-offset:-2px !important;',
      'cursor:crosshair !important;}',
    '[data-bd-selected]{outline:2px solid #ffb020 !important;outline-offset:-2px !important;}',
    '#bd-label{position:fixed;z-index:2147483647;background:#111827;color:#e5e7eb;',
      'font:11px/1.5 ui-monospace,SFMono-Regular,Menlo,monospace;padding:3px 8px;border-radius:6px;',
      'pointer-events:none;display:none;white-space:nowrap;box-shadow:0 4px 14px rgba(0,0,0,.35)}',
    '#bd-flash{position:fixed;z-index:2147483646;pointer-events:none;border:2px solid #ffb020;',
      'border-radius:4px;box-shadow:0 0 0 6px rgba(255,176,32,.28);display:none;transition:all .35s ease}'
  ].join('\n');
  (document.head || document.documentElement).appendChild(css);
  var label = document.createElement('div'); label.id = 'bd-label';
  var flash = document.createElement('div'); flash.id = 'bd-flash';
  (document.body || document.documentElement).appendChild(label);
  (document.body || document.documentElement).appendChild(flash);

  function isIgnored(el) {
    return !el || el === label || el === flash ||
      (el.closest && el.closest('#bd-label,#bd-flash'));
  }
  function pathOf(el) {
    var parts = [];
    while (el && el.nodeType === 1) {
      var tag = el.tagName.toLowerCase();
      if (el.id) { parts.unshift(tag + '#' + el.id); break; }
      var i = 1, n = el;
      while ((n = n.previousElementSibling)) { if (n.tagName === el.tagName) i++; }
      parts.unshift(i > 1 ? tag + ':nth-of-type(' + i + ')' : tag);
      if (tag === 'html') break;
      el = el.parentElement;
    }
    return parts.join(' > ');
  }
  function labelFor(el) {
    var tag = el.tagName.toLowerCase();
    var id = el.id ? '#' + el.id : '';
    var cls = typeof el.className === 'string' && el.className.trim()
      ? '.' + el.className.trim().split(/\s+/).slice(0, 2).join('.') : '';
    return tag + id + cls;
  }
  function showLabel(el) {
    var r = el.getBoundingClientRect();
    label.textContent = labelFor(el);
    label.style.display = 'block';
    var top = Math.max(0, r.top - 22);
    label.style.left = Math.max(4, Math.min(r.left, window.innerWidth - label.offsetWidth - 8)) + 'px';
    label.style.top = top + 'px';
  }
  function describe(el) {
    var cs = getComputedStyle(el);
    var r = el.getBoundingClientRect();
    return {
      bd_id: el.getAttribute('data-bd-id') || '',
      tag: el.tagName.toLowerCase(),
      id: el.id || '',
      classes: typeof el.className === 'string'
        ? el.className.trim().split(/\s+/).filter(Boolean) : [],
      path: pathOf(el),
      children: el.children ? el.children.length : 0,
      text: (el.textContent || '').trim().replace(/\s+/g, ' ').slice(0, 200),
      styles: {
        color: cs.color, backgroundColor: cs.backgroundColor,
        fontSize: cs.fontSize, fontWeight: cs.fontWeight,
        fontFamily: cs.fontFamily, lineHeight: cs.lineHeight,
        padding: cs.padding, borderRadius: cs.borderRadius,
        textAlign: cs.textAlign, display: cs.display,
      },
      rect: { x: Math.round(r.x), y: Math.round(r.y), w: Math.round(r.width), h: Math.round(r.height) },
    };
  }
  function select(el) {
    if (selEl) selEl.removeAttribute('data-bd-selected');
    selEl = el;
    el.setAttribute('data-bd-selected', '1');
    parent.postMessage({ source: 'bd-preview', type: 'select', el: describe(el) }, '*');
  }
  function clearHover() {
    if (hoverEl) { hoverEl.removeAttribute('data-bd-hover'); hoverEl = null; }
    label.style.display = 'none';
  }
  document.addEventListener('mousemove', function (e) {
    if (!ENABLED || isIgnored(e.target)) return;
    if (hoverEl !== e.target) {
      if (hoverEl) hoverEl.removeAttribute('data-bd-hover');
      hoverEl = e.target;
      hoverEl.setAttribute('data-bd-hover', '1');
    }
    showLabel(hoverEl);
  }, true);
  document.addEventListener('mouseleave', clearHover, true);
  document.addEventListener('click', function (e) {
    if (!ENABLED || isIgnored(e.target)) return;
    e.preventDefault(); e.stopPropagation();
    select(e.target);
  }, true);
  window.addEventListener('scroll', clearHover, true);
  window.addEventListener('message', function (ev) {
    var d = ev.data;
    if (!d || d.source !== 'bd-host') return;
    if (d.type === 'pick') {
      ENABLED = !!d.enabled;
      if (!ENABLED) clearHover();
    } else if (d.type === 'flash' && d.bd_id) {
      var target = document.querySelector('[data-bd-id="' + String(d.bd_id).replace(/"/g, '') + '"]');
      if (!target) return;
      var r = target.getBoundingClientRect();
      flash.style.display = 'block';
      flash.style.left = r.x + 'px'; flash.style.top = r.y + 'px';
      flash.style.width = r.width + 'px'; flash.style.height = r.height + 'px';
      setTimeout(function () { flash.style.display = 'none'; }, 1200);
      target.scrollIntoView({ behavior: 'smooth', block: 'center' });
    }
  });
  parent.postMessage({ source: 'bd-preview', type: 'ready' }, '*');
})();"""


def inject_preview(html: str, *, script: str = PICKER_JS) -> str:
    """Код проекта → HTML для iframe: с data-bd-id и скриптом пикера.

    Инжект живёт только в ответе сервера; хранимый код не меняется.
    """
    if len(html) > MAX_HTML_CHARS:
        raise ValueError("документ слишком большой для превью")
    root = parse_document(html)
    assign_bd_ids(root)
    script_node = Node(tag="script", tag_case="script")
    script_node.children.append(Node(kind="text", raw=script, parent=script_node))
    body = _find_body(root)
    (body if body is not None else root).children.append(script_node)
    return serialize(root, bd_ids=True)


# ================================================================
# Epoch 4: строительные блоки для компонентов, токенов, отзывчивости и линта.
#
# Всё, что ниже, обязано соблюдать главный инвариант модуля: узел, которого
# правка не касалась, отдаётся ДОСЛОВНЫМ исходным тегом. Поэтому ни одна из
# функций ниже не зовёт `touch()` на чужих узлах — только на тех, которые она
# действительно переписывает, и только через существующие op_*.
# ================================================================

# Имя компонента / слота / токена — идентификатор, а не текст. Проверяется до
# того, как попадёт в разметку или в имя файла: имя — вторая половина той же
# дыры, что закрыта в `op_set_attrs` (значение экранируется, имя — нет).
COMPONENT_NAME_RE = re.compile(r"^[a-z0-9][a-z0-9._-]{0,47}$")
SLOT_NAME_RE = re.compile(r"^[a-z0-9][a-z0-9._-]{0,47}$")
TOKEN_NAME_RE = re.compile(r"^[a-z0-9][a-z0-9-]{0,47}$")
TOKEN_GROUP_RE = re.compile(r"^[a-z][a-z0-9-]{0,23}$")
# Значение токена уезжает ВНУТРЬ <style> — там нет экранирования, там сырой
# текст. Скобка или `<` закрывают блок и открывают чужой элемент.
_UNSAFE_IN_CSS_VALUE = re.compile(r"[<>{}\;]|/\*|\*/|</")

COMPONENT_ATTR = "data-bd-component"
COMPONENT_VERSION_ATTR = "data-bd-cver"
SLOT_ATTR = "data-bd-slot"

TOKENS_STYLE_ID = "bd-tokens"
RESPONSIVE_STYLE_ID = "bd-responsive"
NAV_ATTR = "data-bd-nav"


# ---------------------------------------------------------------- чтение/запись поддерева

def outer_html(node: Node) -> str:
    """Внешний HTML узла — ровно то, что вернёт сборка на этом поддереве."""
    return serialize(node)


def inner_html(node: Node) -> str:
    """Внутренний HTML: дети узла, собранные подряд."""
    return "".join(serialize(child) for child in node.children)


def set_inner_html(node: Node, html: str) -> None:
    """Заменить содержимое узла разобранным фрагментом (сам узел не трогаем)."""
    nodes = parse_fragment(html)
    for child in nodes:
        child.parent = node
    node.children = nodes


def _single_root(html: str, what: str) -> Node:
    """Фрагмент → ровно один корневой элемент, иначе честная ошибка."""
    nodes = [n for n in parse_fragment(html) if n.kind != "text" or n.raw.strip()]
    elements = [n for n in nodes if n.kind == "element"]
    if len(elements) != 1 or any(n.kind != "element" for n in nodes):
        raise ValueError(
            f"{what} должен состоять ровно из одного корневого элемента — "
            f"сейчас их {len(elements)}; оберните разметку в один тег")
    return elements[0]


def insert_nodes(target: Node, nodes: list[Node], position: str = "append") -> None:
    """Вставить узлы относительно цели: append | prepend | before | after."""
    if position in ("append", "prepend"):
        for node in nodes:
            node.parent = target
        if position == "append":
            target.children.extend(nodes)
        else:
            target.children[0:0] = nodes
        return
    parent = target.parent
    if parent is None:
        raise ValueError("нельзя вставить рядом с элементом без родителя")
    index = parent.children.index(target)
    if position == "after":
        index += 1
    elif position != "before":
        raise ValueError(f"неизвестная позиция вставки: {position!r} — "
                         "доступно: append, prepend, before, after")
    for node in nodes:
        node.parent = parent
    parent.children[index:index] = nodes


# ---------------------------------------------------------------- компоненты

def component_instances(root: Node, name: str | None = None) -> list[Node]:
    """Все экземпляры компонента в документе (по служебному атрибуту)."""
    out = []
    for element in walk_elements(root):
        marker = element.attrs.get(COMPONENT_ATTR)
        if marker is None:
            continue
        if name is None or str(marker) == str(name):
            out.append(element)
    return out


def collect_slots(instance: Node) -> dict[str, str]:
    """Содержимое слотов экземпляра: {имя слота: внутренний HTML}.

    Слот — это место, которое владелец правит ПОСЛЕ вставки. При обновлении
    определения оно обязано пережить пересборку, иначе «обновить компонент»
    означало бы «стереть текст на всех страницах».
    """
    slots: dict[str, str] = {}
    for element in walk_elements(instance):
        slot = element.attrs.get(SLOT_ATTR)
        if slot is None:
            continue
        slots.setdefault(str(slot), inner_html(element))
    root_slot = instance.attrs.get(SLOT_ATTR)
    if root_slot is not None:
        slots.setdefault(str(root_slot), inner_html(instance))
    return slots


def render_component(definition_html: str, name: str, version: int,
                     slots: dict[str, str] | None = None) -> Node:
    """Определение компонента → узел-экземпляр с проставленными слотами.

    Правило предсказуемости: разметка приходит из ОПРЕДЕЛЕНИЯ целиком, а из
    старого экземпляра переносится только содержимое слотов. Никаких «умных»
    слияний: владелец должен уметь сказать, что получится, не запуская код.
    """
    if not COMPONENT_NAME_RE.match(str(name or "")):
        raise ValueError(f"недопустимое имя компонента: {name!r}")
    node = _single_root(definition_html, "компонент")
    node.parent = None
    for slot_name, html in (slots or {}).items():
        if not SLOT_NAME_RE.match(str(slot_name)):
            continue
        for element in [node, *walk_elements(node)]:
            if str(element.attrs.get(SLOT_ATTR) or "") == str(slot_name):
                set_inner_html(element, html)
                break
    op_set_attrs(node, {COMPONENT_ATTR: str(name),
                        COMPONENT_VERSION_ATTR: str(int(version))})
    return node


def sync_component(html: str, name: str, definition_html: str, version: int) -> tuple[str, int]:
    """Пересобрать все экземпляры компонента в документе. → (новый HTML, сколько)."""
    root = parse_document(html)
    instances = component_instances(root, name)
    if not instances:
        return html, 0
    for instance in instances:
        fresh = render_component(definition_html, name, version, collect_slots(instance))
        keep_id = instance.attrs.get("id")
        if keep_id is not None:
            op_set_attrs(fresh, {"id": str(keep_id)})
        parent = instance.parent
        if parent is None:
            continue
        index = parent.children.index(instance)
        parent.children[index] = fresh
        fresh.parent = parent
        instance.parent = None
    return serialize(root), len(instances)


# ---------------------------------------------------------------- блоки <style> в <head>

def _find_head(root: Node) -> Node | None:
    return next((n for n in walk_elements(root) if n.tag == "head"), None)


def find_by_attr(root: Node, name: str, value: str) -> Node | None:
    return next((n for n in walk_elements(root)
                 if str(n.attrs.get(name) or "") == str(value)), None)


def _find_managed_style(root: Node, style_id: str) -> Node | None:
    """Именно `<style id=...>`, а не любой элемент владельца с тем же id."""
    return next((n for n in walk_elements(root)
                 if n.is_element("style") and str(n.attrs.get("id") or "") == str(style_id)), None)


def set_managed_style(html: str, style_id: str, css: str) -> str:
    """Записать управляемый панелью блок `<style id=...>` в <head>.

    Токены и брейкпоинты применяются ЧЕРЕЗ ЭТОТ БЛОК, а не переписыванием
    каждого элемента: иначе «поменять акцентный цвет» означало бы тронуть
    тысячу узлов и потерять дословное написание каждого из них.
    """
    root = parse_document(html)
    existing = _find_managed_style(root, style_id)
    if existing is not None:
        if not css.strip():
            op_delete(existing)
            return serialize(root)
        existing.children = [Node(kind="text", raw=css, parent=existing)]
        return serialize(root)
    if not css.strip():
        return serialize(root)
    node = Node(tag="style", tag_case="style", attrs={"id": style_id}, dirty=True)
    node.children.append(Node(kind="text", raw=css, parent=node))
    host = _find_head(root)
    if host is None:
        host = next((n for n in walk_elements(root) if n.tag == "html"), None) or root
    node.parent = host
    host.children.append(node)
    return serialize(root)


def get_managed_style(html: str, style_id: str) -> str:
    root = parse_document(html)
    node = _find_managed_style(root, style_id)
    if node is None:
        return ""
    return "".join(c.raw for c in node.children if c.kind == "text")


# ---------------------------------------------------------------- цвета и контраст

_NAMED_COLORS = {
    "black": (0, 0, 0), "white": (255, 255, 255), "red": (255, 0, 0),
    "green": (0, 128, 0), "blue": (0, 0, 255), "gray": (128, 128, 128),
    "grey": (128, 128, 128), "silver": (192, 192, 192), "yellow": (255, 255, 0),
    "orange": (255, 165, 0), "purple": (128, 0, 128), "navy": (0, 0, 128),
    "teal": (0, 128, 128), "olive": (128, 128, 0), "maroon": (128, 0, 0),
    "lime": (0, 255, 0), "aqua": (0, 255, 255), "fuchsia": (255, 0, 255),
}

_RGB_RE = re.compile(r"rgba?\(([^)]*)\)", re.I)
_VAR_RE = re.compile(r"var\(\s*(--[A-Za-z0-9_-]+)\s*(?:,([^)]*))?\)")


def parse_color(value: str, variables: dict[str, str] | None = None,
                _depth: int = 0) -> tuple[int, int, int] | None:
    """CSS-цвет → (r, g, b). Непонятное — None: линт молчит, а не выдумывает.

    Понимает #rgb/#rrggbb, rgb()/rgba(), базовые имена и `var(--x)` с опорой на
    словарь переменных документа. Всё остальное (градиенты, color-mix, hsl)
    честно не разбирается — лучше не сообщить о проблеме, чем сообщить о ней
    там, где её нет.
    """
    text = str(value or "").strip().lower()
    if not text or _depth > 4:
        return None
    match = _VAR_RE.search(text)
    if match:
        name = match.group(1)
        fallback = (match.group(2) or "").strip()
        resolved = (variables or {}).get(name)
        return parse_color(resolved or fallback, variables, _depth + 1)
    if text.startswith("#"):
        digits = text[1:]
        if len(digits) == 3 and all(c in "0123456789abcdef" for c in digits):
            return tuple(int(c * 2, 16) for c in digits)      # type: ignore[return-value]
        if len(digits) in (6, 8) and all(c in "0123456789abcdef" for c in digits[:6]):
            return (int(digits[0:2], 16), int(digits[2:4], 16), int(digits[4:6], 16))
        return None
    rgb = _RGB_RE.match(text)
    if rgb:
        parts = [p.strip() for p in rgb.group(1).replace("/", " ").split(",")]
        if len(parts) == 1:
            parts = [p for p in rgb.group(1).replace("/", " ").split() if p]
        nums = []
        for part in parts[:3]:
            try:
                nums.append(int(round(float(part.rstrip("%")) *
                                      (2.55 if part.endswith("%") else 1))))
            except ValueError:
                return None
        if len(nums) == 3:
            return (max(0, min(255, nums[0])), max(0, min(255, nums[1])),
                    max(0, min(255, nums[2])))
        return None
    return _NAMED_COLORS.get(text)


def _channel(value: int) -> float:
    c = value / 255.0
    return c / 12.92 if c <= 0.04045 else ((c + 0.055) / 1.055) ** 2.4


def relative_luminance(rgb: tuple[int, int, int]) -> float:
    r, g, b = (_channel(v) for v in rgb)
    return 0.2126 * r + 0.7152 * g + 0.0722 * b


def contrast_ratio(fg: tuple[int, int, int], bg: tuple[int, int, int]) -> float:
    """Коэффициент контраста по WCAG 2.1 (от 1 до 21)."""
    a, b = relative_luminance(fg), relative_luminance(bg)
    lighter, darker = (a, b) if a >= b else (b, a)
    return round((lighter + 0.05) / (darker + 0.05), 3)


# ---------------------------------------------------------------- мини-каскад CSS

_COMMENT_RE = re.compile(r"/\*.*?\*/", re.S)
_AT_RULE_RE = re.compile(r"@[a-zA-Z-]+")


def _strip_at_blocks(css: str) -> str:
    """Убрать @-правила вместе с их блоками: медиа-запросы каскад не двигают.

    Линт считает контраст «как в самом широком случае». Разбирать вложенные
    правила медиазапросов честнее было бы, но тогда одна и та же пара цветов
    давала бы несколько взаимоисключающих вердиктов — а находка обязана быть
    однозначной, иначе её никто не чинит.
    """
    out, i, n = [], 0, len(css)
    while i < n:
        match = _AT_RULE_RE.search(css, i)
        if match is None:
            out.append(css[i:])
            break
        out.append(css[i:match.start()])
        j = css.find("{", match.start())
        semi = css.find(";", match.start())
        if j < 0 or (0 <= semi < j):                 # @import ...; — без блока
            i = (semi + 1) if semi >= 0 else n
            continue
        depth, k = 0, j
        while k < n:
            if css[k] == "{":
                depth += 1
            elif css[k] == "}":
                depth -= 1
                if depth == 0:
                    k += 1
                    break
            k += 1
        i = k
    return "".join(out)


def _specificity(selector: str) -> int:
    return (selector.count("#") * 100) + (selector.count(".") * 10) + 1


def collect_css_rules(root: Node) -> list[tuple[str, dict[str, str], int, int]]:
    """`<style>` документа → [(селектор, объявления, специфичность, порядок)].

    Разбираются только простые селекторы (`tag`, `.class`, `#id` и их списки
    через запятую) — ровно те, что панель и генератор действительно пишут.
    Сложное молча пропускается: линт не должен выдумывать.
    """
    rules: list[tuple[str, dict[str, str], int, int]] = []
    order = 0
    for style in walk_elements(root):
        if not style.is_element("style"):
            continue
        css = _strip_at_blocks(_COMMENT_RE.sub("", "".join(
            c.raw for c in style.children if c.kind == "text")))
        for chunk in css.split("}"):
            selector_text, brace, body = chunk.partition("{")
            if not brace:
                continue
            decls = _parse_style(body)
            if not decls:
                continue
            for selector in selector_text.split(","):
                selector = " ".join(selector.split())
                if not selector:
                    continue
                order += 1
                rules.append((selector, decls, _specificity(selector), order))
    return rules


def _selector_matches(selector: str, element: Node) -> bool:
    """Совпадает ли простой (одноуровневый) селектор с элементом."""
    if " " in selector or ">" in selector or "+" in selector or "~" in selector:
        return False
    if ":" in selector:                              # :hover и подобное — не статика
        return False
    tag = selector
    for sep in ("#", "."):
        if sep in tag:
            tag = tag.split(sep, 1)[0]
    tag = tag.strip().lower()
    if tag and tag not in ("*", element.tag):
        return False
    want_id = ""
    if "#" in selector:
        want_id = selector.split("#", 1)[1].split(".")[0]
    if want_id and str(element.attrs.get("id") or "") != want_id:
        return False
    classes = set(str(element.attrs.get("class") or "").split())
    for wanted in re.findall(r"\.([A-Za-z0-9_-]+)", selector):
        if wanted not in classes:
            return False
    return True


def css_variables(root: Node) -> dict[str, str]:
    """Значения `--custom` из правил `:root`/`html`/`body` — для разбора var()."""
    variables: dict[str, str] = {}
    for style in walk_elements(root):
        if not style.is_element("style"):
            continue
        css = _strip_at_blocks(_COMMENT_RE.sub("", "".join(
            c.raw for c in style.children if c.kind == "text")))
        for chunk in css.split("}"):
            selector_text, brace, body = chunk.partition("{")
            if not brace:
                continue
            selectors = {" ".join(s.split()) for s in selector_text.split(",")}
            if not selectors & {":root", "html", "body", "*"}:
                continue
            for prop, value in _parse_style(body).items():
                if prop.startswith("--"):
                    variables[prop] = value
    return variables


# ---------------------------------------------------------------- линт

DEFAULT_TEXT_COLOR = (0, 0, 0)
DEFAULT_BACKGROUND = (255, 255, 255)
CONTRAST_MIN = 4.5
CONTRAST_MIN_LARGE = 3.0

_HEADINGS = ("h1", "h2", "h3", "h4", "h5", "h6")
_LABELLED_CONTROLS = ("input", "select", "textarea")
_UNLABELLED_INPUT_TYPES = frozenset({"hidden", "submit", "button", "reset", "image"})


def _text_of(element: Node) -> str:
    """Весь текст поддерева одной строкой — как его увидит человек."""
    parts = []
    stack = [element]
    while stack:
        node = stack.pop()
        if node.kind == "text":
            parts.append(node.raw)
        elif node.kind == "element" and node.tag not in ("script", "style"):
            stack.extend(reversed(node.children))
    return " ".join("".join(parts).split())


def _accessible_name(element: Node, labels_for: set[str], in_label: bool) -> str:
    for attr in ("aria-label", "title", "alt"):
        value = str(element.attrs.get(attr) or "").strip()
        if value:
            return value
    if str(element.attrs.get("aria-labelledby") or "").strip():
        return "aria-labelledby"
    element_id = str(element.attrs.get("id") or "")
    if element_id and element_id in labels_for:
        return f"label[for={element_id}]"
    if in_label:
        return "label-wrapper"
    return ""


def _finding(rule: str, severity: str, element: Node | None, message: str, **extra) -> dict:
    item = {"rule": rule, "severity": severity, "message": message}
    if element is not None:
        item["bd_id"] = element.bd_id
        item["tag"] = element.tag
        item["id"] = str(element.attrs.get("id") or "")
        item["text"] = _text_of(element)[:80]
    item.update(extra)
    return item


def _computed_colors(root: Node) -> dict[int, tuple[tuple[int, int, int] | None,
                                                    tuple[int, int, int] | None, float]]:
    """Для каждого элемента: (цвет текста, цвет фона, размер шрифта в px).

    Наследование считается сверху вниз одним проходом: `color` наследуется,
    фон берётся ближайший непрозрачный сверху — это и есть то, что видит глаз.
    """
    variables = css_variables(root)
    rules = collect_css_rules(root)
    out: dict[int, tuple] = {}
    stack: list[tuple[Node, tuple | None, tuple | None, float]] = [
        (child, None, None, 16.0) for child in reversed(root.children) if child.kind == "element"]
    while stack:
        element, color, background, size = stack.pop()
        decls: dict[str, str] = {}
        for selector, rule_decls, spec, order in sorted(rules, key=lambda r: (r[2], r[3])):
            if _selector_matches(selector, element):
                decls.update(rule_decls)
        decls.update(_parse_style(str(element.attrs.get("style") or "")))
        own_color = parse_color(decls.get("color", ""), variables)
        if own_color is not None:
            color = own_color
        own_bg = decls.get("background-color") or decls.get("background") or ""
        parsed_bg = parse_color(own_bg, variables)
        if parsed_bg is not None:
            background = parsed_bg
        raw_size = str(decls.get("font-size") or "").strip()
        if raw_size.endswith("px"):
            try:
                size = float(raw_size[:-2])
            except ValueError:
                pass
        out[id(element)] = (color, background, size)
        for child in reversed(element.children):
            if child.kind == "element":
                stack.append((child, color, background, size))
    return out


def lint_document(html: str) -> list[dict]:
    """Семантика и доступность документа → список структурированных находок.

    Правила: `img-alt`, `heading-order`, `heading-missing-h1`,
    `heading-multiple-h1`, `contrast`, `control-label`, `link-name`,
    `html-lang`, `document-title`. Каждая находка несёт bd_id того же
    номера, что и превью, — панель может подсветить проблему на месте.
    """
    root = parse_document(html)
    assign_bd_ids(root)
    elements = list(walk_elements(root))
    findings: list[dict] = []

    labels_for = {str(n.attrs.get("for") or "") for n in elements
                  if n.is_element("label") and n.attrs.get("for")}
    label_ancestors: set[int] = set()
    for node in elements:
        if node.is_element("label"):
            for inner in walk_elements(node):
                label_ancestors.add(id(inner))

    html_el = next((n for n in elements if n.tag == "html"), None)
    if html_el is not None and not str(html_el.attrs.get("lang") or "").strip():
        findings.append(_finding("html-lang", "warning", html_el,
                                 "у <html> нет атрибута lang — экранный диктор не знает языка"))
    title = next((n for n in elements if n.tag == "title"), None)
    if title is None or not _text_of(title):
        findings.append(_finding("document-title", "warning", title,
                                 "у страницы нет непустого <title>"))

    # --- альтернативный текст
    for element in elements:
        if element.tag == "img" and "alt" not in element.attrs:
            findings.append(_finding("img-alt", "error", element,
                                     "у <img> нет атрибута alt — картинка недоступна без зрения",
                                     src=str(element.attrs.get("src") or "")[:120]))

    # --- порядок заголовков
    headings = [n for n in elements if n.tag in _HEADINGS]
    h1s = [n for n in headings if n.tag == "h1"]
    if headings and not h1s:
        findings.append(_finding("heading-missing-h1", "warning", headings[0],
                                 "на странице нет <h1> — у документа нет главного заголовка"))
    for extra in h1s[1:]:
        findings.append(_finding("heading-multiple-h1", "warning", extra,
                                 "на странице больше одного <h1>"))
    previous = 0
    for heading in headings:
        level = int(heading.tag[1])
        if previous and level > previous + 1:
            findings.append(_finding(
                "heading-order", "error", heading,
                f"уровень заголовка прыгнул с h{previous} на h{level} — "
                "пропущенный уровень ломает навигацию по структуре",
                level=level, previous=previous))
        previous = level

    # --- подписи элементов управления
    for element in elements:
        if element.tag in _LABELLED_CONTROLS:
            if element.tag == "input" and str(
                    element.attrs.get("type") or "").lower() in _UNLABELLED_INPUT_TYPES:
                continue
            name = _accessible_name(element, labels_for, id(element) in label_ancestors)
            if not name:
                hint = ("подпись есть только в placeholder — она исчезает при вводе"
                        if element.attrs.get("placeholder") else "подписи нет вовсе")
                findings.append(_finding("control-label", "error", element,
                                         f"у <{element.tag}> нет доступной подписи: {hint}"))
        elif element.tag == "button":
            if not _text_of(element) and not _accessible_name(element, labels_for, False):
                findings.append(_finding("control-label", "error", element,
                                         "у <button> нет ни текста, ни aria-label"))
        elif element.tag == "a" and element.attrs.get("href") is not None:
            if not _text_of(element) and not _accessible_name(element, labels_for, False):
                findings.append(_finding("link-name", "error", element,
                                         "ссылка без текста — её нечем назвать вслух"))

    # --- контраст
    colors = _computed_colors(root)
    for element in elements:
        if element.tag in ("script", "style", "head", "html", "title", "meta", "link"):
            continue
        own_text = "".join(c.raw for c in element.children if c.kind == "text").strip()
        if not own_text:
            continue
        color, background, size = colors.get(id(element), (None, None, 16.0))
        fg = color or DEFAULT_TEXT_COLOR
        bg = background or DEFAULT_BACKGROUND
        ratio = contrast_ratio(fg, bg)
        # «Крупный текст» по WCAG — 24px и выше (жирность мы не считаем: без
        # неё порог 18.66px дал бы поблажку обычному тексту).
        minimum = CONTRAST_MIN_LARGE if size >= 24 else CONTRAST_MIN
        if ratio < minimum:
            findings.append(_finding(
                "contrast", "error", element,
                f"контраст текста {ratio} при минимуме {minimum} — текст плохо читается",
                ratio=ratio, minimum=minimum,
                foreground="#%02x%02x%02x" % fg, background="#%02x%02x%02x" % bg))
    return findings
