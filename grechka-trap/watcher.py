# Grechka trap: pulpit media -> DPAPI .enc into Grechka/Oldy.
# One-off transfer + recurring 15-min watcher. Token and owner chat/user ID
# are read from companion.env / companion.json and NEVER printed.
# Never lists Grechka contents.
from __future__ import annotations

import ctypes
import hashlib
import json
import os
import random
import string
import time
import urllib.request
import uuid
from ctypes import wintypes

LOCALAPPDATA = os.environ.get("LOCALAPPDATA", "")
COMPANION_DIR = os.path.join(LOCALAPPDATA, "Bossman", "telegram-companion")
ENV_PATH = os.path.join(COMPANION_DIR, "companion.env")
CONFIG_PATH = os.path.join(COMPANION_DIR, "companion.json")
GRECHKA = "".join(chr(c) for c in (1043, 1088, 1077, 1095, 1082, 1072))
OLDY = "".join(chr(c) for c in (1054, 1083, 1076, 1099))
DEST = os.path.join("C:\\Users\\asd\\Bossman", GRECHKA, OLDY)
TRAP_DIR = os.path.join("C:\\Users\\asd\\Bossman", "grechka-trap")
STATE_PATH = os.path.join(TRAP_DIR, "state.json")
LIMIT_FIRST_RUN = 16
MAX_DL = 20 * 1024 * 1024


def load_token(env_path: str = ENV_PATH) -> str:
    with open(env_path, "r", encoding="utf-8") as f:
        for line in f:
            line = line.strip()
            if line.startswith("TG_COMPANION_BOT_TOKEN="):
                return line.split("=", 1)[1].strip()
    raise SystemExit("token not found")


def load_owner_id(env_path: str = ENV_PATH, config_path: str = CONFIG_PATH) -> int:
    if os.path.exists(env_path):
        with open(env_path, "r", encoding="utf-8") as f:
            for line in f:
                line = line.strip()
                for key in ("TG_COMPANION_OWNER_ID=", "TG_OWNER_ID=", "TG_CHAT_ID="):
                    if line.startswith(key):
                        val = line.split("=", 1)[1].strip()
                        if val.lstrip("-").isdigit():
                            return int(val)
    if os.path.exists(config_path):
        with open(config_path, "r", encoding="utf-8") as f:
            cfg = json.load(f)
        for key in ("owner_user_id", "owner_chat_id", "allowed_user_id", "chat_id"):
            val = cfg.get(key)
            if isinstance(val, int) or (isinstance(val, str) and val.lstrip("-").isdigit()):
                return int(val)
    raise SystemExit("owner_id not found")


def api(token: str, method: str, **params):
    url = "https://api.telegram.org/bot" + token + "/" + method
    data = json.dumps(params).encode() if params else None
    req = urllib.request.Request(url, data=data, headers={"Content-Type": "application/json"})
    with urllib.request.urlopen(req, timeout=60) as r:
        return json.loads(r.read().decode())


def download(token: str, file_path: str, out_path: str) -> None:
    url = "https://api.telegram.org/file/bot" + token + "/" + file_path
    with urllib.request.urlopen(url, timeout=180) as r, open(out_path, "wb") as f:
        while True:
            chunk = r.read(1 << 20)
            if not chunk:
                break
            f.write(chunk)


class BLOB(ctypes.Structure):
    _fields_ = [("cbData", wintypes.DWORD), ("pbData", ctypes.POINTER(ctypes.c_ubyte))]


def dpapi_encrypt(data: bytes) -> bytes:
    crypt32 = ctypes.windll.crypt32
    kernel32 = ctypes.windll.kernel32
    blob_in = BLOB(len(data), (ctypes.c_ubyte * len(data)).from_buffer_copy(data))
    blob_out = BLOB()
    ok = crypt32.CryptProtectData(ctypes.byref(blob_in), None, None, None, None, 0x1, ctypes.byref(blob_out))
    if not ok:
        raise RuntimeError("CryptProtectData failed: %d" % kernel32.GetLastError())
    try:
        return bytes(bytearray(blob_out.pbData[: blob_out.cbData]))
    finally:
        kernel32.LocalFree(blob_out.pbData)


