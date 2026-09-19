"""GrapesJS edits the body; Bossman retains the document, versions and CAS.

Do not send executable/unsupported markup through a lossy component importer.
The existing source editor remains available for those documents.
"""
from . import web_designer_dom as dom

STYLE_ATTR = "data-bossman-visual"
UNSUPPORTED = {"script", "iframe", "object", "embed", "template", "svg", "math",
               "style", "link", "base", "meta", "noscript"}


def _validate_body(body):
    for node in (body, *dom.walk_elements(body)):
        if node.tag in UNSUPPORTED or "-" in (node.tag or ""):
            raise ValueError(f"Элемент <{node.tag}> нужно редактировать в панели «Код сайта».")
        if any(k.lower().startswith("on") or k.lower() in {"srcdoc", "is"}
               or k.lower().startswith("data-gjs-") for k in node.attrs):
            raise ValueError("Документ содержит программируемые элементы. Используйте панель «Код сайта».")
        for key in ("href", "src", "action", "formaction", "xlink:href"):
            value = "".join(str(node.attrs.get(key) or "").split()).lower()
            if value.startswith(("javascript:", "vbscript:")):
                raise ValueError("Активные ссылки нужно редактировать в панели «Код сайта».")


def _document(html):
    root = dom.parse_document(html)
    heads = [n for n in dom.walk_elements(root) if n.tag == "head"]
    bodies = [n for n in dom.walk_elements(root) if n.tag == "body"]
    if len(heads) != 1 or len(bodies) != 1:
        raise ValueError("Конструктору нужен HTML-документ с одним <head> и <body>.")
    _validate_body(bodies[0])
    return root, heads[0], bodies[0]


def parts(html):
    _, head, body = _document(html)
    styles = [n for n in head.children if n.tag == "style"]
    def css(nodes):
        return "\n".join("".join(c.raw for c in n.children) for n in nodes)
    return {
        "body": "".join(dom.serialize(n) for n in body.children),
        "body_attributes": dict(body.attrs),
        "css": css([n for n in styles if STYLE_ATTR in n.attrs]),
        "base_css": css([n for n in styles if STYLE_ATTR not in n.attrs]),
        # Keep stylesheet boundaries: flattening discards media/type conditions,
        # so print-only or mobile styles would incorrectly affect every canvas.
        "base_styles": [{"text": css([n]), "media": n.attrs.get("media") or "",
                         "type": n.attrs.get("type") or ""}
                        for n in styles if STYLE_ATTR not in n.attrs],
    }


def merge(html, body_html, css, body_attributes=None):
    root, head, body = _document(html)
    nodes = dom.parse_fragment(body_html)
    wrapper = dom.Node(tag="div", children=nodes)
    _validate_body(wrapper)
    if any(n.tag in {"html", "head", "body"} for n in dom.walk_elements(wrapper)):
        raise ValueError("Конструктор принимает содержимое страницы, а не второй HTML-документ.")
    # A stylesheet is raw-text HTML: a closing tag would escape the style node.
    if "</style" in css.lower():
        raise ValueError("Недопустимый закрывающий тег в CSS.")
    if body_attributes is not None and body_attributes != body.attrs:
        # The wrapper's generated id is needed by style rules targeting body.
        # Use the existing attribute validator/serializer, never concatenate it.
        attrs = {k: '' if v is None else v for k, v in body_attributes.items()}
        body.attrs = {}
        dom.op_set_attrs(body, attrs)
        _validate_body(body)
    for n in nodes:
        n.parent = body
    body.children = nodes
    head.children = [n for n in head.children
                     if not (n.tag == "style" and STYLE_ATTR in n.attrs)]
    if css.strip():
        style = dom.Node(tag="style", attrs={STYLE_ATTR: "grapesjs"}, parent=head)
        style.children = [dom.Node(kind="text", raw=css, parent=style)]
        head.children.append(style)
    result = dom.serialize(root)
    if len(result) > dom.MAX_HTML_CHARS:
        raise ValueError("Сайт превышает допустимый размер.")
    return result
