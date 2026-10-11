"""Owner-only remote-access commands of the pult (owner request, 2026-10-11).

The owner leaves the PC without a display and wants, from the phone:
  /remote          start or restore Claude Code Remote Control and get the current link;
  /remote_status   processes, connection marker, last errors;
  /claude_open     open an interactive Claude CLI in the configured checkout;
  /chatgpt         launch the installed ChatGPT desktop app (it cannot attach to a
                   specific Codex thread — that limitation is stated, not hidden);
  /reboot          controlled reboot: checkpoint, no active renders, explicit confirm.
«Продолжи Claude» is the existing /claude <text> (resumes the recorded session).

Rules kept from the Claude bridge: owner identity is checked on every call and again
before delivery; everything needs ``claude_bridge: true`` in the local config; STOP
(``delegation_locked``) blocks launches and reboots; no raw shell from Telegram; the
password of the Windows account is never read, stored or sent. A repeated /remote never
spawns a second Remote Control: a live process with our session name is reported instead.
Processes started by somebody else are never killed here.
"""
from __future__ import annotations

import json
import os
import re
import subprocess
import time
from pathlib import Path

from .agent_bridge import AGENT_DENIED, REPO_ROOT
from .config import Person

REMOTE_COMMANDS = {"/remote", "/remote_status", "/remote_stop", "/claude_open", "/chatgpt",
                   "/reboot", "/reboot_confirm", "/reboot_cancel"}
# Natural phrases the owner uses in the pult → command. Matched on the lower-cased start of the text.
PHRASES = (
    ("включи удалённый claude", "/remote"), ("включи удаленный claude", "/remote"),
    ("удалённый claude", "/remote"), ("удаленный claude", "/remote"),
    ("статус удалённого доступа", "/remote_status"), ("статус удаленного доступа", "/remote_status"),
    ("открой chatgpt", "/chatgpt"), ("открой чат гпт", "/chatgpt"),
    ("открой claude", "/claude_open"), ("открой клод", "/claude_open"),
    ("продолжи claude", "/claude"), ("продолжи клод", "/claude"),
    ("перезагрузи компьютер", "/reboot"), ("перезагрузи комп", "/reboot"),
)
LINK_RE = re.compile(r"https://claude\.ai/code/session_[A-Za-z0-9]+")
CONNECTED_RE = re.compile(r"\bConnected\b")
# A reboot must not interrupt renders, training or another executor's long job.
BUSY_PATTERNS = ("facefusion", "sd-cli", "sd.exe", "kohya", "train_network", "accelerate launch",
                 "ffmpeg", "wan2", "vace")
REBOOT_DELAY_S = 90
CONFIRM_WINDOW_S = 300
LAUNCHER = REPO_ROOT / "tools" / "claude_remote_service.ps1"
# One flag instead of os.name checks: tests toggle it without touching pathlib (a patched
# os.name makes Path() pick WindowsPath on Linux CI and crash).
IS_WINDOWS = os.name == "nt"
# Constants, not config keys: the installed companion of an older build rejects unknown
# keys in config.json (Settings(**data)), and one config file serves both builds.
SESSION_NAME = "Bossman-Owner-PC"
CHATGPT_APP_ID = "OpenAI.Codex_2p2nqsd0c76g0!App"
STARTER_MODES = ("default", "acceptEdits", "bypassPermissions")   # ValidateSet of start_claude_remote.ps1


def remote_dir() -> Path:
    base = os.environ.get("LOCALAPPDATA") or str(Path.home() / ".local" / "share")
    return Path(base) / "Bossman" / "remote"


def _powershell_json(script: str, timeout: float = 20) -> list[dict]:
    """Run a read-only PowerShell query and return its JSON list. Empty on any failure."""
    try:
        proc = subprocess.run(["powershell", "-NoProfile", "-NonInteractive", "-Command", script],
                              capture_output=True, timeout=timeout)
        out = proc.stdout.decode("utf-8", "replace").strip()
    except (OSError, subprocess.SubprocessError):
        return []
    if not out:
        return []
    try:
        data = json.loads(out)
    except ValueError:
        return []
    if isinstance(data, dict):
        data = [data]
    return [d for d in data if isinstance(d, dict)]


