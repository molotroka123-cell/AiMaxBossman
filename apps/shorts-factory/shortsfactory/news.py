"""News / explainer pipeline that makes ORIGINAL shorts: no third-party footage is ever re-used.

  GDELT DOC API (open, keyless) -> fresh article list (titles, outlet domain, URL kept)
  -> local Qwen writes a 30-50 s neutral Russian script from those titles ONLY (+ executable fact checks)
  -> local Russian TTS (Piper; Kokoro v1.0 has no Russian voice) -> 1-2 generated illustrations via the Bossman
  direct-gen API (or kinetic text cards when it is unavailable) -> 1080x1920 render with burned captions,
  the outlet name always on screen -> queue as ready. Never published here.
"""
from __future__ import annotations

import json
import os
import re
import subprocess
import time
import uuid
from collections import Counter
from pathlib import Path
from typing import Callable
from urllib.parse import urlparse

import httpx

from . import media, metadata
from .llm import parse_json

GDELT_URL = "https://api.gdeltproject.org/api/v2/doc/doc"
GDELT_TERMS = "https://www.gdeltproject.org/about.html#termsofuse"
MIN_INTERVAL_S = 5.5          # GDELT: "Please limit requests to one every 5 seconds"
CACHE_TTL_S = 30 * 60
_last_call = {"t": 0.0}

PIPER_PY = Path(os.environ.get("SHORTS_PIPER_PYTHON", r"C:\Users\asd\Bossman\media-runtime\voice-venv\Scripts\python.exe"))
PIPER_VOICE = Path(os.environ.get("SHORTS_PIPER_VOICE", r"C:\Users\asd\Bossman\models\voice\piper\ru\ru_RU\denis\medium\ru_RU-denis-medium.onnx"))
BCC_URL = os.environ.get("SHORTS_BCC_URL", "http://127.0.0.1:8801")
BCC_TOKEN_FILE = Path(os.environ.get("LOCALAPPDATA", "")) / "Bossman" / "CommandCenter" / "token"
MAX_IMAGES = 2


class RateLimited(RuntimeError):
    pass


class FactCheckError(ValueError):
    def __init__(self, problems: list[str]):
        super().__init__("; ".join(problems))
        self.problems = problems


# ------------------------------------------------------------------ GDELT
def http_get_text(url: str, params: dict) -> tuple[int, str]:
    r = httpx.get(url, params=params, timeout=40, headers={"User-Agent": "BossmanShortsFactory/0.1 (owner PC, low volume)"})
    return r.status_code, r.text


def gdelt_fetch(query: str, *, get_text: Callable = http_get_text, store=None, timespan: str = "24h", maxrecords: int = 40,
                sleep: Callable[[float], None] = time.sleep, clock: Callable[[], float] = time.time, retries: int = 2) -> dict:
    """Rate-limited (>=5.5 s between calls), cached 30 min, 429/'Please limit' -> backoff and retry."""
    params = {"query": query, "mode": "artlist", "format": "json", "maxrecords": str(maxrecords), "timespan": timespan, "sort": "datedesc"}
    key = "gdelt:" + json.dumps(params, sort_keys=True, ensure_ascii=False)
    if store is not None:
        hit = store.kv_get(key)
        if hit and clock() - hit.get("_at", 0) < CACHE_TTL_S:
            return dict(hit, cached=True)
    for attempt in range(retries + 1):
        wait = MIN_INTERVAL_S - (clock() - _last_call["t"])
        if wait > 0:
            sleep(wait)
        _last_call["t"] = clock()
        code, text = get_text(GDELT_URL, params)
        limited = code == 429 or text.lstrip().startswith("Please limit requests")
        if not limited:
            break
        if attempt == retries:
            raise RateLimited("GDELT: лимит запросов (429), попробуйте позже")
        sleep(MIN_INTERVAL_S * (attempt + 2))
    if code != 200:
        raise RuntimeError(f"GDELT HTTP {code}: {text[:200]}")
    try:
        data = json.loads(text) if text.strip() else {}
    except json.JSONDecodeError as exc:
        raise RuntimeError(f"GDELT вернул не JSON: {text[:160]}") from exc
    arts = []
    for a in data.get("articles") or []:
        if not a.get("title") or not a.get("url"):
            continue
        arts.append({"title": re.sub(r"\s+", " ", a["title"]).strip(), "url": a["url"], "domain": a.get("domain") or urlparse(a["url"]).netloc,
                     "seendate": a.get("seendate"), "language": a.get("language"), "country": a.get("sourcecountry")})
    res = {"status": "OK", "query": query, "params": params, "articles": arts, "fetched_at": time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime()),
           "source": GDELT_URL, "terms": GDELT_TERMS, "_at": clock()}
    if store is not None and arts:            # never cache an empty answer
        store.kv_set(key, res)
    return res


