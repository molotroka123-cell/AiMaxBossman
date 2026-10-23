"""Word-by-word analysis of K1m6a videos, two at a time, only while Bossman is idle (owner 23.10).

    python tools/k1m6a_word_analysis.py --manifest <archive>/queue_next_batch_20261023.json [--parallel 2] [--limit N]
                                        [--chain 25,3,44] [--window-words 150] [--root <archive>]

Per video: public auto-captions (yt-dlp, json3 with word timings, saved UNCHANGED) -> windows of ~150 words cut at
sentence ends -> ONE cheap model per window through the one Bossman task path (agent chain: Nemotron Ultra NIM = 25 and
Nemotron free = 3 first, paid GLM 5.3 Flash = 44 only as the last fallback) -> a JSON array per sentence (type,
entities, ASR-suspect words). Word coverage is MEASURED: the sentences must reproduce the window's words, otherwise the
window is recorded as partial. Output stays UNVERIFIED (claims are not trading rules until independently checked).

Idle gate: nothing is sent while the owner's marker (bcc.owner_activity) is live or Direct Gen has running/waiting jobs.
Resume: finished windows are cached (`word_analysis/window-NN.json`); re-running continues where it stopped and
retries windows that came back empty.
"""
from __future__ import annotations

import argparse
import json
import os
import re
import subprocess
import sys
import time
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path

DEFAULT_CHAIN = (25, 3, 44)
DEFAULT_WINDOW = 150
FREE_AGENTS = frozenset({25, 3})
INSTR = ("Ты разбираешь расшифровку трейдингового видео автора K1m6a (BTC, ордерфлоу). Расшифровка автоматическая, в ней "
         "бывают ошибки распознавания (SFP, CVD, OI, value area, single prints, названия монет). Разбери ВСЕ слова окна, "
         "ничего не пропускай и не пересказывай. Верни ТОЛЬКО JSON-массив по предложениям окна, по порядку, без пропусков: "
         '[{"i":номер,"start_s":число,"text":"предложение дословно как в окне","type":"observation|conditional_rule|'
         'dated_forecast|opinion|smalltalk|ad","entities":[{"text":"...","kind":"ticker|price_level|indicator|timeframe|'
         'event|person|number"}],"asr_suspect":["слово, которое похоже на ошибку распознавания"],"note":"короткое '
         'пояснение или пусто"}]. Числа и уровни цен переписывай как сказано; если неясно — kind number и в note UNKNOWN. '
         "Не выдумывай того, чего нет в тексте. Прогнозы и мнения автора не превращай в универсальные правила.")
BUSY_STATUSES = ("queued", "running", "pending", "waiting_approval")


def archive_root() -> Path:
    base = os.environ.get("BCC_DATA_DIR") or str(Path(os.environ.get("LOCALAPPDATA", ".")) / "Bossman" / "CommandCenter")
    return Path(base) / "learning" / "k1m6a-youtube"


# ----------------------------------------------------------------------------- pure helpers (unit-tested)

def load_words(json3_path: Path) -> list[tuple[float, str]]:
    """(start_seconds, word) for every caption segment, in order; the file itself is never modified."""
    doc = json.loads(Path(json3_path).read_text(encoding="utf-8"))
    out: list[tuple[float, str]] = []
    for event in doc.get("events", []):
        for seg in event.get("segs") or []:
            word = (seg.get("utf8") or "").replace("\n", " ").strip()
            if word:
                out.append((round((event.get("tStartMs", 0) + seg.get("tOffsetMs", 0)) / 1000, 2), word))
    return out


def make_windows(words: list[tuple[float, str]], size: int = DEFAULT_WINDOW) -> list[list[tuple[float, str]]]:
    """Windows of at least ``size`` words, cut at the next sentence end; the tail is kept as the last window."""
    windows, cur = [], []
    for item in words:
        cur.append(item)
        if len(cur) >= size and re.search(r"[.?!]$", item[1]):
            windows.append(cur)
            cur = []
    if cur:
        windows.append(cur)
    return windows


def count_words(text: str) -> int:
    return len(re.findall(r"\w+", text, re.U))


def coverage(items: list[dict], window: list[tuple[float, str]]) -> dict:
    total = count_words(" ".join(w for _, w in window))
    covered = count_words(" ".join(str(i.get("text", "")) for i in items))
    return {"words_in_window": total, "words_covered": covered, "coverage": round(min(covered, total) / max(total, 1), 3)}


def extract_items(answer: str) -> list[dict]:
    """The JSON array out of a model answer (code fences / prose around it are tolerated). Unparseable -> []."""
    match = re.search(r"\[.*\]", answer or "", re.S)
    if not match:
        return []
    try:
        data = json.loads(match.group(0))
    except ValueError:
        return []
    return [d for d in data if isinstance(d, dict)] if isinstance(data, list) else []


