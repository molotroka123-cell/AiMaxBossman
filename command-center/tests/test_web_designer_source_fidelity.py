"""Remaining WD source-loss cases after the seven historical P1 fixes."""
import pytest
from bcc.web_designer_dom import apply_edit, parse_document, serialize, Node

@pytest.mark.parametrize("space", ["\n", "\r\n", "\t", " ", "\f"])
def test_svg_attribute_spelling_survives_edit_with_multiline_attributes(space):
    source = f'<svg{space}viewBox="0 0 10 10"{space}preserveAspectRatio="xMidYMid"><linearGradient id="g"/></svg>'
    changed, _ = apply_edit(source, {"op": "style", "path": "svg", "props": {"color": "red"}})
    assert 'viewBox="0 0 10 10"' in changed
    assert 'preserveAspectRatio="xMidYMid"' in changed
    assert '<linearGradient id="g"/>' in changed
    assert 'viewbox=' not in changed

@pytest.mark.parametrize("source", [
    '<DIV><p>hello</p ></DIV >',
    '<p>hello</p></stray>',
    '<p>hello',
    '<main><span>hello</main>',
    '<HTML><BODY><p>hello</P >\n</body></HTML>',
    '<br></br><div>x</DIV >',
])
def test_end_tokens_and_omitted_endings_are_retained(source):
    assert serialize(parse_document(source)) == source


def test_small_edit_preserves_untouched_closing_tokens():
    source = '<DIV><h1>T</h1 ><svg\nviewBox="0 0 1 1"></SVG ></DIV >'
    changed, _ = apply_edit(source, {"op":"style", "path":"h1", "props":{"color":"red"}})
    assert changed[changed.index('</h1 >'):] == source[source.index('</h1 >'):]


def test_programmatically_constructed_nodes_still_close():
    assert serialize(Node(tag="p")) == '<p></p>'