def list_processes(pattern: str) -> list[dict]:
    """Processes whose command line contains ``pattern`` (case-insensitive). Windows only;
    elsewhere empty (the pult runs on the owner's Windows PC)."""
    if not IS_WINDOWS:
        return []
    safe = pattern.replace("'", "''")
    script = ("Get-CimInstance Win32_Process | Where-Object { $_.CommandLine -and "
              f"$_.CommandLine.ToLower().Contains('{safe.lower()}') }} | "
              "Select-Object ProcessId,ParentProcessId,CreationDate,CommandLine | ConvertTo-Json -Compress")
    return _powershell_json(script)


def remote_processes(session_name: str) -> list[dict]:
    rows = list_processes("remote-control")
    return [r for r in rows if session_name.lower() in str(r.get("CommandLine", "")).lower()
            and "--help" not in str(r.get("CommandLine", ""))]


def busy_processes() -> list[str]:
    found = []
    for row in list_processes(""):
        cl = str(row.get("CommandLine", "")).lower()
        if any(p in cl for p in BUSY_PATTERNS):
            found.append(f"{row.get('ProcessId')}: {cl[:90]}")
    return found


def read_log(path: Path, limit: int = 200_000) -> str:
    try:
        with path.open("rb") as fh:
            fh.seek(0, os.SEEK_END)
            size = fh.tell()
            fh.seek(max(0, size - limit))
            return fh.read().decode("utf-8", "replace")
    except OSError:
        return ""


def last_link(text: str) -> str | None:
    found = LINK_RE.findall(text)
    return found[-1] if found else None


def spawn_detached(args: list[str], *, cwd: str) -> int:
    """Start a process that survives the companion. Returns its pid."""
    flags = 0
    if IS_WINDOWS:
        flags = subprocess.DETACHED_PROCESS | subprocess.CREATE_NEW_PROCESS_GROUP
    proc = subprocess.Popen(args, cwd=cwd, creationflags=flags, stdin=subprocess.DEVNULL,
                            stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL, close_fds=True)
    return proc.pid