# ------------------------------------------------------------------ RSS fallback (open publisher feeds) when GDELT throttles
RSS_FEEDS = [u.strip() for u in os.environ.get("SHORTS_NEWS_RSS", "https://lenta.ru/rss/news,https://meduza.io/rss/all,"
             "https://ria.ru/export/rss2/archive/index.xml").split(",") if u.strip()]
_TAG = re.compile(r"<[^>]+>")


def _strip(text: str) -> str:
    import html
    return re.sub(r"\s+", " ", html.unescape(_TAG.sub(" ", text or ""))).strip()


def rss_fetch(keywords: str, *, get_text: Callable = http_get_text, feeds: list[str] | None = None, store=None,
              clock: Callable[[], float] = time.time) -> dict:
    """Headlines + short snippets from public RSS feeds filtered by a keyword regex ('ИИ|нейросет'); URLs kept."""
    import xml.etree.ElementTree as ET
    feeds = feeds or RSS_FEEDS
    key = "rss:" + keywords + "|" + ",".join(feeds)
    if store is not None:
        hit = store.kv_get(key)
        if hit and clock() - hit.get("_at", 0) < CACHE_TTL_S:
            return dict(hit, cached=True)
    pat = re.compile(keywords, re.I | re.U)
    arts, errors = [], {}
    for url in feeds:
        try:
            code, text = get_text(url, {})
            if code != 200:
                errors[url] = f"HTTP {code}"
                continue
            root = ET.fromstring(text.encode("utf-8") if isinstance(text, str) else text)
        except Exception as exc:  # noqa: BLE001 - one bad feed must not hide the others; reported in errors
            errors[url] = f"{type(exc).__name__}: {str(exc)[:120]}"
            continue
        for it in root.iter("item"):
            title, link = _strip(it.findtext("title") or ""), (it.findtext("link") or "").strip()
            snippet = _strip(it.findtext("description") or "")[:400]
            if title and link and pat.search(title + " " + snippet):
                arts.append({"title": title, "url": link, "domain": urlparse(link).netloc.removeprefix("www."), "snippet": snippet,
                             "seendate": it.findtext("pubDate"), "language": "ru", "country": None})
    res = {"status": "OK" if arts else "EMPTY", "query": keywords, "articles": arts[:200], "errors": errors, "source": "rss:" + ",".join(feeds),
           "fetched_at": time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime()), "_at": clock()}
    if store is not None and arts:
        store.kv_set(key, res)
    return res


_WORD = re.compile(r"[\wё]{4,}", re.I | re.U)
STOP = set("этот этого этой которые который также после более будет может могут года году время сегодня заявил заявила "
           "стало стала стали своих свой свою россии россия".split())


def _tokens(title: str) -> set[str]:
    return {w.lower()[:6] for w in _WORD.findall(title) if w.lower() not in STOP}


def group_stories(articles: list[dict], min_overlap: float = 0.5, min_shared: int = 2) -> list[dict]:
    """Greedy clustering of headlines about the same event (overlap coefficient of 6-letter stems against the
    group's first headline, >= 2 shared stems); bigger clusters (more outlets) first."""
    groups: list[dict] = []
    for a in articles:
        tk = _tokens(a["title"])
        best = None
        for g in groups:
            shared = len(tk & g["tokens"])
            j = shared / max(1, min(len(tk), len(g["tokens"])))
            if shared >= min_shared and j >= min_overlap and (best is None or j > best[0]):
                best = (j, g)
        if best:
            best[1]["articles"].append(a)      # group tokens stay those of the lead headline: no drift into other events
        else:
            groups.append({"tokens": set(tk), "articles": [a]})
    out = []
    for g in groups:
        domains = sorted({a["domain"] for a in g["articles"]})
        out.append({"articles": g["articles"][:6], "domains": domains, "outlets": len(domains), "lead": g["articles"][0]["title"]})
    out.sort(key=lambda g: (-g["outlets"], -len(g["articles"])))
    return out


