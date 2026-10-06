"""Настольный чат Bossman (/chat.html): статические контракты страницы.

Браузер здесь не нужен: проверяется то, что можно доказать чтением исходников
и что ломается незаметно — поле токена не предлагается менеджеру паролей,
у каждой кнопки есть имя, скрытые рассуждения модели не доходят до экрана,
анимации выключаются при prefers-reduced-motion, каждый переключаемый класс
нарисован, запись в реестре страниц совпадает с модулем, `bcc-desktop --chat`
открывает /chat.html. Чистая логика чата (SSE, дельты, markdown, даты,
расчёты строки состояния) исполняется в node: ui/tests/chat_core.test.mjs.
"""
from __future__ import annotations

import io
import os
import re
import shutil
import subprocess
from pathlib import Path

import pytest

from bcc import desktop
from bcc.config import settings

CC = Path(__file__).resolve().parents[1]
UI = CC / "ui"
CHAT_DIR = UI / "chat"
CHAT_HTML = UI / "chat.html"


def _chat_js() -> dict[str, str]:
    return {p.name: p.read_text(encoding="utf-8") for p in sorted(CHAT_DIR.glob("*.js"))}


def _strip_js_comments(source: str) -> str:
    """Код без комментариев: блочные целиком, строчные — если `//` не часть URL."""
    source = re.sub(r"/\*.*?\*/", "", source, flags=re.S)
    return re.sub(r"(^|[^:\\'\"`])//[^\n]*", r"\1", source)


# ---------------------------------------------------------------- вход


def test_login_token_field_is_not_offered_to_a_password_manager():
    html = CHAT_HTML.read_text(encoding="utf-8")
    tag = re.search(r"<input[^>]*id=\"chat-login-token\"[^>]*>", html)
    assert tag, "chat.html must carry its own login field for a 401"
    tag = tag.group(0)
    assert 'type="text"' in tag
    assert 'autocomplete="one-time-code"' in tag
    assert "token-mask" in tag
    assert "-webkit-text-security: disc" in tag
    assert not re.search(r"type\s*=\s*[\"']?password", html, re.I)
    for name, source in _chat_js().items():
        assert not re.search(r"type\s*[:=]\s*[\"']password", source, re.I), name
    css = (CHAT_DIR / "chat.css").read_text(encoding="utf-8")
    assert re.search(r"\.token-mask\s*\{\s*-webkit-text-security:\s*disc;\s*\}", css)


def test_login_reuses_the_existing_session_api():
    main = (CHAT_DIR / "main.js").read_text(encoding="utf-8")
    assert "api.login(" in main and "api.loginHint()" in main
    assert "from '../api.js'" in main


# ---------------------------------------------------------------- кнопки и имена


def test_every_button_in_chat_html_has_a_name():
    html = CHAT_HTML.read_text(encoding="utf-8")
    buttons = re.findall(r"<button\b([^>]*)>(.*?)</button>", html, re.S)
    assert buttons, "the login card has a submit button"
    for attrs, inner in buttons:
        text = re.sub(r"<[^>]+>", "", inner).strip()
        assert text or "aria-label=" in attrs or "title=" in attrs, attrs


def _object_after(source: str, start: int) -> str:
    depth = 0
    for i in range(start, len(source)):
        if source[i] == "{":
            depth += 1
        elif source[i] == "}":
            depth -= 1
            if depth == 0:
                return source[start:i + 1]
    raise AssertionError("unbalanced attribute object")


def test_every_button_the_chat_builds_has_an_accessible_name():
    """Каждый h('button…', {…}) в модулях чата несёт aria-label.

    Кнопки-значки строит iconButton(), и он ставит aria-label и title из
    обязательного аргумента — это проверяется отдельно."""
    checked = 0
    for name, source in _chat_js().items():
        for m in re.finditer(r"h\('button[^']*',\s*\{", source):
            attrs = _object_after(source, m.end() - 1)
            assert "'aria-label'" in attrs, f"{name}: {attrs[:120]}"
            checked += 1
    assert checked >= 20, "the search itself broke: too few buttons found"
    dom = (CHAT_DIR / "dom.js").read_text(encoding="utf-8")
    icon_button = dom[dom.index("export function iconButton"):]
    icon_button = icon_button[:icon_button.index("\n}\n")]
    assert "'aria-label': label" in icon_button and "title: label" in icon_button


