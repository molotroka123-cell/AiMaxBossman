"""Newsletter template engine: 3 templates x 3 languages, HTML + plain text, from one JSON document.

JSON interface (see fixtures/mock_weekly_ru.json):
  publication_name, issue_date, language (en|ru|cz), template (weekly|breaking|article), subject, preheader,
  intro, articles[{title, summary, category, image_url, article_url}], sender{name,email},
  footer{address, unsubscribe_url}, mock
Rules enforced here, not left to the caller:
  - every string is HTML-escaped; URLs must be absolute https (http only for localhost previews);
  - an issue without a footer address and an unsubscribe URL does not render;
  - article counts: weekly 3..5, breaking exactly 1, article exactly 1;
  - wording that promises returns is refused;
  - mock issues ("mock": true) carry a visible MOCK banner in both HTML and text.
"""
from __future__ import annotations

import html
import json
import math
import sys
from urllib.parse import urlparse

LANGS = ("en", "ru", "cz")
HTML_LANG = {"en": "en", "ru": "ru", "cz": "cs"}
T = {
    "en": {"read": "Read the article", "unsub": "Unsubscribe",
           "why": "You receive this email because you subscribed and confirmed your address.",
           "mock": "MOCK DATA: test issue, not real news", "weekly": "Weekly digest", "breaking": "Breaking news",
           "article": "New article", "note": "Not financial advice. We never promise investment returns."},
    "ru": {"read": "Читать статью", "unsub": "Отписаться",
           "why": "Вы получили это письмо, потому что подписались и подтвердили адрес.",
           "mock": "ТЕСТОВЫЕ ДАННЫЕ: пробный выпуск, не настоящие новости", "weekly": "Дайджест недели",
           "breaking": "Срочная новость", "article": "Новая статья",
           "note": "Не является финансовой рекомендацией. Мы не обещаем доходность."},
    "cz": {"read": "Číst článek", "unsub": "Odhlásit odběr",
           "why": "Tento e-mail dostáváte, protože jste se přihlásili k odběru a potvrdili adresu.",
           "mock": "TESTOVACÍ DATA: zkušební vydání, nejde o skutečné zprávy", "weekly": "Týdenní přehled",
           "breaking": "Mimořádná zpráva", "article": "Nový článek",
           "note": "Nejedná se o investiční doporučení. Neslibujeme žádné výnosy."},
}
COUNTS = {"weekly": (3, 5), "breaking": (1, 1), "article": (1, 1)}
BANNED = ("guaranteed", "гарантирован", "zaručen", "100% profit", "без риска", "risk-free", "bez rizika")
FONT = "Arial,Helvetica,sans-serif"


class IssueError(ValueError):
    pass


def e(s) -> str:
    return html.escape(str(s or ""), quote=True)


def safe_url(u, field: str) -> str:
    u = str(u or "")
    p = urlparse(u)
    local = p.hostname in ("localhost", "127.0.0.1")
    if p.scheme not in ("https", "http") or not p.hostname or (p.scheme == "http" and not local):
        raise IssueError(f"{field}: only absolute https URLs are allowed, got {u!r}")
    if p.username or p.password or any(c in u for c in ' "<>\n\r'):
        raise IssueError(f"{field}: unsafe URL {u!r}")
    return u


def validate(issue: dict) -> dict:
    lang, tpl = issue.get("language"), issue.get("template")
    if lang not in LANGS:
        raise IssueError(f"language must be one of {LANGS}")
    if tpl not in COUNTS:
        raise IssueError(f"template must be one of {tuple(COUNTS)}")
    for k in ("publication_name", "issue_date", "subject", "preheader"):
        if not str(issue.get(k) or "").strip():
            raise IssueError(f"missing {k}")
    arts = issue.get("articles") or []
    lo, hi = COUNTS[tpl]
    if not lo <= len(arts) <= hi:
        raise IssueError(f"template {tpl} takes {lo}..{hi} articles, got {len(arts)}")
    for i, a in enumerate(arts):
        for k in ("title", "summary", "category"):
            if not str(a.get(k) or "").strip():
                raise IssueError(f"articles[{i}].{k} is empty")
        safe_url(a.get("article_url"), f"articles[{i}].article_url")
        if a.get("image_url"):
            safe_url(a["image_url"], f"articles[{i}].image_url")
    footer = issue.get("footer") or {}
    if not str(footer.get("address") or "").strip():
        raise IssueError("footer.address (postal address of the sender) is required")
    safe_url(footer.get("unsubscribe_url"), "footer.unsubscribe_url")
    text = json.dumps(issue, ensure_ascii=False).lower()
    for w in BANNED:
        if w in text:
            raise IssueError(f"editorial rule: promise-of-return wording {w!r} is not allowed")
    return issue


