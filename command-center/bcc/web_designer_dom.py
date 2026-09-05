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
"""
from __future__ import annotations

from dataclasses import dataclass, field
from html import escape
from html.parser import HTMLParser

VOID_TAGS = frozenset({
    "area", "base", "br", "col", "embed", "hr", "img", "input",
    "link", "meta", "param", "source", "track", "wbr",
})

MAX_HTML_CHARS = 2_000_000


# ---------------------------------------------------------------- дерево

@dataclass
class Node:
    """Узел дерева. kind: element | text | comment | doctype."""
    kind: str = "element"
    tag: str | None = None                  # только для element, в нижнем регистре
    attrs: dict[str, str | None] = field(default_factory=dict)
    raw: str = ""                           # дословное содержимое text/comment/doctype
    children: list["Node"] = field(default_factory=list)
    parent: "Node | None" = None

    def is_element(self, *tags: str) -> bool:
        return self.kind == "element" and (not tags or self.tag in tags)


class _TreeBuilder(HTMLParser):
    """Терпимый сборщик дерева: незакрытые теги не роняют разбор."""

    def __init__(self) -> None:
        super().__init__(convert_charrefs=False)
        self.root = Node(kind="element", tag="#root")
        self._stack: list[Node] = [self.root]

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

    # -- HTMLParser ----------------------------------------------------

    def handle_starttag(self, tag, attrs):
        tag = tag.lower()
        node = Node(tag=tag, attrs={str(k).lower(): v for k, v in attrs})
        self._append(node)
        if tag not in VOID_TAGS:
            self._stack.append(node)

    def handle_startendtag(self, tag, attrs):
        tag = tag.lower()
        self._append(Node(tag=tag, attrs={str(k).lower(): v for k, v in attrs}))

    def handle_endtag(self, tag):
        tag = tag.lower()
        # ищем свой тег вверх по стеку; незакрытые попутные закрываем
        for i in range(len(self._stack) - 1, 0, -1):
            if self._stack[i].tag == tag:
                del self._stack[i:]
                return

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


def parse_document(html: str) -> Node:
    """HTML-документ → корень (#root) с доктайпом, html и комментариями."""
    builder = _TreeBuilder()
    builder.feed(html)
    builder.close()
    return builder.root


def parse_fragment(html: str) -> list[Node]:
    """Фрагмент (внешний HTML элемента) → список узлов верхнего уровня."""
    builder = _TreeBuilder()
    builder.feed(f"<div>{html}</div>")
    builder.close()
    return builder.root.children[0].children


def serialize(node: Node) -> str:
    """Дерево → HTML. Текст и комментарии отдаются как есть."""
    if node.kind == "text":
        return node.raw
    if node.kind == "comment":
        return f"<!--{node.raw}-->"
    if node.kind == "doctype":
        return f"<!{node.raw}>"
    if node.tag == "#root":                  # служебный корень тегом не печатается
        return "".join(serialize(child) for child in node.children)
    attrs = []
    for key, value in node.attrs.items():
        if value is None:
            attrs.append(f" {key}")
        else:
            attrs.append(f' {key}="{escape(str(value), quote=True)}"')
    if node.tag in VOID_TAGS:
        return f"<{node.tag}{''.join(attrs)}>"
    inner = "".join(serialize(child) for child in node.children)
    return f"<{node.tag}{''.join(attrs)}>{inner}</{node.tag}>"


# ---------------------------------------------------------------- обход

def walk_elements(root: Node):
    """Все элементы документа в порядке обхода в глубину (сам #root не считается)."""
    for child in root.children:
        if child.kind != "element":
            continue
        yield child
        yield from walk_elements(child)


def _find_body(root: Node) -> Node | None:
    return next((n for n in walk_elements(root) if n.tag == "body"), None)


# ---------------------------------------------------------------- нумерация и поиск

def assign_bd_ids(root: Node) -> int:
    """data-bd-id="bd-N" по всему документу, детерминированно. Возвращает количество."""
    count = 0
    for element in walk_elements(root):
        count += 1
        element.attrs["data-bd-id"] = f"bd-{count}"
    return count


def find_by_bd_id(root: Node, bd_id: str) -> Node | None:
    return next((n for n in walk_elements(root)
                 if n.attrs.get("data-bd-id") == str(bd_id)), None)


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


def op_set_text(element: Node, text: str) -> None:
    element.children = [Node(kind="text", raw=escape(text, quote=False))]


def op_set_style(element: Node, props: dict[str, str]) -> None:
    style = _parse_style(str(element.attrs.get("style") or ""))
    for prop, value in (props or {}).items():
        style[str(prop).strip().lower()] = str(value).strip()
    element.attrs["style"] = _dump_style(style)


def op_set_attrs(element: Node, attrs: dict[str, str]) -> None:
    for key, value in (attrs or {}).items():
        element.attrs[str(key).strip().lower()] = str(value)


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
        "bd_id": str(element.attrs.get("data-bd-id") or ""),
        "tag": element.tag,
        "id": str(element.attrs.get("id") or ""),
        "classes": classes,
        "text": text[:200],
        "style": _parse_style(str(element.attrs.get("style") or "")),
    }


# ---------------------------------------------------------------- точечная правка

OPS = {"text", "style", "attrs", "replace", "delete"}


def apply_edit(html: str, edit: dict) -> tuple[str, dict]:
    """Одна точечная правка текущего кода. Возвращает (новый код, описание элемента).

    Цель: bd_id (нумерация из превью) с откатом на CSS-путь, если код уже
    переверстывался и номера ушли. Ошибки цели — LookupError, операции —
    ValueError, чтобы API переводил их в честные 400/404, а не 500.
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
    op = edit["op"]
    if op == "text":
        op_set_text(element, str(edit.get("text") or ""))
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
        element.attrs.pop("data-bd-id", None)


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
    script_node = Node(tag="script")
    script_node.children.append(Node(kind="text", raw=script))
    body = _find_body(root)
    (body if body is not None else root).children.append(script_node)
    return serialize(root)