# ------------------------------------------------------------------ script + executable fact checks
SCRIPT_SYSTEM = (
    "Ты редактор новостных YouTube Shorts. Пиши по-русски, нейтрально, без оценок, без домыслов. "
    "Используй ТОЛЬКО факты, которые прямо есть в заголовках ниже: не добавляй числа, даты, имена, цитаты, причины "
    "или последствия, которых там нет. Если фактов мало, скажи это честно (\"подробности пока не сообщаются\"). "
    "Не обвиняй людей и организации в преступлениях, если это не сказано в заголовке как факт; используй "
    "\"по данным <издание>\". Объём: 70-110 слов (30-50 секунд). Ответ ТОЛЬКО JSON: "
    '{"headline":"<= 40 символов","lines":["фраза", "..."],"outlets":["домен издания", "..."],"image_prompt":"<english, abstract editorial illustration, no text, no real people>"}')


def build_script_messages(story: dict) -> list[dict]:
    items = "\n".join(f"- [{a['domain']}] {a['title']}" + (f" — {a['snippet']}" if a.get("snippet") else "") for a in story["articles"])
    return [{"role": "system", "content": SCRIPT_SYSTEM}, {"role": "user", "content": f"Заголовки (и подзаголовки):\n{items}"}]


_NUM = re.compile(r"\d+(?:[.,]\d+)?")


def fact_check(script: dict, story: dict) -> list[str]:
    """Executable checks (not trust in the model): numbers must come from the headlines, outlets must be among the
    fetched domains, length must fit 30-50 s, no direct quotes the headlines do not contain."""
    titles = " ".join(a["title"] + " " + (a.get("snippet") or "") for a in story["articles"])
    text =" ".join(script.get("lines") or [])
    problems = []
    nums_src = {n.replace(",", ".") for n in _NUM.findall(titles)}
    for n in _NUM.findall(text + " " + str(script.get("headline", ""))):
        if n.replace(",", ".") not in nums_src:
            problems.append(f"число {n} отсутствует в заголовках")
    doms = {d.lower() for d in story["domains"]}
    outlets = [str(o).lower().strip() for o in script.get("outlets") or []]
    if not outlets or any(o not in doms for o in outlets):
        problems.append("издания в сценарии должны быть из полученных источников")
    words = len(re.findall(r"[\wё-]+", text, re.U))
    if not 55 <= words <= 125:
        problems.append(f"объём {words} слов вне 55-125 (30-50 с)")
    for q in re.findall(r"[«\"]([^»\"]{8,})[»\"]", text):
        if q not in titles:
            problems.append("цитата, которой нет в заголовках")
    if not str(script.get("headline") or "").strip():
        problems.append("нет заголовка карточки")
    return problems


def write_script(story: dict, chat: Callable, attempts: int = 2) -> dict:
    last = []
    msgs = build_script_messages(story)
    for _ in range(attempts):
        sc = parse_json(chat(msgs))
        sc["lines"] = [re.sub(r"\s+", " ", str(x)).strip() for x in sc.get("lines") or [] if str(x).strip()]
        sc["headline"] = str(sc.get("headline") or "").strip()[:60]
        last = fact_check(sc, story)
        if not last:
            return sc
        msgs = msgs + [{"role": "assistant", "content": json.dumps(sc, ensure_ascii=False)},
                       {"role": "user", "content": "Исправь: " + "; ".join(last) + ". Верни тот же JSON."}]
    raise FactCheckError(last)


