"""ComputerUse: страница — недоверенные данные, а submit не обходит approval.

Мастер-промпт требует двух доказанных свойств:
1. Обычный browser.press не должен пропускать Enter/submit мимо approval;
   consequential-действия идут через confirmed_* с confirm=True, а его runner
   паркует в waiting_approval.
2. Текст страницы — UNTRUSTED DATA: он не меняет права агента и не отменяет
   подтверждения.
"""
from __future__ import annotations



from bossman.agents import BASE_COMPUTER_USE_TOOLS, ToolGrant, _merge_computer_use_tools
from bossman.toolkit import REGISTRY, browser


def test_confirmed_tools_carry_confirm_true_so_runner_gates_them():
    """runner паркует задачу, когда grant.confirm или tool.confirm_default = True.

    Значит confirmed_click/confirmed_press ОБЯЗАНЫ иметь confirm=True в грантах:
    иначе платёж или отправка ушли бы без ведома человека.
    """
    base = dict(BASE_COMPUTER_USE_TOOLS)
    assert base["browser.confirmed_click"] is True
    assert base["browser.confirmed_press"] is True
    # обычные click/press/type — не подтверждаются автоматически, но и не
    # пропускают submit (см. тест ниже)
    assert base["browser.click"] is None
    assert base["browser.press"] is None


def test_every_agent_gets_computer_use_unless_opted_out():
    granted = _merge_computer_use_tools([], enabled=True)
    names = {g.name for g in granted}
    assert "browser.open" in names and "browser.confirmed_click" in names
    # confirm=True сохранён при домешивании
    assert next(g for g in granted if g.name == "browser.confirmed_press").confirm is True

    # явный опт-аут не выдаёт ничего браузерного
    assert _merge_computer_use_tools([], enabled=False) == []

    # ручная настройка агента побеждает дефолт
    manual = [ToolGrant("browser.click", confirm=True)]
    merged = _merge_computer_use_tools(manual, enabled=True)
    click = next(g for g in merged if g.name == "browser.click")
    assert click.confirm is True, "дефолт перетёр ручную настройку агента"


async def test_plain_press_refuses_submit_like_keys(monkeypatch):
    """Enter/Ctrl+Enter через обычный press не проходит — только confirmed_press.

    Это защита от инъекции: текст страницы «нажми Enter чтобы оплатить» не
    обходит approval, потому что обычный press такой ключ отвергает, а
    confirmed_press требует подтверждения человека.
    """
    class _Sess:
        class page:
            url = "https://example.com"

    async def fake_session(agent):
        return _Sess()

    monkeypatch.setattr(browser.MANAGER, "session", fake_session)

    for key in ("Enter", "Control+Enter", "Meta+Enter", "NumpadEnter"):
        res = await browser._press_impl({"key": key}, _ctx(), confirmed=False)
        assert res.error and "confirmed_press" in res.content, key


def test_page_content_is_labelled_untrusted():
    """Инструменты, возвращающие содержимое страницы, помечают его данными."""
    extract = REGISTRY["browser.extract"]
    assert "untrusted" in extract.description.lower()


def _ctx():
    from bossman.toolkit import ToolContext
    from pathlib import Path
    return ToolContext(agent="t", run_id=1, workdir=Path("."))