def test_no_button_is_labelled_exactly_povtorit():
    """Сторож обхода страниц запрещает кнопку «Повторить»: у чата — «Отправить ещё раз»."""
    for name, source in _chat_js().items():
        assert not re.search(r"['\"]Повторить['\"]", source), name
    assert "Отправить ещё раз" in (CHAT_DIR / "render.js").read_text(encoding="utf-8")


# ---------------------------------------------------------------- скрытые рассуждения


def _reasoning_mentions(source: str) -> list[str]:
    code = _strip_js_comments(source)
    return [line.strip() for line in code.splitlines() if re.search(r"reasoning|<think", line)]


def _is_drop_only(line: str) -> bool:
    """Строка state.js, где `<think` встречается только чтобы ВЫРЕЗАТЬ блок из текста (stripHidden): описание
    регулярного выражения или .replace/.test по нему. Любая другая — показ рассуждений."""
    if "reasoning" in line.lower() and "HIDDEN_KINDS" not in line:
        return False
    return bool(re.fullmatch(r"const THINK_BLOCK = /.*/gi;", line)
                or (("<think" in line) and (".replace(" in line or ".test(" in line)
                    and not re.search(r"\b(show|render|push|append|appendChild|h\()", line)))


def test_hidden_model_reasoning_is_only_ever_dropped():
    for name, source in _chat_js().items():
        mentions = _reasoning_mentions(source)
        if name == "state.js":
            head = "export const HIDDEN_KINDS = new Set(['run.reasoning_delta']);"
            assert mentions[0] == head, mentions
            assert all(_is_drop_only(line) for line in mentions[1:]), (
                f"state.js may mention <think only to cut it out of the text: {mentions[1:]}")
        else:
            assert mentions == [], f"{name} must not touch model reasoning: {mentions}"
    state = (CHAT_DIR / "state.js").read_text(encoding="utf-8")
    assert "if (HIDDEN_KINDS.has(kind)) { turn.hiddenDropped += 1; return false; }" in state


def test_the_drop_only_rule_would_notice_a_think_renderer():
    """Негативный контроль к _is_drop_only: вырезание разрешено, показ — нет."""
    assert _is_drop_only(r"const THINK_BLOCK = /<think(?:ing)?>[\s\S]*?<\/think>/gi;")
    assert _is_drop_only("let p = parts[i].replace(THINK_BLOCK, '').replace(/<think>/g, '');")
    assert not _is_drop_only(r"out.push(h('div.thought', text.match(/<think>(.*)<\/think>/)[1]));")
    assert not _is_drop_only("show(text.replace(/<think>/, ''));")
    assert not _is_drop_only("if (ev.kind === 'run.reasoning_delta') turn.thoughts.push(ev.text);")


def test_the_reasoning_check_would_notice_a_renderer():
    """Негативный контроль: код, который рисует рассуждения, эта проверка ловит."""
    bad = "/* ok */\nif (ev.kind === 'run.reasoning_delta') show(ev.text); // рисуем\n"
    assert _reasoning_mentions(bad) == ["if (ev.kind === 'run.reasoning_delta') show(ev.text);"]
    assert _reasoning_mentions("/* run.reasoning_delta в комментарии */\nconst a = 'https://x';\n") == []


# ---------------------------------------------------------------- стили и доступность


def test_reduced_motion_contrast_and_forced_colors_are_respected():
    css = (CHAT_DIR / "chat.css").read_text(encoding="utf-8")
    block = css[css.index("@media (prefers-reduced-motion: reduce)"):]
    block = block[:block.index("\n}\n")]
    assert "animation: none !important" in block and "transition: none !important" in block
    assert ".wave" in block, "the streaming waveform is hidden, not frozen mid-animation"
    assert "@media (prefers-contrast: more)" in css
    assert "@media (forced-colors: active)" in css
    assert ":focus-visible" in css
    assert "[hidden] { display: none !important; }" in css
    assert "@import" not in css and "url(" not in css, "no external fonts or assets"


