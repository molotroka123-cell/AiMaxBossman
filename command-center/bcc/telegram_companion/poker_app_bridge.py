"""/poker_app — the owner turns the public Poker Train Mini App on/off and sees its status (owner 14.10).

The game host is a separate process (`apps/poker-train-host`, `python -m pokerhost run`): static game + signed
leaderboard on 127.0.0.1, published through a Cloudflare quick tunnel; players open it from the Jeff bot's menu
button «🃏 Покер», the owner also gets the button in this pult chat. The pult only starts/stops that process and
reads its state file — it never serves anything to strangers itself.
"""
from __future__ import annotations

import json
import os
import subprocess
import sys
import time
from pathlib import Path

POKER_APP_COMMANDS = {"/poker_app"}
HOST_DIR = Path(__file__).resolve().parents[3] / "apps" / "poker-train-host"
DATA = Path(os.environ.get("LOCALAPPDATA", str(Path.home()))) / "Bossman" / "poker-host"
ON = {"on", "start", "вкл", "включи", "запусти", "старт"}
OFF = {"off", "stop", "выкл", "выключи", "останови", "стоп"}


def _state() -> dict:
    try:
        return json.loads((DATA / "state.json").read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return {}


def _alive(pid) -> bool:
    if not isinstance(pid, int) or pid <= 0:
        return False
    try:
        import psutil
        return psutil.pid_exists(pid)
    except ImportError:
        return True


def _start() -> str:
    if not (DATA / "dist" / "index.html").is_file():
        return f"Нет сборки игры в {DATA / 'dist'} — сначала соберите pokertrain (npm run build) и скопируйте dist."
    (DATA / "STOP").unlink(missing_ok=True)
    log = open(DATA / "host.log", "a", encoding="utf-8")                                # noqa: SIM115
    flags = getattr(subprocess, "DETACHED_PROCESS", 0) | getattr(subprocess, "CREATE_NEW_PROCESS_GROUP", 0)
    subprocess.Popen([sys.executable, "-m", "pokerhost", "run"], cwd=str(HOST_DIR), stdout=log, stderr=log,
                     stdin=subprocess.DEVNULL, creationflags=flags, close_fds=True)
    return "Запускаю хост покера: сервер + туннель, кнопка «🃏 Покер» появится через ~10–20 с. Статус: /poker_app"


def poker_app_text(person, arg: str) -> str:
    if getattr(person, "role", "") != "owner":
        return "Управление покер-приложением доступно только владельцу. Играть — кнопка «🃏 Покер» в боте Jeff."
    word = (arg or "").strip().lower()
    st = _state()
    running = st.get("status") == "running" and _alive(st.get("pid"))
    if word in ON:
        return "Хост уже работает:\n" + st.get("url", "") if running else _start()
    if word in OFF:
        if not running:
            return "Хост покера не запущен."
        DATA.mkdir(parents=True, exist_ok=True)
        (DATA / "STOP").write_text("stop", encoding="utf-8")
        return "Останавливаю: кнопки «🃏 Покер» снимаются, туннель и сервер закрываются (несколько секунд)."
    if not running:
        tail = f" (последнее: {st.get('status')}{', ' + st['reason'] if st.get('reason') else ''})" if st else ""
        return f"🃏 Покер-тренажёр для всех: выключен{tail}.\nВключить: /poker_app вкл"
    up = int(time.time() - float(st.get("started", time.time())))
    players = "?"
    try:
        import urllib.request
        with urllib.request.urlopen(f"http://127.0.0.1:{st.get('port', 8810)}/api/health", timeout=3) as r:
            players = json.loads(r.read()).get("players", "?")
    except Exception:            # noqa: BLE001 — status must answer even if the host is busy
        pass
    buttons = st.get("buttons") or {}
    return ("🃏 Покер-тренажёр для всех: РАБОТАЕТ\n"
            f"• Адрес: {st.get('url')}\n"
            f"• Работает: {up // 3600} ч {up % 3600 // 60} мин · игроков в таблице: {players}\n"
            f"• Кнопка в боте Jeff для всех: {'да' if buttons.get('jeff_all_users') else 'НЕТ'}; "
            f"у вас в пульте: {'да' if buttons.get('pult_owner_only') else 'НЕТ'}\n"
            "• Игра идёт в браузере игрока против ИИ; на вашем ПК — только сайт и таблица лидеров. Денег нет, "
            "платная модель выключена. Адрес меняется при каждом запуске (бесплатный туннель).\n"
            "Выключить: /poker_app выкл")
