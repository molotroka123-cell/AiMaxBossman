"""S3 (P1) — песочница больше не одобряет деструктивные команды сама.

REPRO: `_run_effect` возвращал None (= «не ужесточаю») для ЛЮБОЙ команды в
режиме sandbox, не попавшей в HARD_DENY/ASK_EXTRA. С выданным правом
`terminal.run` это давало `auto`, а `_tool_run` затем звал
`TerminalManager.start(..., approved=True)`. Контейнер монтирует рабочий
каталог владельца на ЗАПИСЬ (`-v {cwd}:/work` в bcc/v2/terminal_control.py),
поэтому `rm -rf /work`, `rm -rf /work/*`, `find /work -delete`,
`git clean -xfd`, `dd if=/dev/zero of=/work/db.sqlite` стирали настоящий
проект без единого вопроса владельцу.

Ожидаемое поведение: sandbox — deny-by-default как и хост. AUTO только для
явного read/build-списка; всё остальное — ask. Безобидные читающие команды
(`ls`, `pytest`, `git status`, `npm ci`) обязаны остаться auto, иначе владелец
утонет в подтверждениях — это была бы поломка другого рода.
"""
from bcc.features.tools_terminal import SPECS, _run_effect
from bcc.tools import decide_effect

SPEC = [s for s in SPECS if s.name == "terminal.run"][0]
GRANTED = {"id": "a", "permissions": ["terminal.run"]}

DESTRUCTIVE = [
    "rm -rf /work",
    "rm -rf /work/*",
    "rm -fr /",                      # порядок флагов
    "rm -rf .",
    "find /work -delete",
    "git clean -xfd",
    "dd if=/dev/zero of=/work/db.sqlite",
]
BENIGN_AUTO = ["ls", "pytest", "git status", "npm ci"]


def _effect(command: str, **extra) -> str:
    args = {"command": command, "mode": "sandbox", **extra}
    return decide_effect(SPEC, args, GRANTED)[0]


# ------------------------------------------------------------- деструктивное

def test_destructive_sandbox_commands_are_never_auto():
    for command in DESTRUCTIVE:
        assert _effect(command) != "auto", f"{command!r} одобрено само собой"


def test_destructive_sandbox_commands_reach_the_owner():
    """Не просто «не auto» — именно ask/deny, а не тихое исполнение."""
    for command in DESTRUCTIVE:
        hinted = _run_effect({"command": command, "mode": "sandbox"})
        assert hinted is not None, f"{command!r}: хук не ужесточил решение"
        assert hinted[0] in ("ask", "deny"), f"{command!r}: {hinted}"


def test_sandbox_is_the_default_mode_when_the_model_omits_it():
    """Пропущенный `mode` не должен открывать более мягкую ветку."""
    for command in DESTRUCTIVE:
        assert decide_effect(SPEC, {"command": command}, GRANTED)[0] != "auto", command


# ---------------------------------------------------------------- безобидное

def test_benign_read_and_build_commands_stay_auto():
    for command in BENIGN_AUTO:
        assert _effect(command) == "auto", f"{command!r} без нужды требует подтверждения"


def test_common_read_commands_stay_auto():
    for command in ("cat README.md", "echo hi", "git diff", "python -m pytest -q",
                    "npm run build", "ruff check ."):
        assert _effect(command) == "auto", command


# ------------------------------------------------ обход через chaining/redirect

def test_auto_prefix_cannot_smuggle_a_chained_or_redirected_command():
    """`pytest` в начале строки не делает auto всю строку."""
    for command in ("pytest; rm -rf /work",
                    "ls && rm -rf /work",
                    "git status | sh",
                    "echo x > /work/db.sqlite",
                    "cat f `rm -rf /work`",
                    "npm ci $(rm -rf /work)",
                    "env rm -rf /work"):        # запуск чужой команды, а не чтение
        assert _effect(command) != "auto", command


# -------------------------------- запуск скрипта проекта: auto, но узко

def test_project_script_run_stays_auto_so_the_path_check_can_refuse_it():
    """`python mutate.py` обязан ДОЙТИ до обработчика.

    Авторизация каталога (`within(cwd, roots)`) живёт в `_tool_run`, то есть
    ПОСЛЕ гейта. Если гейт отправит команду на approval, каталог вне корней
    владельца превратится из отказа в вопрос — владельца учат прокликивать то,
    что политика всё равно отвергнет. Класс «запусти код проекта» и так идёт
    auto (`pytest` → conftest.py, `npm run build` → package.json, `make build`)."""
    assert _effect("python mutate.py") == "auto"
    assert _effect("python3 tools/build.py --fast") == "auto"


def test_project_script_pattern_does_not_reopen_arbitrary_execution():
    """Узко: только относительный путь к .py внутри рабочего каталога."""
    for command in ('python -c "import shutil; shutil.rmtree(\'/work\')"',
                    "python /etc/evil.py",
                    "python ../evil.py",
                    "python -m evil",
                    "python mutate.py; rm -rf /work",
                    "python mutate.py > /work/db.sqlite"):
        assert _effect(command) != "auto", command


def test_the_reproduced_destructive_set_is_unaffected_by_the_script_pattern():
    """Ни одна из воспроизведённых команд S3 не проходит через новый шаблон."""
    for command in DESTRUCTIVE:
        assert _effect(command) != "auto", command


# ------------------------------------------------ прежние гарантии не ослабли

def test_host_modes_still_always_ask():
    assert _run_effect({"command": "git status", "mode": "project_host"})[0] == "ask"
    assert _run_effect({"command": "git status", "mode": "system_admin"})[0] == "ask"


def test_hard_deny_and_ask_extra_still_win():
    assert _run_effect({"command": "git push --force", "mode": "sandbox"})[0] == "deny"
    assert _run_effect({"command": "pip install requests", "mode": "sandbox"})[0] == "ask"
    assert _run_effect({"command": "sudo ls", "mode": "sandbox"})[0] == "ask"


def test_enabling_network_in_the_sandbox_still_asks():
    assert _run_effect({"command": "pytest", "mode": "sandbox", "network": True})[0] == "ask"


def test_the_hook_touches_only_terminal_run():
    """S3 меняет политику ОДНОГО инструмента: status/stdin/kill не задеты."""
    by_name = {s.name: s for s in SPECS}
    assert by_name["terminal.run"].effect_hook is _run_effect
    for name, expected in (("terminal.status", "auto"), ("terminal.stdin", "ask"),
                           ("terminal.kill", "auto")):
        assert by_name[name].effect_hook is None, name
        assert by_name[name].default_effect == expected, name