def test_every_class_the_chat_toggles_has_a_css_rule():
    css = (CHAT_DIR / "chat.css").read_text(encoding="utf-8")
    sources = list(_chat_js().values()) + [(UI / "pages" / "chat.js").read_text(encoding="utf-8")]
    toggled = {c for s in sources for c in re.findall(r"classList\.(?:add|toggle)\(\s*'([a-z0-9-]+)'", s)}
    assert toggled, "the search itself broke"
    missing = sorted(c for c in toggled if not re.search(rf"\.{re.escape(c)}\b", css))
    assert not missing, missing
    assert not re.search(r"\.bx-definitely-not-a-class\b", css)


def test_chat_modules_import_only_existing_files_and_touch_no_dom_at_import():
    for name, source in _chat_js().items():
        for rel in re.findall(r"from '(\.{1,2}/[^']+)'", source):
            assert (CHAT_DIR / rel).resolve().is_file(), f"{name}: {rel}"
    for name in ("markdown.js", "state.js", "format.js", "stream.js", "audio.js", "scroll.js"):
        code = _strip_js_comments((CHAT_DIR / name).read_text(encoding="utf-8"))
        assert "document." not in code, f"{name} is pure logic and is imported by node tests"
    html = CHAT_HTML.read_text(encoding="utf-8")
    assert '<script type="module" src="chat/main.js"></script>' in html
    assert 'href="chat/chat.css"' in html
    assert "innerHTML" not in "".join(_strip_js_comments(s) for s in _chat_js().values())


def test_api_raw_accepts_an_abort_signal_without_changing_other_calls():
    api = (UI / "api.js").read_text(encoding="utf-8")
    assert "raw: (path, { method = 'GET', body, signal } = {}) => request(method, path, body, signal ? { signal } : undefined)," in api
    assert "if (method !== 'GET' || opts.signal) return rawRequest(method, path, body, opts);" in api


# ---------------------------------------------------------------- страница в Command Center


def test_lazy_page_entry_matches_the_module():
    index = (UI / "pages" / "index.js").read_text(encoding="utf-8")
    page = (UI / "pages" / "chat.js").read_text(encoding="utf-8")
    entry = re.search(r"lazyPage\(\{ id: 'chat'[^}]*\},\s*\(\) => import\('\./chat\.js'\)", index)
    assert entry, "chat must be registered in ui/pages/index.js"
    fields = dict(re.findall(r"(\w+): '([^']+)'", entry.group(0).split("},")[0]))
    assert fields == {"id": "chat", "title": "Чат", "icon": "tasks", "nav": "primary", "section": "main"}
    for key, value in fields.items():
        assert f"{key}: '{value}'" in page, key
    assert re.findall(r"title: '([^']+)'", index).count("Чат") == 1
    assert "title: 'Чат'" not in (UI / "pages.js").read_text(encoding="utf-8")
    assert page.endswith("export default ChatPage;\n")
    assert "console.log" not in page and "innerHTML" not in page
    assert "'/chat.html'" in page and "/chat.html#t=" in page
    assert "<button" not in page and "h('button" not in page, "links, not opener buttons"


# ---------------------------------------------------------------- bcc-desktop --chat

SAME = {"app": desktop.APP_IDENTITY, "version": "0.1.0", "source_identity": "PASS", "build_sha": "a" * 40}


def test_window_url_adds_the_chat_page_only_when_asked():
    assert desktop.window_url("http://127.0.0.1:8800/") == "http://127.0.0.1:8800/"
    assert desktop.window_url("http://127.0.0.1:8800/", chat=False) == "http://127.0.0.1:8800/"
    assert desktop.window_url("http://127.0.0.1:8800/", chat=True) == "http://127.0.0.1:8800/chat.html"
    assert desktop.build_parser().parse_args(["--chat"]).chat is True
    assert desktop.build_parser().parse_args([]).chat is False


