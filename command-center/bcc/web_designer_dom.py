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