def media_of(msg: dict):
    if "photo" in msg and msg["photo"]:
        biggest = msg["photo"][-1]
        return ("photo", biggest["file_id"], biggest.get("file_size", 0))
    if "video" in msg:
        return ("video", msg["video"]["file_id"], msg["video"].get("file_size", 0))
    if "document" in msg:
        return ("document", msg["document"]["file_id"], msg["document"].get("file_size", 0))
    if "video_note" in msg:
        return ("video_note", msg["video_note"]["file_id"], msg["video_note"].get("file_size", 0))
    if "animation" in msg:
        return ("animation", msg["animation"]["file_id"], msg["animation"].get("file_size", 0))
    return None


def is_owner_message(msg: dict, owner_id: int) -> bool:
    """Strictly verify that BOTH chat.id and from.id belong to the owner."""
    if not isinstance(msg, dict) or not owner_id:
        return False
    chat = msg.get("chat")
    sender = msg.get("from")
    if not isinstance(chat, dict) or not isinstance(sender, dict):
        return False
    try:
        chat_id = int(chat.get("id"))
        from_id = int(sender.get("id"))
    except (TypeError, ValueError):
        return False
    return chat_id == int(owner_id) and from_id == int(owner_id)


def dedup_key(chat_id: int, message_id: int) -> str:
    return f"{int(chat_id)}:{int(message_id)}"


def parse_seen(raw_seen, owner_id: int) -> set[tuple[int, int]]:
    out: set[tuple[int, int]] = set()
    for item in raw_seen or []:
        if isinstance(item, list | tuple) and len(item) == 2:
            try:
                out.add((int(item[0]), int(item[1])))
            except (TypeError, ValueError):
                continue
        elif isinstance(item, str) and ":" in item:
            c_str, _, m_str = item.partition(":")
            try:
                out.add((int(c_str), int(m_str)))
            except (TypeError, ValueError):
                continue
        elif isinstance(item, int):
            out.add((int(owner_id), int(item)))
    return out


def filter_owner_candidates(
    updates: list[dict],
    owner_id: int,
    seen: set[tuple[int, int]],
    *,
    first_run: bool = False,
    limit_first_run: int = LIMIT_FIRST_RUN,
) -> list[tuple[int, int, str, str, int]]:
    """Filter updates to owner-only messages and deduplicate by (chat_id, message_id)."""
    cands: list[tuple[int, int, str, str, int]] = []
    batch_keys: set[tuple[int, int]] = set()
    for upd in updates or []:
        msg = (upd or {}).get("message") or {}
        if not is_owner_message(msg, owner_id):
            continue
        m = media_of(msg)
        if not m:
            continue
        chat_id = int(msg["chat"]["id"])
        mid = int(msg["message_id"])
        key = (chat_id, mid)
        if key in batch_keys:
            continue
        batch_keys.add(key)
        cands.append((chat_id, mid, m[0], m[1], m[2]))
    cands.sort(key=lambda x: (x[0], x[1]))
    if first_run and len(cands) > limit_first_run:
        for chat_id, mid, _, _, _ in cands[:-limit_first_run]:
            seen.add((chat_id, mid))
        todo = [c for c in cands[-limit_first_run:] if (c[0], c[1]) not in seen]
    else:
        todo = [c for c in cands if (c[0], c[1]) not in seen]
    return todo


def acquire_token_poller_lock(token: str):
    import msvcrt

    fp = hashlib.sha256(str(token or "").encode("utf-8")).hexdigest()[:24]
    lock_dir = os.path.join(LOCALAPPDATA, "Bossman", "telegram-pollers")
    os.makedirs(lock_dir, exist_ok=True)
    lock_file = open(os.path.join(lock_dir, f"{fp}.lock"), "a+b")
    try:
        if lock_file.seek(0, 2) == 0:
            lock_file.write(b"0")
            lock_file.flush()
        lock_file.seek(0)
        msvcrt.locking(lock_file.fileno(), msvcrt.LK_NBLCK, 1)
        return lock_file
    except OSError:
        lock_file.close()
        return None