def test_print_launcher_mentions_chat_only_when_given():
    with_chat, without = io.StringIO(), io.StringIO()
    assert desktop.run(["--print-launcher", "--port", "8800", "--chat"], out=with_chat) == 0
    assert desktop.run(["--print-launcher", "--port", "8800"], out=without) == 0
    command = [line for line in with_chat.getvalue().splitlines() if "команда ярлыка" in line]
    assert command and command[0].endswith(" --chat")
    assert "--chat" not in without.getvalue()


@pytest.mark.parametrize(("extra", "expected"), [(["--chat"], "http://127.0.0.1:18071/chat.html"),
                                                  ([], "http://127.0.0.1:18071/")])
def test_desktop_window_opens_chat_html_with_the_flag(tmp_path, monkeypatch, extra, expected):
    monkeypatch.setattr(settings, "data_dir", tmp_path)
    monkeypatch.setattr(desktop, "_local_identity", lambda: SAME)
    monkeypatch.setattr(desktop, "identify_server", lambda *a, **k: SAME)
    monkeypatch.setattr(desktop, "port_busy", lambda *a, **k: False)
    monkeypatch.setattr(desktop, "_orphan_window", lambda *a, **k: None)
    opened: list[str] = []
    code = desktop.run(["--port", "18071", "--browser", "dummy-browser", "--profile", str(tmp_path / "profile"),
                        "--no-show-token", *extra],
                       launcher=lambda browser, url, *a, **k: opened.append(url) or 0, out=io.StringIO())
    assert code == 0
    assert opened == [expected]


# ---------------------------------------------------------------- чистая логика в node


def test_chat_core_logic_in_node():
    node = os.environ.get("CODEX_PRIMARY_RUNTIME_NODE") or shutil.which("node")
    if not node:
        pytest.skip(reason="Node unavailable: ui/tests/chat_core.test.mjs was not executed")
    # node < 20.19 без "type": "module" не распознаёт ES-модули: флаг включает распознавание (на новых безвреден)
    result = subprocess.run([node, "--experimental-detect-module", "--no-warnings", "--test",
                             str(UI / "tests" / "chat_core.test.mjs")],
                            capture_output=True, text=True, timeout=60, check=False, cwd=str(CC))
    assert result.returncode == 0, result.stdout + result.stderr


# ---------------------------------------------------------------- политика страницы и новые элементы
# CSP + Trusted Types (ни одного встроенного скрипта), единственная область объявлений,
# кнопка «К последнему сообщению» и баннер потери связи с доступными именами, правила
# reduced-motion для новых элементов, ярлык BOSSMAN не заменяется ярлыком чата.
# Добавлены в конец файла: строка pytest.skip выше не сдвигается.

_CSP_META = re.compile(r"<meta\s+http-equiv=\"Content-Security-Policy\"\s+content=\"([^\"]+)\"\s*>", re.I)


def _inline_script_problems(html: str) -> list[str]:
    """Что политика script-src 'self' запретила бы: <script> без src и обработчики on…= в разметке."""
    problems = [m.group(0) for m in re.finditer(r"<script\b(?![^>]*\bsrc=)[^>]*>", html, re.I)]
    problems += [m.group(0) for m in re.finditer(r"<[a-z][^>]*\son[a-z]+\s*=", html, re.I)]
    return problems


def test_chat_html_has_a_strict_csp_with_trusted_types_and_no_inline_script():
    html = CHAT_HTML.read_text(encoding="utf-8")
    meta = _CSP_META.search(html)
    assert meta, "chat.html renders untrusted model output: it must carry its own CSP"
    policy = {d.strip().split()[0]: d.strip().split()[1:] for d in meta.group(1).split(";") if d.strip()}
    assert policy["require-trusted-types-for"] == ["'script'"]
    assert policy["trusted-types"] == ["'none'"]
    for directive in ("default-src", "script-src", "connect-src"):
        assert policy[directive] == ["'self'"], directive
    assert policy["object-src"] == ["'none'"] and policy["base-uri"] == ["'none'"]
    # мета действует только на то, что ниже неё: она раньше любого скрипта и стиля
    assert meta.start() < html.index("<script") and meta.start() < html.index("<link")
    assert _inline_script_problems(html) == []
    head = html[:html.index("</head>")]
    assert '<script src="chat/theme-boot.js"></script>' in head, "theme before first paint, as a file"
    assert (CHAT_DIR / "theme-boot.js").is_file()
    assert "localStorage" not in re.sub(r"<!--.*?-->", "", html, flags=re.S), "no inline theme script left"


