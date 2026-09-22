"""Прямого пути исполнения из Telegram больше нет (раздел 5 ТЗ владельца).

Раньше этот файл проверял, что /sh и /claude РАБОТАЮТ. Теперь он проверяет
обратное свойство: второго агента с прямым доступом к shell и мыши в Telegram
не существует ни для кого — ни для гостя, ни для владельца, ни при включённом
тумблере. Гость и выключенный тумблер по-прежнему получают отказ, а не эффект.
"""
from __future__ import annotations

import ast
import asyncio
from pathlib import Path

import pytest

from bcc.telegram_companion import service
from bcc.telegram_companion.config import Person, Settings
from bcc.telegram_companion.console import CONSOLE_OFF, NO_DIRECT_SHELL
from bcc.telegram_companion.service import Companion
from bcc.telegram_companion.store import Store

OWNER = Person(11111, 11111, 'owner', None)
GUEST = Person(22222, 22222, 'guest', None)
PACKAGE = Path(service.__file__).parent


def msg(p, text):
    return {'from': {'id': p.user_id, 'is_bot': False}, 'chat': {'id': p.chat_id, 'type': 'private'}, 'text': text}


class FakeTelegram:
    def __init__(self):
        self.sent = []

    async def send(self, person, text, keyboard=None):
        self.sent.append((person, str(text)))

    async def call(self, *a, **k):
        return True


def build(tmp_path, **kw):
    s = Settings((OWNER, GUEST), local_url='http://127.0.0.1:8083/v1', local_model='best', bot_token='x', **kw)
    return Companion(s, Store(tmp_path), FakeTelegram(), None, None)


def test_direct_shell_and_claude_no_longer_exist(tmp_path):
    """Даже владелец с включённым пультом не получает ни shell, ни Claude Code."""
    c = build(tmp_path, pc_control=True)
    for text in ('/sh Get-Date', '/claude почини всё', '/mode claude', '/claude_new', '/claude_stop', '/pc'):
        assert asyncio.run(c.handle(OWNER, msg(OWNER, text))) == NO_DIRECT_SHELL
    # Обычный текст при выставленном старом «режиме Claude» уходит в локальную
    # модель чата (здесь её нет — AttributeError), а не в исполнитель команд.
    c.store.put('mode:' + OWNER.key, 'claude')
    with pytest.raises(AttributeError):
        asyncio.run(c.handle(OWNER, msg(OWNER, 'проверь диск')))


def test_disabled_by_default_and_never_for_guests(tmp_path):
    off = build(tmp_path / 'a')
    assert asyncio.run(off.handle(OWNER, msg(OWNER, '/sh Get-Date'))) == CONSOLE_OFF
    assert asyncio.run(off.handle(OWNER, msg(OWNER, '/approvals'))) == CONSOLE_OFF
    assert asyncio.run(off.handle(OWNER, msg(OWNER, '/stop'))) == CONSOLE_OFF
    on = build(tmp_path / 'b', pc_control=True)
    for text in ('/sh Get-Date', '/claude hi', '/screen', '/mode claude', '/pc', '/approvals', '/stop', '/resume'):
        assert asyncio.run(on.handle(GUEST, msg(GUEST, text))) == CONSOLE_OFF
    assert not any('СТОП' in label for row in on.main_menu(GUEST) for label, _ in row)
    assert any('СТОП' in label for row in on.main_menu(OWNER) for label, _ in row)


def test_pc_control_module_is_gone_and_package_starts_no_processes():
    """Ни модуля прямого исполнения, ни вызова subprocess/PowerShell в пакете."""
    assert not (PACKAGE / 'pc_control.py').exists()
    banned = {'subprocess', 'pty', 'pyautogui', 'pywinauto'}
    hits = []
    for file in PACKAGE.glob('*.py'):
        tree = ast.parse(file.read_text(encoding='utf-8'))
        for node in ast.walk(tree):
            names = ([a.name for a in node.names] if isinstance(node, ast.Import) else
                     [node.module or ''] if isinstance(node, ast.ImportFrom) else [])
            hits += [(file.name, n) for n in names if n.split('.')[0] in banned]
            if isinstance(node, ast.Attribute) and node.attr in {'create_subprocess_exec', 'create_subprocess_shell'}:
                hits.append((file.name, node.attr))
    assert hits == []


def test_removed_permission_mode_field_is_not_a_setting():
    """Поле bypassPermissions удалено вместе с путём, которому оно давало права."""
    with pytest.raises(TypeError):
        Settings((OWNER,), pc_control=True, claude_permission_mode='bypassPermissions')
    assert not hasattr(Settings((OWNER,), pc_control=True), 'claude_permission_mode')


def test_old_config_with_retired_fields_still_loads(tmp_path):
    """Сохранённый до удаления config.json грузится, но прав уже не даёт."""
    import json
    from bcc.telegram_companion.config import load
    path = tmp_path / 'config.json'
    path.write_text(json.dumps({
        'people': [{'user_id': 11111, 'chat_id': 11111, 'role': 'owner'}],
        'local_url': 'http://127.0.0.1:8083/v1', 'local_model': 'best', 'pc_control': True,
        'claude_cwd': r'C:\Users\asd', 'claude_permission_mode': 'bypassPermissions',
        'claude_timeout': 1800, 'bossman_launch': 'Start-Process bossman.exe'}), encoding='utf-8')
    settings = load(path)
    assert settings.pc_control is True
    for retired in ('claude_cwd', 'claude_permission_mode', 'claude_timeout', 'bossman_launch'):
        assert not hasattr(settings, retired)