def render_text(issue: dict) -> str:
    validate(issue)
    t = T[issue["language"]]
    out = []
    if issue.get("mock"):
        out += [f"*** {t['mock']} ***", ""]
    out += [f"{issue['publication_name']} / {t[issue['template']]} / {issue['issue_date']}", ""]
    if issue.get("intro"):
        out += [issue["intro"], ""]
    for a in issue["articles"]:
        out += [f"[{a['category']}] {a['title']}", a["summary"], f"{t['read']}: {a['article_url']}", ""]
    f = issue["footer"]
    out += ["--", t["note"], t["why"], f["address"], f"{t['unsub']}: {f['unsubscribe_url']}"]
    return "\n".join(out)


def _card(a: dict, t: dict, big: bool) -> str:
    img = ""
    if a.get("image_url"):
        img = (f'<img src="{e(a["image_url"])}" alt="" width="536" '
               'style="display:block;width:100%;max-width:536px;height:auto;border-radius:6px;margin:0 0 12px 0">')
    size = "26" if big else "19"
    return (
        '<tr><td style="padding:20px 32px;border-top:1px solid #e6e8ec">' + img +
        f'<p style="margin:0 0 6px 0;font:600 12px {FONT};letter-spacing:.06em;text-transform:uppercase;color:#b45309">{e(a["category"])}</p>'
        f'<h2 style="margin:0 0 8px 0;font:700 {size}px/1.25 Georgia,serif;color:#111827">{e(a["title"])}</h2>'
        f'<p style="margin:0 0 14px 0;font:400 15px/1.55 {FONT};color:#374151">{e(a["summary"])}</p>'
        f'<a href="{e(a["article_url"])}" style="display:inline-block;padding:10px 18px;background:#111827;color:#ffffff;'
        f'font:600 14px {FONT};text-decoration:none;border-radius:4px">{e(t["read"])}</a></td></tr>')


def render_html(issue: dict) -> str:
    validate(issue)
    t = T[issue["language"]]
    f = issue["footer"]
    big = issue["template"] != "weekly"
    cards = "".join(_card(a, t, big) for a in issue["articles"])
    mock = ""
    if issue.get("mock"):
        mock = (f'<tr><td style="padding:10px 32px;background:#fde68a;font:700 13px {FONT};color:#7c2d12;text-align:center">'
                f'{e(t["mock"])}</td></tr>')
    intro = ""
    if issue.get("intro"):
        intro = f'<tr><td style="padding:0 32px 20px 32px;font:400 16px/1.6 {FONT};color:#1f2937">{e(issue["intro"])}</td></tr>'
    return (
        f'<!doctype html><html lang="{HTML_LANG[issue["language"]]}"><head><meta charset="utf-8">'
        '<meta name="viewport" content="width=device-width,initial-scale=1"><meta name="color-scheme" content="light">'
        f"<title>{e(issue['subject'])}</title></head>"
        '<body style="margin:0;padding:0;background:#f3f4f6">'
        f'<div style="display:none;max-height:0;overflow:hidden;opacity:0">{e(issue["preheader"])}</div>'
        '<table role="presentation" width="100%" cellpadding="0" cellspacing="0" style="background:#f3f4f6">'
        '<tr><td align="center" style="padding:24px 12px">'
        '<table role="presentation" width="600" cellpadding="0" cellspacing="0" '
        'style="width:100%;max-width:600px;background:#ffffff;border-radius:8px">' + mock +
        f'<tr><td style="padding:28px 32px 8px 32px"><p style="margin:0;font:700 22px Georgia,serif;color:#111827">{e(issue["publication_name"])}</p>'
        f'<p style="margin:4px 0 16px 0;font:400 13px {FONT};color:#6b7280">{e(t[issue["template"]])} · {e(issue["issue_date"])}</p></td></tr>'
        + intro + cards +
        f'<tr><td style="padding:22px 32px 28px 32px;border-top:1px solid #e6e8ec;font:400 12px/1.6 {FONT};color:#6b7280">'
        f'{e(t["note"])}<br>{e(t["why"])}<br>{e(f["address"])}<br>'
        f'<a href="{e(f["unsubscribe_url"])}" style="color:#374151;text-decoration:underline">{e(t["unsub"])}</a></td></tr>'
        "</table></td></tr></table></body></html>")


def headers(issue: dict) -> dict:
    """Headers the sending provider must set (RFC 2369 and RFC 8058 one-click unsubscribe)."""
    validate(issue)
    return {"List-Unsubscribe": f"<{issue['footer']['unsubscribe_url']}>", "List-Unsubscribe-Post": "List-Unsubscribe=One-Click"}


if __name__ == "__main__":
    doc = json.load(open(sys.argv[1], encoding="utf-8"))
    base = sys.argv[2] if len(sys.argv) > 2 else "out/preview"
    open(base + ".html", "w", encoding="utf-8").write(render_html(doc))
    open(base + ".txt", "w", encoding="utf-8").write(render_text(doc))
    print("rendered", base + ".html", base + ".txt", "html bytes", math.ceil(len(render_html(doc).encode("utf-8"))))