# ------------------------------------------------------------------ voice (Piper, local)
def tts_lines(lines: list[str], work: Path, *, python: Path = PIPER_PY, voice: Path = PIPER_VOICE) -> list[dict]:
    if not python.is_file() or not voice.is_file():
        raise media.RenderError("локальный Piper TTS не найден (SHORTS_PIPER_PYTHON / SHORTS_PIPER_VOICE)")
    env = dict(os.environ, PYTHONUTF8="1", PYTHONIOENCODING="utf-8")
    out = []
    for n, line in enumerate(lines):
        txt, wav = work / f"line{n}.txt", work / f"line{n}.wav"
        txt.write_text(line, encoding="utf-8")
        r = subprocess.run([str(python), "-m", "piper", "-m", str(voice), "-i", str(txt), "-f", str(wav), "--sentence-silence", "0.15"],
                           capture_output=True, env=env, timeout=300, **media._NOWIN)
        if r.returncode != 0 or not wav.is_file():
            raise media.RenderError("Piper: " + r.stderr.decode("utf-8", "replace")[-400:])
        out.append({"text": line, "wav": str(wav), "dur": media.probe(wav)["duration"]})
    return out


def caption_chunks_from_lines(voiced: list[dict], gap: float = 0.25, max_chars: int = 28) -> tuple[list[dict], float]:
    """Line-level timing is measured (wav duration); inside a line words are spread by character count."""
    t, chunks = 0.0, []
    for v in voiced:
        words = v["text"].split()
        total_chars = sum(len(w) + 1 for w in words) or 1
        acc, cur, cur_start = 0, [], t
        for wi, w in enumerate(words):
            cur.append(w)
            acc += len(w) + 1
            if len(" ".join(cur)) >= max_chars or w[-1:] in ".!?,;:" or wi == len(words) - 1:
                end = t + v["dur"] * acc / total_chars
                chunks.append({"start": round(cur_start, 3), "end": round(end, 3), "text": " ".join(cur)})
                cur, cur_start = [], end
        t += v["dur"] + gap
    return chunks, round(t, 3)


# ------------------------------------------------------------------ visuals (Bossman direct-gen) or text cards
def bcc_token() -> str | None:
    try:
        return BCC_TOKEN_FILE.read_text(encoding="utf-8").strip() or None
    except OSError:
        return None


def generate_image(prompt: str, out: Path, *, url: str = BCC_URL, token: str | None = None, model: str = "z-image-turbo",
                   resolution: str = "864x1536", timeout_s: float = 300, client: httpx.Client | None = None) -> dict:
    token = token or bcc_token()
    if not token:
        return {"status": "NOT_CONNECTED", "reason": "нет токена Bossman (LOCALAPPDATA\\Bossman\\CommandCenter\\token)"}
    c = client or httpx.Client(timeout=30)
    h = {"X-BCC-Token": token}
    t0 = time.time()
    try:
        r = c.post(f"{url}/api/direct-gen/jobs", headers=h, json={"model": model, "prompt": prompt, "resolution": resolution,
                   "negative": "text, letters, watermark, logo, real person, celebrity, gore"})
        if r.status_code >= 400:
            return {"status": "ERROR", "reason": f"HTTP {r.status_code}: {r.text[:200]}"}
        job = r.json()
        jid = job.get("id") or job.get("job_id") or (job.get("job") or {}).get("id")
        while time.time() - t0 < timeout_s:
            j = c.get(f"{url}/api/direct-gen/jobs/{jid}", headers=h).json()
            j = j.get("job", j)
            if j.get("status") == "completed":
                f = c.get(f"{url}/api/direct-gen/jobs/{jid}/file", headers=h)
                out.write_bytes(f.content)
                return {"status": "OK", "job_id": jid, "file": str(out), "model": model, "resolution": resolution, "wall_s": round(time.time() - t0, 1)}
            if j.get("status") in ("failed", "cancelled"):
                return {"status": "ERROR", "job_id": jid, "reason": str(j.get("error") or j.get("stage"))[:200]}
            time.sleep(3)
        try:                                   # do not leave a stuck generation holding the GPU
            c.post(f"{url}/api/direct-gen/jobs/{jid}/cancel", headers=h)
        except httpx.HTTPError:
            pass
        return {"status": "ERROR", "job_id": jid, "reason": f"timeout after {timeout_s:.0f} s (job cancelled)"}
    except httpx.HTTPError as exc:
        return {"status": "NOT_CONNECTED", "reason": f"{type(exc).__name__}: {str(exc)[:160]}"}


