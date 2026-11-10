import copy, json, pathlib, pytest
from apps.newsletter import render

FX = json.loads((pathlib.Path(__file__).parent.parent / "fixtures" / "mock_weekly_ru.json").read_text(encoding="utf-8"))


def issue(**kw):
    d = copy.deepcopy(FX)
    d.update(kw)
    return d


@pytest.mark.parametrize("lang", ["en", "ru", "cz"])
@pytest.mark.parametrize("tpl,n", [("weekly", 3), ("breaking", 1), ("article", 1)])
def test_all_templates_and_languages_render(lang, tpl, n):
    d = issue(language=lang, template=tpl, articles=FX["articles"][:n])
    h, t = render.render_html(d), render.render_text(d)
    assert render.T[lang]["unsub"] in h and render.T[lang]["unsub"] in t
    assert d["footer"]["unsubscribe_url"] in t and h.count("<h2") == n
    assert render.T[lang]["mock"] in h and render.T[lang]["mock"] in t


def test_html_is_escaped():
    d = issue()
    d["articles"][0]["title"] = '<script>alert(1)</script> & "quotes"'
    h = render.render_html(d)
    assert "<script>" not in h and "&lt;script&gt;" in h and "&amp;" in h


@pytest.mark.parametrize("bad", ["javascript:alert(1)", "http://example.com/a", "/relative", "https://user:pw@example.com/", "https://exa mple.com", ""])
def test_unsafe_article_url_refused(bad):
    d = issue()
    d["articles"][0]["article_url"] = bad
    with pytest.raises(render.IssueError):
        render.render_html(d)


def test_unsubscribe_and_address_are_mandatory():
    for key in ("unsubscribe_url", "address"):
        d = issue()
        d["footer"][key] = ""
        with pytest.raises(render.IssueError):
            render.render_text(d)


def test_article_counts_enforced():
    with pytest.raises(render.IssueError):
        render.validate(issue(template="weekly", articles=FX["articles"][:2]))
    with pytest.raises(render.IssueError):
        render.validate(issue(template="breaking", articles=FX["articles"][:2]))


@pytest.mark.parametrize("phrase", ["Guaranteed 300% profit", "гарантированный доход", "без риска"])
def test_promise_of_return_refused(phrase):
    d = issue()
    d["articles"][1]["summary"] = phrase
    with pytest.raises(render.IssueError):
        render.validate(d)


def test_one_click_unsubscribe_headers():
    h = render.headers(issue())
    assert h["List-Unsubscribe"] == "<" + FX["footer"]["unsubscribe_url"] + ">"
    assert h["List-Unsubscribe-Post"] == "List-Unsubscribe=One-Click"
