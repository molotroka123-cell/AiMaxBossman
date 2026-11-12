"""Seed custom tools for the PC operator (agent 82): executable lessons for the narrow PC-control profile.

Each function below becomes one operator custom tool (tool.create storage, %LOCALAPPDATA%\\Bossman\\operator_tools)
via tools/pc_operator_seed_tools/install.py. Every tool is read-only except notepad_write (types into a Notepad it
opened itself and saves to the given path) and close_window (closes ONLY windows whose title contains a >=4-char
substring, via WM_CLOSE — never by process name). Output: short Russian text with the fact in the last line.
Measured reason (12.11, benchmark tools/pc_operator_bench.py): the model answered D: free space from the system-disk
tool, and PowerShell one-liners break inside terminal.run's `sh -lc` ($_ is expanded by sh).
"""
from __future__ import annotations

import argparse
import ctypes
import json
import os
import re
import shutil
import socket
import string
import subprocess
import sys
import time
from ctypes import wintypes
from pathlib import Path

if hasattr(sys.stdout, "reconfigure"):
    sys.stdout.reconfigure(encoding="utf-8")


def _ps(cmd: str) -> str:
    return subprocess.run(["powershell", "-NoProfile", "-NonInteractive", "-Command", cmd], capture_output=True,
                          text=True, encoding="utf-8", errors="replace", timeout=60).stdout.strip()


# ------------------------------------------------------------------------------------------------ disk_free
def disk_free(a):
    drives = [d for d in string.ascii_uppercase if os.path.exists(f"{d}:\\")]
    for d in drives:
        u = shutil.disk_usage(f"{d}:\\")
        print(f"{d}: свободно {u.free / 2**30:.1f} ГБ из {u.total / 2**30:.1f} ГБ")
    if a.drive:
        d = a.drive.strip().rstrip(":\\/").upper()[:1]
        if d not in drives:
            print(f"ИТОГ: диска {d}: на этом ПК нет (есть: {', '.join(x + ':' for x in drives)})")
        else:
            print(f"ИТОГ: на диске {d}: свободно {shutil.disk_usage(d + ':' + chr(92)).free / 2**30:.1f} ГБ")


# ------------------------------------------------------------------------------------------------ active_window
def active_window(a):
    u = ctypes.windll.user32
    h = u.GetForegroundWindow()
    buf = ctypes.create_unicode_buffer(1024)
    u.GetWindowTextW(h, buf, 1024)
    pid = wintypes.DWORD()
    u.GetWindowThreadProcessId(h, ctypes.byref(pid))
    print(f"hwnd={h} pid={pid.value}")
    print(f"ИТОГ: заголовок активного окна: {buf.value}")


# ------------------------------------------------------------------------------------------------ find_files
def find_files(a):
    root = Path(a.root)
    if not root.is_dir():
        print(f"ИТОГ: папки {root} нет")
        return
    ext = (a.ext or "").lower()
    if ext and not ext.startswith("."):
        ext = "." + ext
    hits = []
    for p in root.rglob("*"):
        if not p.is_file():
            continue
        if ext and p.suffix.lower() != ext:
            continue
        if a.name and a.name.lower() not in p.name.lower():
            continue
        hits.append(p)
    hits.sort(key=lambda p: p.stat().st_size, reverse=True)
    for p in hits[:40]:
        print(f"{p.stat().st_size:>10}  {p}")
    print(f"найдено файлов: {len(hits)}")
    if hits:
        print(f"самый большой: {hits[0].name} ({hits[0].stat().st_size} байт)")
    names = ", ".join(p.name for p in hits[:5])
    print(f"ИТОГ: файлов {len(hits)}" + (f"; имена: {names}" if hits else ""))


# ------------------------------------------------------------------------------------------------ json_get
def json_get(a):
    p = Path(a.path)
    text = p.read_text(encoding="utf-8-sig")
    try:
        v = json.loads(text)
    except ValueError:
        v = None
    if v is None:
        for line in text.splitlines():
            m = re.match(r"\s*([^=#;\[]+?)\s*[=:]\s*(.*)$", line)
            if m and m.group(1).strip() == a.key:
                print(f"ИТОГ: {a.key} = {m.group(2).strip()}")
                return
        print(f"ИТОГ: ключа {a.key} нет")
        return
    for part in [x for x in re.split(r"\.|\[(\d+)\]", a.key or "") if x]:
        if isinstance(v, list) and part.isdigit():
            v = v[int(part)]
        elif isinstance(v, dict) and part in v:
            v = v[part]
        else:
            print(f"ИТОГ: ключа {a.key} нет (на «{part}»)")
            return
    if isinstance(v, (list, dict)):
        print(json.dumps(v, ensure_ascii=False)[:1500])
        print(f"ИТОГ: {a.key} — {'массив' if isinstance(v, list) else 'объект'} из {len(v)} элементов")
    else:
        print(f"ИТОГ: {a.key} = {v}")