def release_token_poller_lock(lock_file) -> None:
    if lock_file is None:
        return
    import msvcrt

    try:
        lock_file.seek(0)
        msvcrt.locking(lock_file.fileno(), msvcrt.LK_UNLCK, 1)
    except OSError:
        pass
    finally:
        lock_file.close()


def _run_with_lock(token: str, owner_id: int | None = None) -> None:
    resolved_owner = int(owner_id) if owner_id is not None else load_owner_id()
    os.makedirs(DEST, exist_ok=True)
    os.makedirs(TRAP_DIR, exist_ok=True)
    if os.path.exists(STATE_PATH):
        with open(STATE_PATH, "r", encoding="utf-8") as f:
            state = json.load(f)
        seen = parse_seen(state.get("seen", []), resolved_owner)
        first_run = False
    else:
        seen = set()
        first_run = True
    res = api(token, "getUpdates", timeout=5)
    if not res.get("ok"):
        print("getUpdates FAILED")
        return
    todo = filter_owner_candidates(
        res.get("result", []),
        resolved_owner,
        seen,
        first_run=first_run,
        limit_first_run=LIMIT_FIRST_RUN,
    )
    done = []
    failed = []
    for chat_id, mid, kind, file_id, fsize in todo:
        key = (chat_id, mid)
        try:
            if fsize and fsize > MAX_DL:
                failed.append((mid, kind, "too big for Bot API download"))
                seen.add(key)
                continue
            g = api(token, "getFile", file_id=file_id)
            if not g.get("ok"):
                failed.append((mid, kind, "getFile failed"))
                seen.add(key)
                continue
            fpath = g["result"]["file_path"]
            tmp = os.path.join(TRAP_DIR, "dl-" + uuid.uuid4().hex + ".bin")
            download(token, fpath, tmp)
            with open(tmp, "rb") as f:
                raw = f.read()
            os.remove(tmp)
            enc = dpapi_encrypt(raw)
            ts = time.strftime("%Y%m%d-%H%M%S", time.localtime())
            rand = "".join(random.choice(string.hexdigits.lower()) for _ in range(8))
            name = "oldy-%s-%d-%s.enc" % (ts, mid, rand)
            with open(os.path.join(DEST, name), "wb") as f:
                f.write(enc)
            done.append((mid, kind, name, len(raw), len(enc)))
            seen.add(key)
        except Exception as e:  # noqa: BLE001
            why = str(e).replace(token, "[TOKEN]")
            failed.append((mid, kind, "ERR " + why))
            seen.add(key)
    serialized_seen = [dedup_key(c, m) for c, m in sorted(seen)]
    with open(STATE_PATH + ".tmp", "w", encoding="utf-8") as f:
        json.dump({"seen": serialized_seen}, f)
    os.replace(STATE_PATH + ".tmp", STATE_PATH)
    print("PROCESSED:", len(todo))
    print("SAVED:", len(done))
    for mid, kind, name, raw_len, enc_len in done:
        print("OK", mid, kind, name, raw_len, enc_len)
    print("FAILED:", len(failed))
    for mid, kind, why in failed:
        print("FAIL", mid, kind, why)


def main() -> None:
    token = load_token()
    owner_id = load_owner_id()
    lock_file = acquire_token_poller_lock(token)
    if lock_file is None:
        print("SKIPPED: ANOTHER_POLLER_FOR_THIS_BOT_TOKEN (token_poller_lock held by active companion)")
        return
    try:
        _run_with_lock(token, owner_id=owner_id)
    finally:
        release_token_poller_lock(lock_file)


if __name__ == "__main__":
    main()