def render_news(out: Path, *, images: list[Path], voice_wavs: list[str], chunks: list[dict], total: float, headline: str,
                footer: str, work: Path) -> dict:
    """Images are placed at native size (never enlarged) over a blurred fill; outlet credit stays on screen."""
    if total > media.MAX_S:
        raise media.RenderError(f"озвучка {total:.1f} с > {media.MAX_S:.0f} с")
    W, H = media.TARGET_W, media.TARGET_H
    media.write_ass(work / "subs.ass", chunks, W, H, header=headline, footer=footer, total=total, margin_v_frac=0.2)
    inputs, graph = [], []
    if images:
        seg = total / len(images)
        for n, img in enumerate(images):
            inputs += ["-loop", "1", "-t", f"{seg + 0.05:.3f}", "-i", str(Path(img).resolve())]
            graph.append(f"[{n}:v]split=2[b{n}][f{n}];"
                         f"[b{n}]scale={W}:{H}:force_original_aspect_ratio=increase,crop={W}:{H},gblur=sigma=30,eq=brightness=-0.25[bb{n}];"
                         f"[f{n}]scale='min(iw,{W})':'min(ih,{H})':force_original_aspect_ratio=decrease[ff{n}];"
                         f"[bb{n}][ff{n}]overlay=(W-w)/2:(H-h)/2,fps=30,format=yuv420p,setsar=1[v{n}]")
        graph.append("".join(f"[v{n}]" for n in range(len(images))) + f"concat=n={len(images)}:v=1:a=0[vcat]")
        nimg = len(images)
    else:
        inputs += ["-f", "lavfi", "-t", f"{total:.3f}", "-i", f"color=c=0x101826:s={W}x{H}:r=30"]
        graph.append("[0:v]format=yuv420p,setsar=1[vcat]")
        nimg = 1
    for w in voice_wavs:
        inputs += ["-i", str(Path(w).resolve())]
    a_in = "".join(f"[{nimg + k}:a]apad=pad_dur=0.25[a{k}];" for k in range(len(voice_wavs)))
    a_cat = "".join(f"[a{k}]" for k in range(len(voice_wavs))) + f"concat=n={len(voice_wavs)}:v=0:a=1,loudnorm=I=-14:TP=-1.5:LRA=11,aresample=48000[a]"
    fc = ";".join(graph) + ";[vcat]subtitles=subs.ass[v];" + a_in + a_cat
    cmd = ["ffmpeg", "-v", "error", "-nostdin", "-y", *inputs, "-filter_complex", fc, "-map", "[v]", "-map", "[a]",
           "-t", f"{total:.3f}", "-c:v", "libx264", "-preset", "medium", "-crf", "20", "-pix_fmt", "yuv420p", "-threads", media.FFMPEG_THREADS,
           "-c:a", "aac", "-b:a", "192k", "-ar", "48000", "-movflags", "+faststart", str(out.resolve())]
    media.run(cmd, cwd=work)
    return {"mode": "images" if images else "text_cards", "out_w": W, "out_h": H}