# ------------------------------------------------------------------------------------------------ count_lines
def count_lines(a):
    lines = Path(a.path).read_bytes().decode("utf-8-sig", errors="replace").splitlines()
    print(f"всего строк: {len(lines)}")
    print(f"непустых строк: {sum(1 for x in lines if x.strip())}")
    if a.contains:
        print(f"строк со словом «{a.contains}»: {sum(1 for x in lines if a.contains in x)}")
    if a.skip_header:
        print(f"строк данных без заголовка: {max(len(lines) - 1, 0)}")
    key = (f"со словом «{a.contains}»: {sum(1 for x in lines if a.contains in x)}" if a.contains else
           f"без заголовка: {max(len(lines) - 1, 0)}" if a.skip_header else f"всего: {len(lines)}")
    print(f"ИТОГ: строк {key}")


# ------------------------------------------------------------------------------------------------ processes / ports
def proc_count(a):
    name = re.sub(r"\.exe$", "", a.name or "", flags=re.I)
    out = _ps(f"Get-Process -Name '{name}' -ErrorAction SilentlyContinue | ForEach-Object {{ $_.Id }}")
    pids = [x for x in out.split() if x.isdigit()]
    print(f"PID: {', '.join(pids) or '—'}")
    print(f"ИТОГ: процессов {name}: {len(pids)} ({'запущен' if pids else 'не запущен'})")


def port_check(a):
    port = int(a.port)
    with socket.socket() as s:
        s.settimeout(0.5)
        listening = s.connect_ex(("127.0.0.1", port)) == 0
    owner = _ps(f"$c = Get-NetTCPConnection -LocalPort {port} -State Listen -ErrorAction SilentlyContinue | "
                "Select-Object -First 1; if ($c) { (Get-Process -Id $c.OwningProcess).ProcessName }")
    if owner:
        listening = True
    print(f"ИТОГ: порт {port}: {'да, слушает' if listening else 'нет, никто не слушает'}"
          + (f"; процесс: {owner}" if owner else ""))


# ------------------------------------------------------------------------------------------------ git_info
def git_info(a):
    def g(*args):
        return subprocess.run(["git", "-C", a.repo, *args], capture_output=True, text=True, encoding="utf-8",
                              errors="replace").stdout.strip()
    n = g("rev-list", "--count", "HEAD")
    msg = g("log", "-1", "--pretty=%s")
    st = g("status", "--porcelain").splitlines()
    untracked = [x for x in st if x.startswith("??")]
    modified = [x for x in st if not x.startswith("??")]
    print(f"ветка: {g('rev-parse', '--abbrev-ref', 'HEAD')}")
    print(f"коммитов: {n}")
    print(f"последний коммит: {msg}")
    print(f"неотслеживаемых файлов: {len(untracked)}; изменённых: {len(modified)}")
    print(f"ИТОГ: коммитов {n}; последний: {msg}; untracked {len(untracked)}")


# ------------------------------------------------------------------------------------------------ windows (GUI)
EnumWindowsProc = ctypes.WINFUNCTYPE(ctypes.c_bool, wintypes.HWND, wintypes.LPARAM)


def _windows() -> list[tuple[int, str, int]]:
    u = ctypes.windll.user32
    out = []

    def cb(h, _):
        if u.IsWindowVisible(h):
            buf = ctypes.create_unicode_buffer(512)
            u.GetWindowTextW(h, buf, 512)
            if buf.value:
                pid = wintypes.DWORD()
                u.GetWindowThreadProcessId(h, ctypes.byref(pid))
                out.append((int(h), buf.value, pid.value))
        return True
    u.EnumWindows(EnumWindowsProc(cb), 0)
    return out


def close_window(a):
    t = (a.title or "").strip()
    if len(t) < 4:
        print("ИТОГ: отказ — нужна часть заголовка не короче 4 символов (чужие окна не закрываю)")
        return
    hits = [w for w in _windows() if t.lower() in w[1].lower()]
    for h, title, pid in hits:
        ctypes.windll.user32.PostMessageW(h, 0x0010, 0, 0)  # WM_CLOSE
        print(f"закрываю: {title} (pid {pid})")
    time.sleep(2)
    left = [w for w in _windows() if t.lower() in w[1].lower()]
    print(f"ИТОГ: закрыто окон {len(hits) - len(left)} из {len(hits)}; осталось {len(left)}")


def list_windows(a):
    for h, title, pid in _windows():
        if not a.query or a.query.lower() in title.lower():
            print(f"hwnd={h} pid={pid} {title}")
    active_window(a)