def build_prompt(header: str, marker: str, n: int, total: int, window: list[tuple[float, str]]) -> str:
    text = " ".join(f"[{t:.0f}s] {w}" if k % 25 == 0 else w for k, (t, w) in enumerate(window))
    return header + f"Окно {n}/{total} расшифровки:\n```\n{text}\n```" + marker + INSTR


def pending_videos(manifest: dict, root: Path, limit: int | None = None) -> list[dict]:
    """Manifest order, minus videos whose summary already exists."""
    todo = [v for v in manifest["videos"] if not (Path(root) / v["video_id"] / "word_analysis" / "summary.json").exists()]
    return todo[:limit] if limit else todo


# ----------------------------------------------------------------------------- idle gate

def bossman_is_idle(api, data_dir: Path | None = None) -> tuple[bool, str]:
    """(idle, why not). The owner's live request and any generation job beat background analysis."""
    try:
        from bcc import owner_activity
        marker = owner_activity.active(data_dir=data_dir)
        if marker:
            return False, owner_activity.describe(marker)
    except ImportError:
        pass
    try:
        queue = api("GET", "/api/direct-gen/status", timeout=60)["queue"]
    except Exception:  # noqa: BLE001 - a backend that does not answer is not "idle"
        return False, "backend does not answer"
    if queue.get("running") or queue.get("waiting"):
        return False, f"generation queue busy ({1 if queue.get('running') else 0} running, {queue.get('waiting')} waiting)"
    return True, ""


def wait_idle(api, *, poll_s: float = 20.0, sleep=time.sleep, data_dir: Path | None = None, max_wait_s: float = 0.0) -> bool:
    waited = 0.0
    while True:
        idle, _ = bossman_is_idle(api, data_dir)
        if idle:
            return True
        if max_wait_s and waited >= max_wait_s:
            return False
        sleep(poll_s)
        waited += poll_s


# ----------------------------------------------------------------------------- one window / one video

def ask_chain(api, prompt: str, title: str, chain: tuple[int, ...], *, sleep=time.sleep, polls: int = 240) -> dict:
    """Run the prompt through the agents in order until one returns a parseable array. Returns the last attempt."""
    attempt = {"agent": None, "task_id": None, "status": "not_run", "answer": "", "items": [], "cost_usd": 0.0}
    cost = 0.0
    for agent in chain:
        created = api("POST", "/api/tasks", {"title": title, "prompt": prompt, "agent_id": agent, "run_now": True})
        task_id = (created.get("task") or created)["id"]
        record: dict = {}
        for _ in range(polls):
            record = api("GET", f"/api/tasks/{task_id}", timeout=60)
            if record["task"]["status"] not in BUSY_STATUSES:
                break
            sleep(5)
        cost += sum(float(r.get("cost_usd") or 0) for r in record.get("runs", []))
        answer = str(record.get("result") or "")
        attempt = {"agent": agent, "task_id": task_id, "status": record["task"]["status"], "answer": answer,
                   "items": extract_items(answer), "cost_usd": round(cost, 5)}
        if attempt["status"] == "completed" and attempt["items"]:
            break
    return attempt


def analyze_video(video_id: str, root: Path, api, *, chain=DEFAULT_CHAIN, window_words=DEFAULT_WINDOW, header="", marker="",
                  sleep=time.sleep, gate=None) -> dict:
    vdir = Path(root) / video_id
    out = vdir / "word_analysis"
    out.mkdir(parents=True, exist_ok=True)
    words = load_words(vdir / "video.en.json3")
    (out / "words.json").write_text(json.dumps([{"t": t, "w": w} for t, w in words], ensure_ascii=False),
                                    encoding="utf-8", newline="\n")
    windows = make_windows(words, window_words)
    results = []
    for n, window in enumerate(windows, 1):
        path = out / f"window-{n:02d}.json"
        if path.exists():
            cached = json.loads(path.read_text(encoding="utf-8"))
            if cached.get("sentences"):                       # an EMPTY window is a failure, not progress: ask again
                results.append(cached)
                continue
        if gate is not None:
            gate()
        attempt = ask_chain(api, build_prompt(header, marker, n, len(windows), window),
                            f"K1m6a {video_id} words {n}/{len(windows)}", tuple(chain), sleep=sleep)
        cov = coverage(attempt["items"], window)
        rec = {"window": n, "agent_used": attempt["agent"], "task_id": attempt["task_id"], "status": attempt["status"], **cov,
               "start_s": window[0][0], "end_s": window[-1][0], "cost_usd": attempt["cost_usd"], "sentences": attempt["items"],
               "raw_answer_if_unparsed": "" if attempt["items"] else attempt["answer"][:400]}
        path.write_text(json.dumps(rec, ensure_ascii=False, indent=1), encoding="utf-8", newline="\n")
        results.append(rec)
    total = sum(r["words_in_window"] for r in results)
    covered = sum(min(r["words_covered"], r["words_in_window"]) for r in results)
    summary = {"video_id": video_id, "words": len(words), "windows": len(windows),
               "windows_ok": sum(1 for r in results if r["status"] == "completed" and r["sentences"]),
               "coverage": round(covered / max(total, 1), 3), "cost_usd": round(sum(r["cost_usd"] for r in results), 4),
               "paid_windows": sum(1 for r in results if r.get("agent_used") not in FREE_AGENTS), "status": "UNVERIFIED",
               "agents": sorted({r.get("agent_used") for r in results if r.get("agent_used")}), "types": {}, "asr_suspect": []}
    for r in results:
        for s in r["sentences"]:
            summary["types"][s.get("type", "?")] = summary["types"].get(s.get("type", "?"), 0) + 1
            summary["asr_suspect"] += [str(x) for x in (s.get("asr_suspect") or [])]
    summary["asr_suspect"] = sorted(set(summary["asr_suspect"]))[:80]
    (out / "summary.json").write_text(json.dumps(summary, ensure_ascii=False, indent=1), encoding="utf-8", newline="\n")
    return summary


