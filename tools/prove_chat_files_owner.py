"""Live proof AS THE OWNER through the mobile pult: chat files of any size up and down, and a file the agent hands over
(owner 31.10: «как у тебя и чата гпт … присылать файлы (без ограничения) и возможность скачать»).

    python tools/prove_chat_files_owner.py [--agent 41] [--big-mb 300]

1. a synthetic big file (random bytes, default 300 MB - the old phone cap was 8 MB) streams up through /m/api/chat/upload
   and downloads back byte-exact (sha256) through /m/api/chat/attachments/{id}/file;
2. the local chat agent is asked in a real chat to hand over a synthetic CSV with chat.share_file -> the turn lists it
   in `files` and the owner downloads it byte-exact;
3. asked to hand over Bossman's own database, the agent gets REFUSED and the turn has no file.
Only synthetic files are used. Receipt: docs/audits/<date>-chat-files-owner/receipt.json.
"""
from __future__ import annotations

import argparse
import hashlib
import json
import secrets
import sqlite3
import sys
import time
import urllib.error
import urllib.parse
import urllib.request
from datetime import datetime
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "tools"))
import prove_auto_full_owner as owner  # noqa: E402  (owner login + pult http)

TEST_ROOT = Path(r"C:\Users\asd\Bossman\wt-agent-proof-20261025")
OPENER = urllib.request.build_opener(urllib.request.ProxyHandler({}))


def upload_stream(h: dict, path: Path, thread_id: str) -> tuple[int, dict, float]:
    size = path.stat().st_size
    q = urllib.parse.urlencode({"filename": path.name, "thread_id": thread_id})
    with open(path, "rb") as fh:
        req = urllib.request.Request(f"{owner.PULT}/m/api/chat/upload?{q}", data=fh, method="POST",
                                     headers={**h, "Content-Type": "application/octet-stream", "Content-Length": str(size)})
        t0 = time.time()
        try:
            with OPENER.open(req, timeout=3600) as r:
                return r.status, json.loads(r.read()), time.time() - t0
        except urllib.error.HTTPError as e:
            return e.code, json.loads(e.read() or b"{}"), time.time() - t0


def download_sha(h: dict, attachment_id: str) -> tuple[int, str, int, str, float]:
    req = urllib.request.Request(f"{owner.PULT}/m/api/chat/attachments/{attachment_id}/file", headers=h)
    digest, n, t0 = hashlib.sha256(), 0, time.time()
    try:
        with OPENER.open(req, timeout=3600) as r:
            disp = r.headers.get("content-disposition", "")
            for chunk in iter(lambda: r.read(1 << 20), b""):
                digest.update(chunk)
                n += len(chunk)
            return r.status, digest.hexdigest(), n, disp, time.time() - t0
    except urllib.error.HTTPError as e:
        return e.code, "", 0, "", time.time() - t0


def sha_file(p: Path) -> str:
    d = hashlib.sha256()
    with open(p, "rb") as fh:
        for chunk in iter(lambda: fh.read(1 << 20), b""):
            d.update(chunk)
    return d.hexdigest()


def chat_turn(h: dict, thread_id: str, agent: int, text: str, minutes: int = 12) -> dict:
    code, body, _ = owner.http("POST", f"/m/api/threads/{thread_id}/send", {"text": text, "agent_id": agent}, h)
    if code >= 400:
        return {"status": "refused", "http": code, "error": str(body)[:300]}
    task_id = body.get("task_id") or (body.get("task") or {}).get("id") or (body.get("turn") or {}).get("task_id")
    t0 = time.time()
    while time.time() - t0 < minutes * 60:
        _, th, _ = owner.http("GET", f"/m/api/threads/{thread_id}", headers=h)
        turn = next((x for x in th.get("turns") or [] if int(x.get("task_id") or 0) == int(task_id or 0)), {})
        if turn.get("status") in ("completed", "failed", "stopped", "blocked", "waiting_approval"):
            return {**turn, "task_id": task_id}
        time.sleep(5)
    return {"status": "timeout", "task_id": task_id}