class _KI(ctypes.Structure):
    _fields_ = [("wVk", wintypes.WORD), ("wScan", wintypes.WORD), ("dwFlags", wintypes.DWORD),
                ("time", wintypes.DWORD), ("dwExtraInfo", ctypes.c_size_t)]


class _IN(ctypes.Structure):
    class _U(ctypes.Union):
        _fields_ = [("ki", _KI), ("pad", ctypes.c_byte * 32)]
    _anonymous_ = ("u",)
    _fields_ = [("type", wintypes.DWORD), ("u", _U)]


def _key(vk=0, scan=0, flags=0):
    i = _IN(type=1)
    i.ki = _KI(vk, scan, flags, 0, 0)
    ctypes.windll.user32.SendInput(1, ctypes.byref(i), ctypes.sizeof(_IN))


def _type_text(text: str):
    for ch in text:
        if ch == "\n":
            _key(0x0D)
            _key(0x0D, flags=2)
            continue
        _key(scan=ord(ch), flags=4)       # KEYEVENTF_UNICODE
        _key(scan=ord(ch), flags=4 | 2)
        time.sleep(0.01)


def _paste_text(text: str) -> bool:
    """Put text on the clipboard (CF_UNICODETEXT) and press Ctrl+V; the previous clipboard text is restored."""
    k, u = ctypes.windll.kernel32, ctypes.windll.user32
    k.GlobalAlloc.restype = ctypes.c_void_p
    k.GlobalLock.restype = ctypes.c_void_p
    k.GlobalLock.argtypes = [ctypes.c_void_p]
    k.GlobalUnlock.argtypes = [ctypes.c_void_p]
    u.SetClipboardData.argtypes = [wintypes.UINT, ctypes.c_void_p]
    u.GetClipboardData.restype = ctypes.c_void_p
    old = None
    if not u.OpenClipboard(0):
        return False
    try:
        h = u.GetClipboardData(13)
        if h:
            pt = k.GlobalLock(h)
            old = ctypes.wstring_at(pt) if pt else None
            k.GlobalUnlock(h)
        data = (text + chr(0)).encode("utf-16-le")
        hmem = k.GlobalAlloc(0x0002, len(data))
        ptr = k.GlobalLock(hmem)
        ctypes.memmove(ptr, data, len(data))
        k.GlobalUnlock(hmem)
        u.EmptyClipboard()
        u.SetClipboardData(13, hmem)
    finally:
        u.CloseClipboard()
    _hotkey(0x11, 0x56)
    time.sleep(0.6)
    if old is not None and u.OpenClipboard(0):
        try:
            data = (old + chr(0)).encode("utf-16-le")
            hmem = k.GlobalAlloc(0x0002, len(data))
            ptr = k.GlobalLock(hmem)
            ctypes.memmove(ptr, data, len(data))
            k.GlobalUnlock(hmem)
            u.EmptyClipboard()
            u.SetClipboardData(13, hmem)
        finally:
            u.CloseClipboard()
    return True


def _hotkey(*vks):
    for v in vks:
        _key(v)
    for v in reversed(vks):
        _key(v, flags=2)


def _focus(h):
    u = ctypes.windll.user32
    _key(0x12)            # ALT tap lets SetForegroundWindow succeed from a background process
    _key(0x12, flags=2)
    u.ShowWindow(h, 9)
    u.SetForegroundWindow(h)
    time.sleep(0.5)
    return u.GetForegroundWindow() == h


def _wait_window(pred, timeout=15.0):
    end = time.time() + timeout
    while time.time() < end:
        for w in _windows():
            if pred(w):
                return w
        time.sleep(0.3)
    return None