def test_the_inline_script_check_would_notice_a_regression():
    """Негативный контроль для проверки выше."""
    assert _inline_script_problems('<script>alert(1)</script>') == ["<script>"]
    assert _inline_script_problems('<button onclick="x()">a</button>') != []
    assert _inline_script_problems('<script type="module" src="chat/main.js"></script>') == []
    assert _inline_script_problems('<button class="once">a</button>') == []


def test_chat_code_needs_nothing_the_csp_forbids():
    """Trusted Types без политик: любая HTML-строка в DOM бросит ошибку — в коде чата их нет.
    Внешних адресов и WebSocket чат не открывает (connect-src 'self')."""
    sources = {**_chat_js(), "api.js": (UI / "api.js").read_text(encoding="utf-8")}
    for name, source in sources.items():
        code = _strip_js_comments(source)
        for sink in ("innerHTML", "outerHTML", "insertAdjacentHTML", "document.write", "eval(", "new Function",
                     "srcdoc", "javascript:"):
            assert sink not in code, f"{name}: {sink}"
    for name, source in _chat_js().items():
        code = _strip_js_comments(source)
        assert "WebSocket" not in code and "EventStream" not in code, name
        assert not re.search(r"fetch\(\s*['\"`]https?://", code), name


def test_live_regions_jump_button_and_reconnect_banner_are_accessible():
    html = CHAT_HTML.read_text(encoding="utf-8")
    announce = re.search(r"<div[^>]*id=\"chat-announce\"[^>]*>", html)
    assert announce and 'role="status"' in announce.group(0) and "sr-only" in announce.group(0)
    assert len(re.findall(r"id=\"chat-announce\"", html)) == 1
    jump = re.search(r"<button[^>]*class=\"jump-latest\"[^>]*>", html)
    assert jump and 'aria-label="К последнему сообщению (End)"' in jump.group(0)
    now = re.search(r"<button[^>]*id=\"chat-reconnect-now\"[^>]*>(.*?)</button>", html)
    assert now and "aria-label=" in now.group(0) and now.group(1) == "Сейчас"
    assert re.search(r"<span[^>]*id=\"chat-reconnect-msg\"[^>]*role=\"status\"", html)
    css = (CHAT_DIR / "chat.css").read_text(encoding="utf-8")
    assert re.search(r"\.sr-only\s*\{[^}]*clip", css)
    render = (CHAT_DIR / "render.js").read_text(encoding="utf-8")
    assert "h('div.bot-status')," in render, "the per-row status is not a second live region"
    # ход, ждущий подтверждения, не «занят»: карточка решения доходит до читалки сразу
    assert "row.setAttribute('aria-busy', live && !waiting ? 'true' : 'false');" in render
    main = (CHAT_DIR / "main.js").read_text(encoding="utf-8")
    assert "if (e.defaultPrevented) return;" in main, "Esc in the title or search field is not STOP"
    assert "S.stream.retryNow()" in main and "window.addEventListener('online', retryStreamNow);" in main


def test_reduced_motion_and_rendering_rules_cover_the_new_elements():
    css = (CHAT_DIR / "chat.css").read_text(encoding="utf-8")
    block = css[css.index("@media (prefers-reduced-motion: reduce)"):]
    block = block[:block.index("\n}\n")]
    for selector in (".md-copy", ".jump-latest"):
        assert selector in block, selector
    assert re.search(r"\.md-copy\[data-copied\]\s*\{[^}]*var\(--copied\)", css)
    assert css.count("--copied:") >= 2, "the copied colour is defined for the dark and the light theme"
    assert re.search(r"\.md-code-head\s*\{[^}]*position:\s*sticky", css)
    assert "content-visibility: auto" in css and 'aria-busy="true"' in css and ":focus-within" in css
    scroll = (CHAT_DIR / "scroll.js").read_text(encoding="utf-8")
    assert "reducedMotion()" in scroll, "the jump scrolls smoothly only without reduce-motion"