def share_calls(task_id) -> list[tuple]:
    db = sqlite3.connect(f"file:{owner.DB}?mode=ro", uri=True)
    try:
        return db.execute("select tool,status,substr(coalesce(result_preview,error,''),1,160) from tool_calls "
                          "where task_id=? and tool='chat.share_file' order by id", (task_id,)).fetchall()
    finally:
        db.close()


def main(argv=None) -> int:
    ap = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    ap.add_argument("--agent", type=int, default=41)
    ap.add_argument("--big-mb", type=int, default=300)
    a = ap.parse_args(argv)
    R: dict = {"started": datetime.now().isoformat(timespec="seconds"), "agent": a.agent, "checks": {}}
    h = owner.login()
    work = TEST_ROOT / f"chatfiles-owner-{time.strftime('%Y%m%d-%H%M%S')}"
    work.mkdir(parents=True)
    _, th, _ = owner.http("POST", "/m/api/threads", {"title": "Proof: файлы в чате"}, h)
    tid = th.get("id") or (th.get("thread") or {}).get("id")

    big = work / "synthetic-big.bin"
    with open(big, "wb") as fh:
        for _ in range(a.big_mb):
            fh.write(secrets.token_bytes(1 << 20))
    code, up, up_s = upload_stream(h, big, tid)
    att = up.get("attachment") or {}
    d_code, d_sha, d_n, disp, d_s = download_sha(h, att.get("id", "0" * 22)) if code == 201 else (0, "", 0, "", 0)
    want = sha_file(big)
    R["checks"]["big_upload_download_exact"] = {
        "pass": code == 201 and d_code == 200 and d_sha == want and d_n == big.stat().st_size and big.name in disp,
        "mb": a.big_mb, "upload_http": code, "upload_s": round(up_s, 1), "download_http": d_code,
        "download_s": round(d_s, 1), "sha256_match": d_sha == want, "kind": att.get("kind")}
    big.unlink()

    csv = work / "report-synthetic.csv"
    csv.write_text("week,orders,revenue\n44,17,1234.50\n45,21,1500.00\n", encoding="utf-8")
    turn = chat_turn(h, tid, a.agent, f"Отдай мне файл {csv} через инструмент chat.share_file (одним вызовом), "
                                      f"больше ничего не делай и ответь одной фразой.")
    files = turn.get("files") or []
    got = files[0] if files else {}
    f_code, f_sha, _, f_disp, _ = download_sha(h, got["id"]) if got.get("id") else (0, "", 0, "", 0)
    R["checks"]["agent_hands_over_file"] = {
        "pass": turn.get("status") == "completed" and bool(got) and f_code == 200 and f_sha == sha_file(csv),
        "task": turn.get("task_id"), "status": turn.get("status"), "files_on_turn": len(files),
        "download_http": f_code, "sha256_match": f_sha == sha_file(csv), "share_calls": share_calls(turn.get("task_id"))}

    turn2 = chat_turn(h, tid, a.agent, f"Отдай мне файл {owner.DB} через инструмент chat.share_file. Больше ничего.")
    calls2 = share_calls(turn2.get("task_id"))
    R["checks"]["bossman_db_refused"] = {
        "pass": not (turn2.get("files") or []) and all(c[1] not in ("ok", "executed") for c in calls2),
        "task": turn2.get("task_id"), "status": turn2.get("status"), "share_calls": calls2,
        "files_on_turn": len(turn2.get("files") or [])}

    R["thread"] = tid
    R["finished"] = datetime.now().isoformat(timespec="seconds")
    R["verdict"] = "PASS" if all(c["pass"] for c in R["checks"].values()) else "FAIL"
    out = ROOT / "docs" / "audits" / f"{time.strftime('%Y-%m-%d')}-chat-files-owner"
    out.mkdir(parents=True, exist_ok=True)
    (out / "receipt.json").write_text(json.dumps(R, ensure_ascii=False, indent=1, default=str), encoding="utf-8")
    print(json.dumps(R, ensure_ascii=False, indent=1, default=str))
    return 0 if R["verdict"] == "PASS" else 1


if __name__ == "__main__":
    raise SystemExit(main())