def notepad_write(a):
    """Open a NEW Notepad window, type the text, save it as --path (Save As dialog), verify, close that window."""
    path = Path(a.path)
    path.parent.mkdir(parents=True, exist_ok=True)
    before = {w[0] for w in _windows()}
    subprocess.Popen(["notepad.exe"])
    w = _wait_window(lambda w: w[0] not in before and ("блокнот" in w[1].lower() or "notepad" in w[1].lower()))
    if not w:
        print("ИТОГ: окно Блокнота не появилось")
        return
    if not _focus(w[0]):
        print("ИТОГ: не удалось вывести окно Блокнота на передний план")
        return
    _hotkey(0x11, 0x4E)   # Ctrl+N: a fresh empty tab even if Notepad restored an old session
    time.sleep(0.8)
    if not _paste_text(a.text):
        _type_text(a.text)
    time.sleep(0.3)
    _hotkey(0x11, 0x10, 0x53)  # Ctrl+Shift+S = Save As
    dlg = _wait_window(lambda x: x[0] not in before and x[0] != w[0] and
                       any(k in x[1].lower() for k in ("сохран", "save")), timeout=10)
    if not dlg:
        print("ИТОГ: диалог «Сохранить как» не открылся")
        return
    time.sleep(0.8)
    _hotkey(0x12, 0x4E)   # Alt+N = file name field
    time.sleep(0.3)
    _hotkey(0x11, 0x41)
    if not _paste_text(str(path)):
        _type_text(str(path))
    time.sleep(0.3)
    _key(0x0D)
    _key(0x0D, flags=2)
    time.sleep(1.5)
    for x in _windows():   # «file exists, replace?» -> Yes
        if x[0] not in before and x[0] not in (w[0], dlg[0]) and "подтвер" in x[1].lower():
            _hotkey(0x12, 0x59)
    end = time.time() + 8
    while time.time() < end and not path.exists():
        time.sleep(0.3)
    got = path.read_bytes().decode("utf-8-sig", errors="replace") if path.exists() else None
    ok = got is not None and a.text in got
    print(f"файл: {path} содержимое: {got!r}" if got is not None else f"файл {path} не создан")
    if ok and not a.keep_open:
        ctypes.windll.user32.PostMessageW(w[0], 0x0010, 0, 0)
    print(f"ИТОГ: {'сохранено и проверено' if ok else 'НЕ получилось'}: {path}")


def open_in_notepad(a):
    p = Path(a.path)
    before = {w[0] for w in _windows()}
    subprocess.Popen(["notepad.exe", str(p)])
    w = _wait_window(lambda w: p.stem.lower() in w[1].lower(), timeout=15)
    print(f"ИТОГ: {'окно открыто: ' + w[1] + f' (hwnd {w[0]}, pid {w[2]})' if w else 'окно не появилось'}")
    del before


TOOLS = {
    "disk_free": (disk_free, "Свободное место на КАЖДОМ диске (C:, D:, E: ...) в ГБ; --drive D покажет один диск или "
                             "скажет, что такого диска нет. Используй вместо pc.system_info (тот видит только C:).",
                  {"drive": "буква диска, например D (необязательно)"}),
    "active_window": (active_window, "Заголовок и pid активного (переднего) окна Windows.", {}),
    "list_windows": (list_windows, "Список открытых окон (заголовок, hwnd, pid) и какое из них активное.",
                     {"query": "часть заголовка (необязательно)"}),
    "find_files": (find_files, "Найти файлы в папке рекурсивно: по расширению (--ext) и/или части имени (--name); "
                               "печатает количество, пути и самый большой файл.",
                   {"root": "папка", "ext": "расширение, например .csv", "name": "часть имени"}),
    "json_get": (json_get, "Прочитать значение из JSON по пути через точку (app.server.port, nodes) или из ini/"
                           "key=value файла по ключу; для массива печатает число элементов.",
                 {"path": "файл", "key": "путь ключа"}),
    "count_lines": (count_lines, "Посчитать строки в текстовом файле: всего, непустые, со словом (--contains), "
                                 "без заголовка CSV (--skip_header 1).",
                    {"path": "файл", "contains": "слово", "skip_header": "1 для CSV"}),
    "proc_count": (proc_count, "Сколько процессов с таким именем запущено (и их PID); запущен ли процесс.",
                   {"name": "имя процесса без .exe"}),
    "port_check": (port_check, "Слушает ли кто-то TCP-порт на этом ПК и какой процесс (имя) его слушает.",
                   {"port": "номер порта"}),
    "git_info": (git_info, "Git-репозиторий: число коммитов, сообщение последнего коммита, неотслеживаемые и "
                           "изменённые файлы (git status).", {"repo": "папка репозитория"}),
    "close_window": (close_window, "Закрыть ТОЛЬКО окна, в заголовке которых есть заданная строка (>=4 символов), "
                                   "мягко (WM_CLOSE). Не закрывает процессы по имени.",
                     {"title": "часть заголовка, например имя файла"}),
    "notepad_write": (notepad_write, "Открыть Блокнот (notepad), напечатать текст клавиатурой, сохранить как файл "
                                     "по пути и проверить содержимое; окно потом закрывается.",
                      {"path": "куда сохранить", "text": "текст"}),
    "open_in_notepad": (open_in_notepad, "Открыть файл в Блокноте (notepad) и дождаться окна (печатает hwnd, pid).",
                        {"path": "файл"}),
}


def main():
    fixed = globals().get("_TOOL_NAME")
    name = fixed or sys.argv[1]
    argv = sys.argv[1:] if fixed else sys.argv[2:]
    ap = argparse.ArgumentParser()
    for k in TOOLS[name][2]:
        ap.add_argument(f"--{k}", default=None)
    ap.add_argument("--keep_open", default=None)
    TOOLS[name][0](ap.parse_args(argv))


if __name__ == "__main__":  # install.py inserts _TOOL_NAME above this line
    main()
