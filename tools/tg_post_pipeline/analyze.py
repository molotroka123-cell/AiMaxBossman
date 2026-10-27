"""Blind hand-off: frames of the PC-agent screen recording -> owner's Gemma (agent 41) in a chat thread -> pipeline -> pult.

Claude never opens the frames or the answer. Printed: counts, statuses and ONLY the first service line of the answer
(«ПОСТ_ОТКРЫТ: да|нет; ВИДЕО: N»), which says whether the recording shows the post at all. The full answer goes to the
owner's pult via pult_note --file, only when the service line says the post was open.

    python analyze.py
"""
from __future__ import annotations

import json
import re
import subprocess
import sys
import time
from pathlib import Path

REPO = Path(r"C:\Users\asd\Bossman\wt-trio-claude")
sys.path.insert(0, str(REPO / "tools"))
from prove_chat_attachments import api  # noqa: E402

HERE = Path(__file__).resolve().parent
AGENT_ID = 41
FRAMES = HERE / "frames"
PROMPT = (
    "Это кадры записи экрана: в Telegram Desktop открыт пост канала и проигрываются его видео (кадры по порядку, раз в 8 секунд), "
    "последний файл — снимок экрана. ПЕРВОЙ строкой ответа напиши строго в формате: «ПОСТ_ОТКРЫТ: да; ВИДЕО: N» или "
    "«ПОСТ_ОТКРЫТ: нет; ВИДЕО: 0; ЭКРАН: <одно из: рабочий_стол | telegram_список_чатов | telegram_другой_чат | telegram_нет_доступа | telegram_загрузка | другое>», если на кадрах нет открытого поста. Дальше, если пост открыт: "
    "1) что в посте — текст, оформление, хештеги, кнопки; 2) видео — формат кадра, длительность, монтаж и темп, "
    "текст на экране, стиль, эффекты и переходы, что происходит в кадре; 3) что видно о канале — тематика и формат. "
    "Затем пайплайн «как делать то же самое»: шаги от идеи до публикации, инструменты (сначала локальные в Боссмане: "
    "Motion Studio / Wan Animate для видео, ACE-Step для музыки, ffmpeg для монтажа), шаблоны промптов, тайминги, "
    "чек-лист качества и что нужно от владельца. Только описание и план — ничего не выполняй. По-русски, с заголовками."
)


def frames(video: str, shots: list[str]) -> list[Path]:
    FRAMES.mkdir(exist_ok=True)
    for p in FRAMES.glob("*"):
        p.unlink()
    subprocess.run(["ffmpeg", "-v", "error", "-y", "-i", video, "-vf", "fps=1/8.5,scale=1280:-2", "-frames:v", "7",
                    str(FRAMES / "f%02d.jpg")], check=True)
    out = sorted(FRAMES.glob("f*.jpg"))
    for i, s in enumerate(shots[:1]):
        dst = FRAMES / f"s{i}.png"
        dst.write_bytes(Path(s).read_bytes())
        out.append(dst)
    return out


def main() -> int:
    st = json.loads((HERE / "pc_task.json").read_text(encoding="utf-8"))
    media = [m for m in st["media"] if m]
    video = next(m for m in media if m.endswith(".mp4"))
    shots = [m for m in media if m.endswith(".png")][1:] or [m for m in media if m.endswith(".png")]
    files = frames(video, shots)
    print(f"frames={len(files)}")
    thread = api("POST", "/api/chat/threads", {"title": "Разбор поста Telegram → пайплайн"})
    tid = thread.get("id") or (thread.get("thread") or {}).get("id")
    ids = []
    for f in files:
        up = api("POST", f"/api/chat/attachments?filename={f.name}&thread_id={tid}", raw=f.read_bytes(),
                 ctype="application/octet-stream")
        ids.append(up["id"])
    sent = api("POST", f"/api/chat/threads/{tid}/send", {"text": PROMPT, "agent_id": AGENT_ID, "attachments": ids}, timeout=900)
    task_id = sent.get("task_id") or (sent.get("task") or {}).get("id")
    print(f"thread={tid} task={task_id} attachments={len(ids)}")
    t0, status, answer = time.time(), None, ""
    while time.time() - t0 < 540:
        view = api("GET", f"/api/tasks/{task_id}")
        status = (view.get("task") or {}).get("status")
        if status in ("completed", "failed", "stopped", "cancelled"):
            answer = str(view.get("result") or "").strip()
            break
        time.sleep(8)
    first = answer.splitlines()[0][:140] if answer else ""
    m = re.search(r"ПОСТ_ОТКРЫТ:\s*(да|нет)\s*;\s*ВИДЕО:\s*(\d+)", first, re.I)
    print(f"status={status} answer_chars={len(answer)} service_line_ok={bool(m)} "
          f"post_open={m.group(1) if m else '?'} videos={m.group(2) if m else "?"} screen={(re.search(r"ЭКРАН:s*([a-zа-я_]+)", first) or [None, "?"])[1]} seconds={round(time.time() - t0)}")
    if status == "completed" and m and m.group(1).lower() == "да":
        out = HERE / "pipeline_answer.txt"          # not printed, not read by Claude
        out.write_text(f"Пайплайн по посту Telegram — Gemma 4 локально (ПК-агент записал экран, задача {task_id})\n\n{answer}",
                       encoding="utf-8")
        r = subprocess.run([sys.executable, "-X", "utf8", str(REPO / "tools" / "pult_note.py"), "--file", str(out)],
                           capture_output=True, text=True, encoding="utf-8", errors="replace")
        print("pult:", "sent" if r.returncode == 0 else f"error rc={r.returncode}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