def test_install_shortcut_with_chat_never_replaces_the_main_shortcut(monkeypatch):
    from bcc import desktop_install

    calls: list[tuple] = []
    monkeypatch.setattr(desktop_install, "install", lambda *a, **k: calls.append(("install", a)) or [])
    monkeypatch.setattr(desktop_install, "uninstall", lambda *a, **k: calls.append(("uninstall", a)) or [])
    for flag in ("--install-shortcut", "--uninstall-shortcut"):
        out = io.StringIO()
        assert desktop.run([flag, "--port", "8800", "--chat"], out=out) == 2
        assert "--chat не меняет ярлык BOSSMAN" in out.getvalue()
    assert calls == [], "the standard BOSSMAN shortcut was touched"
    # негативный контроль: без --chat установка по-прежнему доходит до ярлыка, и в нём нет --chat
    assert desktop.run(["--install-shortcut", "--port", "8800"], out=io.StringIO()) == 0
    assert [c[0] for c in calls] == ["install"]
    assert "--chat" not in calls[0][1][0].argv


def test_chat_flag_with_a_running_bossman_window_tells_where_the_chat_is(tmp_path, monkeypatch):
    """Окно BOSSMAN уже открыто (живой замок той же сборки, тот же профиль Chrome): второе окно
    `bcc-desktop --chat` не открывает, но и не молчит — печатает адрес чата.
    Негативный контроль: без --chat отказ прежний, адреса чата в нём нет."""
    import json

    monkeypatch.setattr(settings, "data_dir", tmp_path)
    monkeypatch.setattr(desktop, "_local_identity", lambda: SAME)
    monkeypatch.setattr(desktop, "identify_server", lambda *a, **k: SAME)
    for extra, mentions_chat in ((["--chat"], True), ([], False)):
        (tmp_path / "desktop.lock").write_text(
            json.dumps({"pid": os.getpid(), "pid_created": desktop._process_created(os.getpid()),
                        "port": 18073}), encoding="utf-8")
        opened: list = []
        out = io.StringIO()
        code = desktop.run(["--port", "18073", "--browser", "dummy-browser", "--profile", str(tmp_path / "profile"),
                            "--no-show-token", *extra],
                           launcher=lambda *a, **k: opened.append(a) or 0, out=out)
        assert code == 0 and opened == [], "a second window on the same profile is still refused"
        assert "уже запущено" in out.getvalue()
        assert ("http://127.0.0.1:18073/chat.html" in out.getvalue()) is mentions_chat, out.getvalue()


def test_skip_link_and_foreign_anchors_never_reset_the_open_thread():
    """«Перейти к полю ввода» (href="#chat-input") меняла бы адрес, а hashchange открывал бы новый чат."""
    main = (CHAT_DIR / "main.js").read_text(encoding="utf-8")
    html = CHAT_HTML.read_text(encoding="utf-8")
    assert '<a class="skip-link" href="#chat-input">' in html
    assert "document.querySelector('.skip-link')" in main and "e.preventDefault();" in main
    handler = main[main.index("window.addEventListener('hashchange'"):]
    handler = handler[:handler.index("});")]
    assert "!parseHash().thread" in handler and handler.index("!parseHash().thread") < handler.index("route();")


def test_failed_task_output_is_never_presented_as_a_verified_result():
    pages = (UI / "pages.js").read_text(encoding="utf-8")
    block_start = pages.index("result ? h('div',")
    block = pages[block_start:block_start + 500]
    assert "status === 'completed' ? 'Результат' : 'Ответ модели · задача не завершена'" in block
    assert "Текст ответа не подтверждает выполнение действия." in block
