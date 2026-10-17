"""«🛰 Терминал» next to «♠️ Покер»: Mini App over HTTPS, browser link over the tailnet HTTP, owner only (owner 17.10)."""
from bcc.telegram_companion.adapters import markup
from bcc.telegram_companion.config import Person
from bcc.telegram_companion.store import Store
from bcc.telegram_companion.war_terminal_button import DEFAULT_URL, ENV, terminal_button, terminal_text

OWNER = Person(101, 101, "owner")
GUEST = Person(202, 202, "guest")


def test_https_base_gives_mini_app_on_tg_path():
    label, target = terminal_button({ENV: "https://box.tail0.ts.net:8443/"})
    assert label == "🛰 Терминал"
    assert target == {"web_app": {"url": "https://box.tail0.ts.net:8443/tg/"}}
    assert terminal_button({ENV: "https://box.tail0.ts.net/tg"})[1] == {"web_app": {"url": "https://box.tail0.ts.net/tg/"}}


def test_without_https_falls_back_to_browser_link_never_web_app():
    assert terminal_button({})[1] == {"url": DEFAULT_URL + "/"}
    assert terminal_button({ENV: "http://box:8830"})[1] == {"url": "http://box:8830/"}
    for bad in ("ftp://x", "https://u:p@x", "https://x/?q=1", "javascript:alert(1)"):
        assert terminal_button({ENV: bad})[1] == {"url": DEFAULT_URL + "/"}, bad


def test_markup_renders_link_and_mini_app_buttons_and_refuses_http_mini_app():
    m = markup([[("a", "b:0123456789abcdef"), ("T", {"web_app": {"url": "https://h/tg/"}}), ("L", {"url": "http://h:8830/"})],
                [("bad1", {"web_app": {"url": "http://h/tg/"}}), ("bad2", {"url": "javascript:x"}),
                 ("bad3", {"url": "https://h", "callback_data": "x"})]])
    assert m == {"inline_keyboard": [[{"text": "a", "callback_data": "b:0123456789abcdef"},
                                      {"text": "T", "web_app": {"url": "https://h/tg/"}},
                                      {"text": "L", "url": "http://h:8830/"}]]}


def test_terminal_command_is_owner_only_and_control_lane():
    reply = terminal_text(OWNER, {})
    assert reply.keyboard == [[("🛰 Терминал", {"url": DEFAULT_URL + "/"})]]
    assert "браузере" in str(reply)
    assert "Mini App" in str(terminal_text(OWNER, {ENV: "https://h"}))
    assert terminal_text(GUEST, {}) == "Терминал доступен только владельцу."
    assert Store.lane({"text": "/terminal"}) == "control"


def test_pc_menu_has_terminal_right_after_poker():
    from bcc.telegram_companion.pc_agent_bridge import PCAgentBridgeMixin

    class Fake(PCAgentBridgeMixin):
        def button(self, person, label, command):
            return (label, "b:" + "0" * 16)

    rows = Fake().pc_agent_menu(OWNER)
    row = next(r for r in rows if any(label == "♠️ Покер" for label, _ in r))
    labels = [label for label, _ in row]
    assert labels[labels.index("♠️ Покер") + 1] == "🛰 Терминал"
    assert all(len(r) <= 4 for r in rows)          # markup() keeps at most 4 per row
    assert any(label == "🧬 Boost" for r in rows for label, _ in r)
    rendered = markup(rows)["inline_keyboard"]
    assert any(b.get("text") == "🛰 Терминал" and ("url" in b or "web_app" in b) for r in rendered for b in r)