class RemoteAccessMixin:
    """Mixed into Companion next to AgentBridgeMixin (needs agent_policy, store, button)."""

    # Seams for tests and for a non-Windows host.
    _remote_processes = staticmethod(remote_processes)
    _busy_processes = staticmethod(busy_processes)
    _spawn = staticmethod(spawn_detached)
    _run_shutdown = staticmethod(lambda args: subprocess.run(args, check=False, timeout=30))

    @staticmethod
    def phrase_command(text: str) -> tuple[str, str] | None:
        low = text.strip().lower()
        for phrase, command in PHRASES:
            if low == phrase or low.startswith((phrase + " ", phrase + ",", phrase + ":")):
                return command, text.strip()[len(phrase):].lstrip(" ,:").strip()
        return None

    def _remote_settings(self, person: Person, message: dict | None):
        current = self.agent_policy(person, message)
        if current is None:
            self._agent_log({"who": getattr(person, "key", None), "command": "remote", "result": "denied"})
            return None
        return current

    async def remote_command(self, person: Person, command: str, arg: str, message: dict):
        from .service import Reply
        current = self._remote_settings(person, message)
        if current is None:
            return AGENT_DENIED
        if not current.claude_bridge:
            return "Удалённый доступ выключен локально (claude_bridge: false в config.json на компьютере)."
        if command == "/remote_status":
            return Reply(self.remote_status_text(current), self.remote_menu(person))
        if command == "/reboot_cancel":
            return self.reboot_cancel(person)
        # STOP blocks every launch (independent review, Mistral 11.10: app launches too); only
        # the read-only status and cancelling a pending reboot stay available.
        if self.store.get("delegation_locked", False):
            return "СТОП активен: запуски и перезагрузка заблокированы. Снять — /resume."
        if command == "/chatgpt":
            return self.open_chatgpt(current)
        if command == "/claude_open":
            return self.open_claude(current)
        if command == "/remote":
            return Reply(self.remote_start(current), self.remote_menu(person))
        if command == "/remote_stop":
            return self.remote_stop(current)
        if command == "/reboot":
            return self.reboot_prepare(person, current, force=arg.lower() in {"force", "принудительно"})
        if command == "/reboot_confirm":
            return self.reboot_confirm(person, current)
        return AGENT_DENIED

    # ------------------------------------------------------------ remote control
    @staticmethod
    def remote_mode(current) -> str:
        """The owner's Claude permission mode (claude_permission_mode) when the starter accepts it."""
        mode = getattr(current, "claude_permission_mode", "default")
        return mode if mode in STARTER_MODES else "default"

    def remote_start(self, current) -> str:
        name = SESSION_NAME
        mode = self.remote_mode(current)
        live = self._remote_processes(name)
        log = remote_dir() / "claude-remote.log"
        if live:
            link = last_link(read_log(log))
            pids = ", ".join(str(r.get("ProcessId")) for r in live)
            return (f"Remote Control «{name}» уже работает (PID {pids}); второй экземпляр не запускаю.\n"
                    + (f"Ссылка: {link}" if link else
                       "Ссылку из этого запуска в журнале не вижу — откройте claude.ai/code и выберите сессию "
                       f"«{name}»."))
        if not LAUNCHER.is_file():
            return f"Нет сценария запуска {LAUNCHER.name} в checkout — обновите исходники."
        # Remote Control always runs from the checkout the pult itself runs from: the
        # starter script verifies that branch, so a foreign claude_cwd cannot be used.
        cwd = str(REPO_ROOT)
        remote_dir().mkdir(parents=True, exist_ok=True)
        args = ["powershell", "-NoProfile", "-ExecutionPolicy", "Bypass", "-File", str(LAUNCHER),
                "-Checkout", cwd, "-Name", name, "-PermissionMode", mode, "-Once"]
        try:
            pid = self._spawn(args, cwd=cwd)
        except OSError as exc:
            self._agent_log({"command": "/remote", "result": "spawn_error", "error": type(exc).__name__})
            return f"Не удалось запустить сценарий: {type(exc).__name__}"
        self.store.put("remote_launch", {"pid": pid, "at": time.time(), "name": name})
        self._agent_log({"command": "/remote", "result": "started", "launcher_pid": pid})
        return (f"Запускаю Remote Control «{name}» (режим {mode}). "
                "Через 20–60 с нажмите «Статус» — пришлю ссылку, когда она появится в журнале.")

    def remote_status_text(self, current) -> str:
        name = SESSION_NAME
        live = self._remote_processes(name)
        log = remote_dir() / "claude-remote.log"
        text = read_log(log)
        link = last_link(text)
        lines = [f"Remote Control «{name}»: " + (f"работает, PID {', '.join(str(r.get('ProcessId')) for r in live)}"
                                                 if live else "не запущен")]
        tail = text[-4000:]
        if CONNECTED_RE.search(tail):
            lines.append("Подключение: Connected (по журналу).")
        elif text:
            lines.append("Подключение: маркера Connected в последних строках журнала нет.")
        else:
            lines.append("Журнал пуст: запусков через пульт ещё не было.")
        lines.append(f"Ссылка: {link}" if link else "Ссылка: не найдена в журнале.")
        errors = [l for l in tail.splitlines() if re.search(r"error|failed|exception|ошибк", l, re.I)][-3:]
        if errors:
            lines.append("Последние ошибки:\n" + "\n".join(l.strip()[:160] for l in errors))
        launch = self.store.get("remote_launch")
        if isinstance(launch, dict):
            lines.append(f"Последний запуск из пульта: {time.strftime('%H:%M:%S', time.localtime(launch.get('at', 0)))}")
        if self.store.get("delegation_locked", False):
            lines.append("СТОП активен.")
        return "\n".join(lines)

    def remote_stop(self, current) -> str:
        """Stops only the Remote Control that the pult launcher started (recorded pid tree);
        a Remote Control started by hand or by another executor is left alone."""
        launch = self.store.get("remote_launch")
        if not isinstance(launch, dict) or not launch.get("pid"):
            return "Из пульта ничего не запускалось — чужой Remote Control не трогаю."
        if IS_WINDOWS:
            self._run_shutdown(["taskkill", "/T", "/F", "/PID", str(launch["pid"])])
        (remote_dir() / "STOP").write_text("pult", encoding="utf-8")
        self.store.put("remote_launch", None)
        return "Остановил Remote Control, запущенный из пульта, и поставил STOP для автоперезапуска."

    # ------------------------------------------------------------ apps
    def open_chatgpt(self, current) -> str:
        app_id = CHATGPT_APP_ID
        if not IS_WINDOWS:
            return "Запуск ChatGPT доступен только на Windows-ПК владельца."
        try:
            self._spawn(["explorer.exe", f"shell:AppsFolder\\{app_id}"], cwd=str(Path.home()))
        except OSError as exc:
            return f"Не удалось запустить ChatGPT: {type(exc).__name__}"
        return ("Открыл приложение ChatGPT. Ограничение: приложение не умеет открыть конкретный диалог Codex "
                "по команде — нужный чат выбирается внутри приложения; запущенный процесс не означает "
                "восстановленную связь с диалогом.")

    def open_claude(self, current) -> str:
        cwd = current.claude_cwd or str(REPO_ROOT.parent)
        if not Path(cwd).is_dir():
            return f"Папка claude_cwd не найдена: {cwd}"
        if not IS_WINDOWS:
            return "Открытие терминала доступно только на Windows-ПК владельца."
        try:
            self._spawn(["cmd", "/c", "start", "Claude Code", "cmd", "/k", "claude"], cwd=cwd)
        except OSError as exc:
            return f"Не удалось открыть терминал: {type(exc).__name__}"
        return (f"Открыл терминал с Claude CLI в {cwd}. Без монитора это окно никто не видит — для работы "
                "с телефона используйте /remote. Продолжить прошлую сессию текстом: /claude <задание>.")

    # ------------------------------------------------------------ reboot
    def reboot_prepare(self, person: Person, current, *, force: bool):
        from .service import Reply
        busy = [] if force else self._busy_processes()
        if busy:
            return ("Перезагрузку не начинаю: идут рендер/обучение или чужая долгая задача:\n"
                    + "\n".join(busy[:6]) + "\nДождитесь окончания или /reboot force.")
        checkpoint = self._reboot_checkpoint(current)
        self.store.put("reboot_pending", {"until": time.time() + CONFIRM_WINDOW_S, "checkpoint": str(checkpoint)})
        live = self._remote_processes(SESSION_NAME)
        note = ("Remote Control сейчас работает; после перезагрузки его поднимет задача автозапуска."
                if live else "Remote Control сейчас не запущен; после перезагрузки его поднимет задача автозапуска.")
        return Reply(f"Checkpoint сохранён: {checkpoint.name}. {note}\n"
                     f"Подтвердите перезагрузку (окно {CONFIRM_WINDOW_S // 60} мин):",
                     [[self.button(person, "🔁 Перезагрузить", "/reboot_confirm"),
                       self.button(person, "✖ Отмена", "/reboot_cancel")]])

    def _reboot_checkpoint(self, current) -> Path:
        remote_dir().mkdir(parents=True, exist_ok=True)
        path = remote_dir() / time.strftime("reboot-checkpoint-%Y%m%d-%H%M%S.json")
        cwd = current.claude_cwd or str(REPO_ROOT.parent)
        status = head = ""
        try:
            status = subprocess.run(["git", "status", "--short"], cwd=cwd, capture_output=True, timeout=30
                                    ).stdout.decode("utf-8", "replace")
            head = subprocess.run(["git", "rev-parse", "--short", "HEAD"], cwd=cwd, capture_output=True,
                                  timeout=30).stdout.decode("utf-8", "replace").strip()
        except (OSError, subprocess.SubprocessError):
            pass
        data = {"at": time.strftime("%Y-%m-%dT%H:%M:%S"), "head": head, "dirty_lines": len(status.splitlines()),
                "remote_link": last_link(read_log(remote_dir() / "claude-remote.log")),
                "remote_live": bool(self._remote_processes(SESSION_NAME))}
        path.write_text(json.dumps(data, ensure_ascii=False, indent=1), encoding="utf-8")
        return path

    def reboot_confirm(self, person: Person, current) -> str:
        pending = self.store.get("reboot_pending")
        if not isinstance(pending, dict) or time.time() > pending.get("until", 0):
            return "Нет подготовленной перезагрузки или окно подтверждения истекло — сначала /reboot."
        self.store.put("reboot_pending", None)
        if not IS_WINDOWS:
            return "Перезагрузка выполняется только на Windows-ПК владельца."
        self._run_shutdown(["shutdown", "/r", "/t", str(REBOOT_DELAY_S), "/c", "Bossman: перезагрузка по команде владельца"])
        self._agent_log({"command": "/reboot_confirm", "result": "scheduled", "delay_s": REBOOT_DELAY_S})
        return (f"Перезагрузка через {REBOOT_DELAY_S} с. Отменить: /reboot_cancel. После входа автозапуск поднимет "
                "Bossman и Remote Control; статус — /remote_status.")

    def reboot_cancel(self, person: Person) -> str:
        self.store.put("reboot_pending", None)
        if IS_WINDOWS:
            self._run_shutdown(["shutdown", "/a"])
        return "Перезагрузка отменена."

    def remote_menu(self, person: Person):
        b = self.button
        return [[b(person, "📡 Статус", "/remote_status"), b(person, "🔗 Включить удалённый Claude", "/remote")],
                [b(person, "💬 Открыть ChatGPT", "/chatgpt"), b(person, "🔁 Перезагрузить", "/reboot")]]