def make_news_short(store, source_id: str, data_dir: Path, *, query: str, chat: Callable, get_text: Callable = http_get_text,
                    image_fn: Callable | None = generate_image, max_images: int = MAX_IMAGES, tts: Callable = tts_lines,
                    actor: str = "editor", story_index: int = 0, rss_keywords: str | None = None,
                    rss_get_text: Callable | None = None, progress: Callable[[str], None] | None = None) -> dict:
    from . import rights
    from .pipeline import enqueue
    src = store.get_source(source_id)
    rights.check(src)
    max_images = max(0, min(max_images, MAX_IMAGES))
    say = progress or (lambda m: None)
    timings, t_all = {}, time.time()
    t = time.time()
    say("fetch")
    gdelt_error = None
    try:
        feed = gdelt_fetch(query, get_text=get_text, store=store)
    except (RateLimited, RuntimeError) as exc:
        feed, gdelt_error = {"articles": [], "source": GDELT_URL}, f"{type(exc).__name__}: {exc}"
    if not feed["articles"] and rss_keywords:                # GDELT throttled/empty -> open publisher RSS, reported
        feed = dict(rss_fetch(rss_keywords, get_text=rss_get_text or get_text, store=store),
                    fallback_reason=gdelt_error or "GDELT: нет статей")
    elif gdelt_error:
        raise RateLimited(gdelt_error)
    timings["fetch_s"] = round(time.time() - t, 1)
    stories = group_stories(feed["articles"])
    if not stories:
        raise RuntimeError("по запросу нет статей (GDELT ищет по машинному переводу на английский: "
                           "используйте английские ключевые слова + sourcelang:russian, либо задайте rss_keywords)")
    story = stories[min(story_index, len(stories) - 1)]
    t = time.time()
    say("script")
    script = write_script(story, chat)
    timings["script_s"] = round(time.time() - t, 1)
    iid = "sh_" + uuid.uuid4().hex[:10]
    work = data_dir / "shorts" / iid
    work.mkdir(parents=True, exist_ok=True)
    t = time.time()
    say("voice")
    voiced = tts(script["lines"], work)
    timings["tts_s"] = round(time.time() - t, 1)
    chunks, total = caption_chunks_from_lines(voiced)
    imgs, gen = [], []
    t = time.time()
    if image_fn and max_images:
        say("images")
        for n in range(max_images):
            r = image_fn(script.get("image_prompt") or "abstract editorial illustration of a news topic, no text", work / f"img{n}.png")
            gen.append(r)
            if r.get("status") != "OK":
                break
            imgs.append(work / f"img{n}.png")
    timings["images_s"] = round(time.time() - t, 1)
    outlets = ", ".join(script.get("outlets") or story["domains"][:2])
    out = work / f"{iid}.mp4"
    t = time.time()
    say("render")
    plan = render_news(out, images=imgs, voice_wavs=[v["wav"] for v in voiced], chunks=chunks, total=total,
                       headline=script["headline"], footer=f"Источник: {outlets}", work=work)
    timings["render_s"] = round(time.time() - t, 1)
    val = media.validate_output(out, expected_w=plan["out_w"], expected_h=plan["out_h"], expected_s=total, src_w=media.TARGET_W)
    media.poster(out, work / "poster.jpg", at=1.0)
    links = "\n".join(f"{a['domain']}: {a['url']}" for a in story["articles"][:4])
    t = time.time()
    meta = metadata.generate(" ".join(script["lines"]), chat=chat, lang="ru", synthetic=True,
                             credit=f"Источники:\n{links}" + (f"\nПоиск: GDELT Project ({GDELT_TERMS})." if feed.get("source") == GDELT_URL else ""))
    timings["metadata_s"] = round(time.time() - t, 1)
    timings["total_s"] = round(time.time() - t_all, 1)
    doc = {"id": iid, "kind": "news", "file": str(out), "poster": str(work / "poster.jpg"), "segment": {"start": 0.0, "end": total, "dur": total},
           "script": script, "story": {"lead": story["lead"], "domains": story["domains"], "articles": story["articles"],
                     "feed": feed.get("source"), "fallback_reason": feed.get("fallback_reason")},
           "framing": plan, "images": gen, "voice": {"engine": "piper", "voice": PIPER_VOICE.stem, "lines": len(voiced)},
           "captions": {"chunks": len(chunks), "language": "ru", "burned": True}, "validation": val, "metadata": meta,
           "privacy": "private", "ai_generated": True, "realistic_synthetic": bool(imgs), "timings": timings,
           "policy": "оригинальный текст и озвучка; чужие видео не используются; факты только из заголовков, источники в описании"}
    it = enqueue(store, source_id, doc, "ready" if val["ok"] else "draft")
    store.audit(actor, "short.created", {"id": iid, "source": source_id, "kind": "news", "state": it["state"], "valid": val["ok"],
                                         "outlets": story["domains"], "images": len(imgs)})
    return it