def fetch_subs(video_id: str, root: Path) -> Path:
    """Public auto-captions with word timings, saved as yt-dlp wrote them (immutable input)."""
    vdir = Path(root) / video_id
    vdir.mkdir(parents=True, exist_ok=True)
    target = vdir / "video.en.json3"
    if not target.exists():
        subprocess.run(["yt-dlp", "--skip-download", "--no-warnings", "--write-auto-subs", "--sub-langs", "en",
                        "--sub-format", "json3", "-o", str(vdir / "video.%(ext)s"), f"https://youtu.be/{video_id}"],
                       check=True, capture_output=True, timeout=300)
    if not target.exists():
        raise FileNotFoundError(f"no English auto-captions for {video_id}")
    return target


# ----------------------------------------------------------------------------- batch

def save_state(root: Path, state: dict) -> None:
    (Path(root) / "batch_state.json").write_text(json.dumps(state, ensure_ascii=False, indent=1), encoding="utf-8", newline="\n")


def run_batch(manifest: dict, root: Path, api, *, parallel=2, limit=None, chain=DEFAULT_CHAIN, window_words=DEFAULT_WINDOW,
              header="", marker="", sleep=time.sleep, fetch=fetch_subs, gate=None) -> dict:
    todo = pending_videos(manifest, root, limit)
    state = {"started": time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime()), "parallel": parallel,
             "total_pending": len(todo), "done": [], "failed": []}

    def one(video):
        vid = video["video_id"]
        try:
            fetch(vid, root)
            summ = analyze_video(vid, root, api, chain=chain, window_words=window_words, header=header, marker=marker,
                                 sleep=sleep, gate=gate)
            state["done"].append({"video_id": vid, "coverage": summ["coverage"],
                                  "windows_ok": f"{summ['windows_ok']}/{summ['windows']}", "cost_usd": summ["cost_usd"]})
        except Exception as exc:  # noqa: BLE001 - one video must not stop the batch; the reason is recorded
            state["failed"].append({"video_id": vid, "error": f"{type(exc).__name__}: {str(exc)[:200]}"})
        save_state(root, state)
    with ThreadPoolExecutor(max_workers=max(1, parallel)) as pool:
        list(pool.map(one, todo))
    return state


def main(argv=None) -> int:
    ap = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    ap.add_argument("--manifest", required=True)
    ap.add_argument("--root", default=str(archive_root()))
    ap.add_argument("--parallel", type=int, default=2)
    ap.add_argument("--limit", type=int, default=None)
    ap.add_argument("--chain", default=",".join(str(x) for x in DEFAULT_CHAIN))
    ap.add_argument("--window-words", type=int, default=DEFAULT_WINDOW)
    a = ap.parse_args(argv)
    here = Path(__file__).resolve().parent
    sys.path.insert(0, str(here))
    sys.path.insert(0, str(here.parent / "command-center"))
    from prove_chat_attachments import api
    from bcc import conversation_context as cc
    manifest = json.loads(Path(a.manifest).read_text(encoding="utf-8"))
    state = run_batch(manifest, Path(a.root), api, parallel=a.parallel, limit=a.limit,
                      chain=tuple(int(x) for x in a.chain.split(",")), window_words=a.window_words,
                      header=cc.HEADER, marker=cc.MARKER, gate=lambda: wait_idle(api))
    print(json.dumps({"done": len(state["done"]), "failed": len(state["failed"]),
                      "state": str(Path(a.root) / "batch_state.json")}))
    return 0 if not state["failed"] else 1


if __name__ == "__main__":
    raise SystemExit(main())
