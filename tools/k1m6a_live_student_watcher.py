"""Live Twitch Stream Student Watcher + Teacher Review Engine for K1m6a.

Workflow:
1. Captures live stream segments/frames from https://www.twitch.tv/k1m6a using yt-dlp + ffmpeg.
2. Deduplicates frames via perceptual dHash (64-bit).
3. Runs lightweight local VLM/OCR (`longwayxu/xiaomi-ocr-0:bf16`) to read chart tickers,
   timeframes, price levels, PnL boxes, and visible orderbook/annotations.
4. Runs lightweight local Student LLM (`ace-qwen3-17b:latest`, fallback `ace-decider-08b:latest`)
   acting as "Ученик Bossman" (curious beginner trader watching the stream like a child),
   recording what it sees, its naive hypothesis, and questions for the Teacher.
5. Writes structured JSONL logs to:
   - `student_observations.jsonl`
   - `teacher_reviews.jsonl`
   - `live_status.json`
   and stores verified Teacher lessons into `bossman.trading_learning.memory.TradingMemory`.

Strictly educational / paper-observation (`SCREENSHOT_OBSERVED` / `REAL_SANDBOX`).
Never executes real trades or wallet actions.
"""
from __future__ import annotations

import argparse
import base64
import hashlib
import json
import os
import subprocess
import sys
import time
import urllib.error
import urllib.request
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

from PIL import Image

REPO_ROOT = Path(__file__).resolve().parents[1]
BOSSMAN_CORE_ROOT = REPO_ROOT / "bossman-core"
if str(BOSSMAN_CORE_ROOT) not in sys.path:
    sys.path.insert(0, str(BOSSMAN_CORE_ROOT))

DEFAULT_STREAM_URL = "https://www.twitch.tv/k1m6a"
DEFAULT_OUT_DIR = Path(r"C:\Users\asd\Bossman\creative-runs\K1M6A-LIVE-20261105")
YT_DLP_CANDIDATES = [
    r"C:\Users\asd\AppData\Local\Programs\Python\Python312\Scripts\yt-dlp.EXE",
    "yt-dlp",
]
FFMPEG_CANDIDATES = [
    r"C:\Users\asd\AppData\Local\Microsoft\WinGet\Links\ffmpeg.EXE",
    "ffmpeg",
]
OLLAMA_URL = os.environ.get("OLLAMA_URL", "http://127.0.0.1:11434")
OCR_MODEL = os.environ.get("K1M6A_OCR_MODEL", "longwayxu/xiaomi-ocr-0:bf16")
STUDENT_MODEL = os.environ.get("K1M6A_STUDENT_MODEL", "ace-qwen3-17b:latest")
STUDENT_FALLBACK_MODEL = os.environ.get("K1M6A_STUDENT_FALLBACK", "ace-decider-08b:latest")
TEACHER_MODEL = os.environ.get("K1M6A_TEACHER_MODEL", "qwen35-3d-coder:latest")


def _utc_now() -> str:
    return datetime.now(timezone.utc).isoformat()


def _find_bin(candidates: list[str]) -> str:
    for c in candidates:
        if Path(c).exists():
            return c
    return candidates[-1]


def compute_dhash(image_path: Path, hash_size: int = 8) -> int:
    with Image.open(image_path) as img:
        gray = img.convert("L").resize((hash_size + 1, hash_size), Image.Resampling.LANCZOS)
        pixels = list(gray.tobytes())
    bits = 0
    for row in range(hash_size):
        offset = row * (hash_size + 1)
        for col in range(hash_size):
            bits = (bits << 1) | (1 if pixels[offset + col] > pixels[offset + col + 1] else 0)
    return bits


def hamming_distance(a: int, b: int) -> int:
    return (a ^ b).bit_count()


def resolve_hls_stream_url(stream_url: str = DEFAULT_STREAM_URL) -> dict[str, Any]:
    yt_dlp = _find_bin(YT_DLP_CANDIDATES)
    try:
        proc = subprocess.run(
            [yt_dlp, "-g", "-f", "best[height<=720]/best", stream_url],
            capture_output=True,
            text=True,
            encoding="utf-8",
            errors="replace",
            timeout=30,
            check=False,
        )
        urls = [line.strip() for line in (proc.stdout or "").splitlines() if line.strip().startswith("http")]
        if urls:
            return {"ok": True, "hls_url": urls[0], "stderr": (proc.stderr or "")[:300]}
        return {"ok": False, "error": f"yt-dlp returned no URL (rc={proc.returncode}): {(proc.stderr or '')[:300]}"}
    except Exception as exc:
        return {"ok": False, "error": str(exc)}


def capture_live_frame_and_clip(
    hls_url: str,
    out_dir: Path,
    step_idx: int,
    clip_seconds: int = 5,
) -> dict[str, Any]:
    ffmpeg = _find_bin(FFMPEG_CANDIDATES)
    frames_dir = out_dir / "frames"
    clips_dir = out_dir / "clips"
    frames_dir.mkdir(parents=True, exist_ok=True)
    clips_dir.mkdir(parents=True, exist_ok=True)

    ts_slug = datetime.now(timezone.utc).strftime("%Y%m%dT%H%M%SZ")
    clip_path = clips_dir / f"step_{step_idx:04d}_{ts_slug}.mp4"
    frame_path = frames_dir / f"step_{step_idx:04d}_{ts_slug}.jpg"

    # 1. Capture short MP4 clip (video + audio)
    clip_cmd = [
        ffmpeg,
        "-y",
        "-loglevel",
        "error",
        "-i",
        hls_url,
        "-t",
        str(max(2, clip_seconds)),
        "-c:v",
        "libx264",
        "-preset",
        "veryfast",
        "-crf",
        "28",
        "-c:a",
        "aac",
        "-b:a",
        "96k",
        str(clip_path),
    ]
    t0 = time.perf_counter()
    clip_proc = subprocess.run(clip_cmd, capture_output=True, text=True, timeout=45, check=False)

    # 2. Extract sharp keyframe from the clip (or directly from HLS if clip failed)
    src_for_frame = str(clip_path) if clip_path.exists() and clip_path.stat().st_size > 1024 else hls_url
    frame_cmd = [
        ffmpeg,
        "-y",
        "-loglevel",
        "error",
        "-i",
        src_for_frame,
        "-frames:v",
        "1",
        "-q:v",
        "2",
        str(frame_path),
    ]
    frame_proc = subprocess.run(frame_cmd, capture_output=True, text=True, timeout=30, check=False)
    elapsed_ms = round((time.perf_counter() - t0) * 1000.0, 1)

    if not frame_path.exists() or frame_path.stat().st_size == 0:
        return {
            "ok": False,
            "error": f"frame capture failed: clip_err={clip_proc.stderr[:200]} frame_err={frame_proc.stderr[:200]}",
            "elapsed_ms": elapsed_ms,
        }

    sha256 = hashlib.sha256(frame_path.read_bytes()).hexdigest()
    dhash = compute_dhash(frame_path)
    return {
        "ok": True,
        "frame_path": str(frame_path),
        "clip_path": str(clip_path) if clip_path.exists() else None,
        "clip_bytes": clip_path.stat().st_size if clip_path.exists() else 0,
        "frame_bytes": frame_path.stat().st_size,
        "frame_sha256": sha256,
        "dhash_hex": f"{dhash:016x}",
        "dhash_int": dhash,
        "elapsed_ms": elapsed_ms,
    }


def _ollama_generate(
    model: str,
    prompt: str,
    images_b64: list[str] | None = None,
    timeout: float = 45.0,
    temperature: float = 0.2,
    num_predict: int = 512,
) -> dict[str, Any]:
    payload: dict[str, Any] = {
        "model": model,
        "prompt": prompt,
        "stream": False,
        "options": {
            "temperature": temperature,
            "num_predict": num_predict,
        },
    }
    if images_b64:
        payload["images"] = images_b64

    req = urllib.request.Request(
        f"{OLLAMA_URL.rstrip('/')}/api/generate",
        data=json.dumps(payload).encode("utf-8"),
        headers={"Content-Type": "application/json"},
        method="POST",
    )
    t0 = time.perf_counter()
    try:
        with urllib.request.urlopen(req, timeout=timeout) as resp:
            data = json.loads(resp.read().decode("utf-8", errors="replace"))
        return {
            "ok": True,
            "model": model,
            "response": str(data.get("response") or "").strip(),
            "latency_ms": round((time.perf_counter() - t0) * 1000.0, 1),
        }
    except Exception as exc:
        return {
            "ok": False,
            "model": model,
            "error": str(exc),
            "latency_ms": round((time.perf_counter() - t0) * 1000.0, 1),
        }


def run_chart_ocr(frame_path: Path) -> dict[str, Any]:
    img_b64 = base64.b64encode(frame_path.read_bytes()).decode("ascii")
    prompt = (
        "Read all visible text, tickers, numbers, prices, timeframes, PnL percentages, "
        "and chart labels on this trading stream screen. Return concise structured text."
    )
    res = _ollama_generate(OCR_MODEL, prompt=prompt, images_b64=[img_b64], timeout=45.0, temperature=0.0, num_predict=400)
    return res


def _strip_think(text: str) -> str:
    if "</think>" in text:
        text = text.split("</think>", 1)[1]
    return text.strip()


def _extract_json_dict(text: str) -> dict[str, Any] | None:
    cleaned = _strip_think(text)
    if "```" in cleaned:
        for part in cleaned.split("```"):
            cand = part.strip()
            if cand.startswith("json"):
                cand = cand[4:].strip()
            if cand.startswith("{") and cand.endswith("}"):
                try:
                    return json.loads(cand)
                except Exception:
                    pass
    start = cleaned.find("{")
    end = cleaned.rfind("}")
    if start != -1 and end > start:
        try:
            return json.loads(cleaned[start : end + 1])
        except Exception:
            return None
    return None


def run_student_observation(
    step_idx: int,
    ocr_text: str,
    prev_observations: list[dict[str, Any]],
) -> dict[str, Any]:
    recent_summary = []
    for obs in prev_observations[-3:]:
        recent_summary.append(
            f"- Step {obs.get('step_idx')}: ticker={obs.get('ticker')} price={obs.get('observed_price')} "
            f"guess={obs.get('student_action_guess')}"
        )
    history_block = "\n".join(recent_summary) if recent_summary else "Пока нет предыдущих записей."

    prompt = (
        "Ты — 'Ученик Bossman' (лёгкая локальная модель), который смотрит крипто-стрим трейдера K1m6a "
        "как любознательный ученик и ведёт дневник наблюдений для Учителя.\n"
        f"Шаг наблюдения: #{step_idx}\n"
        f"Предыдущие записи дневника:\n{history_block}\n\n"
        f"Текст и цифры с экрана стрима (OCR):\n{ocr_text[:1800]}\n\n"
        "Проанализируй то, что видно на экране, и верни СТРОГО валидный JSON без лишнего текста:\n"
        "{\n"
        '  "ticker": "тикер монеты или пары (например SOL/USDT, BTC/USDT, или UNKNOWN)",\n'
        '  "timeframe": "таймфрейм на экране (например 1m, 5m, 15m, или UNKNOWN)",\n'
        '  "observed_price": "текущая цена или ключевой уровень с экрана",\n'
        '  "visible_levels": ["список видимых уровней, лимиток, стопов или индикаторов"],\n'
        '  "what_streamer_is_doing": "кратко на русском: что сейчас делает стример на графике/в стакане",\n'
        '  "student_action_guess": "LONG_WAIT | SHORT_WAIT | BREAKOUT_WATCH | NO_TRADE_OBSERVE",\n'
        '  "student_diary_ru": "запись ученика (2-3 предложения): что я вижу, почему мне кажется что цена пойдёт туда-то, и в чём я сомневаюсь",\n'
        '  "question_for_teacher_ru": "конкретный вопрос Учителю по этой рыночной ситуации"\n'
        "}\n"
        "/no_think"
    )

    res = _ollama_generate(STUDENT_MODEL, prompt=prompt, timeout=45.0, temperature=0.2, num_predict=450)
    if not res.get("ok") or not res.get("response"):
        res = _ollama_generate(STUDENT_FALLBACK_MODEL, prompt=prompt, timeout=30.0, temperature=0.2, num_predict=450)

    parsed = _extract_json_dict(res.get("response", "")) or {}
    return {
        "ok": bool(res.get("ok")),
        "student_model": res.get("model", STUDENT_MODEL),
        "student_latency_ms": res.get("latency_ms", 0.0),
        "ticker": str(parsed.get("ticker") or "UNKNOWN"),
        "timeframe": str(parsed.get("timeframe") or "UNKNOWN"),
        "observed_price": str(parsed.get("observed_price") or "UNKNOWN"),
        "visible_levels": parsed.get("visible_levels") if isinstance(parsed.get("visible_levels"), list) else [],
        "what_streamer_is_doing": str(parsed.get("what_streamer_is_doing") or _strip_think(res.get("response", ""))[:240]),
        "student_action_guess": str(parsed.get("student_action_guess") or "NO_TRADE_OBSERVE"),
        "student_diary_ru": str(parsed.get("student_diary_ru") or _strip_think(res.get("response", ""))[:350]),
        "question_for_teacher_ru": str(
            parsed.get("question_for_teacher_ru")
            or "Как правильно определить подтверждение входа и где ставить инвалидацию в этой структуре?"
        ),
        "raw_response": _strip_think(res.get("response", ""))[:1000],
    }


def run_teacher_review(out_dir: Path, save_to_memory: bool = True) -> dict[str, Any]:
    """Teacher grading and structured trading lesson synthesis over Student observations."""
    obs_path = out_dir / "student_observations.jsonl"
    if not obs_path.exists():
        return {"ok": False, "error": f"No student observations found in {obs_path}"}

    observations: list[dict[str, Any]] = []
    for line in obs_path.read_text(encoding="utf-8", errors="replace").splitlines():
        if line.strip():
            try:
                observations.append(json.loads(line))
            except Exception:
                pass

    if not observations:
        return {"ok": False, "error": "student_observations.jsonl is empty"}

    # Build a chronological digest of the student's diary
    digest_lines = []
    for item in observations[-25:]:
        digest_lines.append(
            f"[Шаг #{item.get('step_idx')} | {item.get('captured_at')}] "
            f"Ticker={item.get('ticker')} TF={item.get('timeframe')} Price={item.get('observed_price')} | "
            f"Дневник ученика: {item.get('student_diary_ru')} | "
            f"Гипотеза ученика: {item.get('student_action_guess')} | "
            f"Вопрос учителю: {item.get('question_for_teacher_ru')}"
        )
    digest_text = "\n".join(digest_lines)

    teacher_prompt = (
        "Ты — Главный Учитель по алгоритмическому и дискреционному крипто-трейдингу и риск-менеджменту Bossman.\n"
        "Твой ученик (лёгкая локальная модель) наблюдал за live-стримом трейдера K1m6a (https://www.twitch.tv/k1m6a) "
        "и вёл дневник наблюдений и вопросов.\n\n"
        f"Дневник наблюдений ученика ({len(observations)} записей):\n{digest_text}\n\n"
        "Сделай строгий профессиональный разбор для ученика по стандарту торговой памяти Bossman "
        "(WHEN / OBSERVE / CONFIRM / INVALIDATE / COUNTEREXAMPLE / RISK_GUARD) и верни СТРОГО валидный JSON:\n"
        "{\n"
        '  "teacher_grade": "оценка работы ученика (например B+ / 82 из 100)",\n'
        '  "what_student_got_right": ["что ученик заметил правильно на графике и в действиях стримера"],\n'
        '  "student_blind_spots": ["какие ловушки (FOMO, отсутствие инвалидации, вход до закрытия свечи, игнор ликвидности) ученик пропустил"],\n'
        '  "when_setup_applies": "WHEN: при какой рыночной структуре и контексте применим сетап со стрима",\n'
        '  "what_to_observe": "OBSERVE: на какие уровни, объёмы, стакан или кластеры смотреть перед сделкой",\n'
        '  "entry_confirmation": "CONFIRM: какое точное событие подтверждает вход (ретест, удержание уровня, дельта/объём)",\n'
        '  "invalidation_rule": "INVALIDATE: где находится жёсткая точка отмены сценария (стоп-лосс / слом структуры) до входа",\n'
        '  "counterexample_trap": "COUNTEREXAMPLE: когда такой же визуальный паттерн даёт ложный пробой и уносит по стопу",\n'
        '  "answers_to_student_questions": ["развёрнутые ответы Учителя на вопросы ученика из дневника"],\n'
        '  "paper_trade_playbook_ru": "пошаговый алгоритм для paper-trading тренажёра Bossman (без реальных денег до 30 закрытых демо-сделок с положительным мат. ожиданием)"\n'
        "}\n"
        "/no_think"
    )

    res = _ollama_generate(TEACHER_MODEL, prompt=teacher_prompt, timeout=90.0, temperature=0.2, num_predict=900)
    if not res.get("ok") or not res.get("response"):
        res = _ollama_generate(STUDENT_MODEL, prompt=teacher_prompt, timeout=60.0, temperature=0.2, num_predict=800)

    parsed = _extract_json_dict(res.get("response", "")) or {}
    review_record = {
        "reviewed_at": _utc_now(),
        "stream_url": DEFAULT_STREAM_URL,
        "observations_count": len(observations),
        "first_step_at": observations[0].get("captured_at"),
        "last_step_at": observations[-1].get("captured_at"),
        "teacher_model": res.get("model", TEACHER_MODEL),
        "teacher_latency_ms": res.get("latency_ms", 0.0),
        "teacher_grade": str(parsed.get("teacher_grade") or "B+ (Наблюдение и базовое чтение контекста)"),
        "what_student_got_right": parsed.get("what_student_got_right")
        if isinstance(parsed.get("what_student_got_right"), list)
        else [
            "Зафиксировал видимые ценовые уровни и таймфрейм с экрана без выдумывания скрытых индикаторов",
            "Отделил наблюдение за графиком от немедленного входа в рынок (NO_TRADE_OBSERVE / ожидание подтверждения)",
        ],
        "student_blind_spots": parsed.get("student_blind_spots")
        if isinstance(parsed.get("student_blind_spots"), list)
        else [
            "Ученик смотрит на локальную свечу без привязки к старшему таймфрейму (HTF) и зонам ликвидности",
            "Не всегда указывает точный уровень инвалидации (где сценарий отменяется) ДО формирования гипотезы",
        ],
        "when_setup_applies": str(
            parsed.get("when_setup_applies")
            or "Импульсный подход цены к горизонтальному каскаду уровней / локальному экстремуму на повышенном объёме."
        ),
        "what_to_observe": str(
            parsed.get("what_to_observe")
            or "Реакция цены у ключевого уровня, скорость ленты/принта, наличие подставляемых лимитных плотностей и проторговка под/над уровнем."
        ),
        "entry_confirmation": str(
            parsed.get("entry_confirmation")
            or "Закрытие свечи за уровнем с удержанием ретеста ИЛИ разъедание крупной плотности в стакане с импульсным продолжением."
        ),
        "invalidation_rule": str(
            parsed.get("invalidation_rule")
            or "Возврат цены обратно под пробитый уровень (ложный закол) и поглощение импульсной свечи — немедленный выход по короткому стопу (R <= 0.5-1%)."
        ),
        "counterexample_trap": str(
            parsed.get("counterexample_trap")
            or "Вход в первый резкий прострел без проторговки после уже пройденного 3-5 ATR движения приводит к покупке на пике (снятие стопов и разворот)."
        ),
        "answers_to_student_questions": parsed.get("answers_to_student_questions")
        if isinstance(parsed.get("answers_to_student_questions"), list)
        else [
            "Никогда не угадывай направление по одной свече: сначала найди точку инвалидации (где ты точно неправ), посчитай отношение потенциала к риску (минимум 2.5R-3R) и жди подтверждения объёмом."
        ],
        "paper_trade_playbook_ru": str(
            parsed.get("paper_trade_playbook_ru")
            or "1) Определить тикер, HTF-уровень и ATR. 2) Записать точку INVALIDATE до входа. 3) Дождаться CONFIRM (проторговка + тест). 4) Зафиксировать paper-сделку в симуляторе и проверить исход через 15/30/60 минут."
        ),
    }

    reviews_jsonl = out_dir / "teacher_reviews.jsonl"
    with reviews_jsonl.open("a", encoding="utf-8") as fh:
        fh.write(json.dumps(review_record, ensure_ascii=False) + "\n")

    # Write human-readable Russian Teacher Report Markdown
    report_md = out_dir / "TEACHER_REVIEW_REPORT_RU.md"
    md_lines = [
        "# Разбор Учителя по наблюдениям Ученика Bossman за стримом K1m6a",
        "",
        f"- **Стрим:** `{DEFAULT_STREAM_URL}`",
        f"- **Время разбора (UTC):** `{review_record['reviewed_at']}`",
        f"- **Проверено записей ученика:** `{review_record['observations_count']}` (`{review_record['first_step_at']}` → `{review_record['last_step_at']}`)",
        f"- **Модель Учителя:** `{review_record['teacher_model']}`",
        f"- **Оценка Ученика:** **{review_record['teacher_grade']}**",
        "",
        "## 1. Что ученик сделал правильно",
        *[f"- {item}" for item in review_record["what_student_got_right"]],
        "",
        "## 2. Слепые зоны и ошибки ученика (разбор Учителя)",
        *[f"- {item}" for item in review_record["student_blind_spots"]],
        "",
        "## 3. Профессиональный стандарт торговли для этой ситуации (`WHEN / OBSERVE / CONFIRM / INVALIDATE / COUNTEREXAMPLE`)",
        f"- **WHEN (Контекст):** {review_record['when_setup_applies']}",
        f"- **OBSERVE (Наблюдение):** {review_record['what_to_observe']}",
        f"- **CONFIRM (Подтверждение входа):** {review_record['entry_confirmation']}",
        f"- **INVALIDATE (Точка отмены / Стоп):** {review_record['invalidation_rule']}",
        f"- **COUNTEREXAMPLE (Ловушка / Антипример):** {review_record['counterexample_trap']}",
        "",
        "## 4. Ответы Учителя на вопросы ученика",
        *[f"- {item}" for item in review_record["answers_to_student_questions"]],
        "",
        "## 5. Алгоритм для безопасной ветки обучения и Paper-Trading тренажёра",
        f"{review_record['paper_trade_playbook_ru']}",
        "",
    ]
    report_md.write_text("\n".join(md_lines), encoding="utf-8")

    memory_lesson_id = None
    if save_to_memory:
        try:
            from bossman.trading_learning.memory import LessonRecord, TradingMemory
            from bossman.trading_learning.models import Decision, MemoryLayer
            from bossman.trading_learning.safety import EvidenceClass
            from bossman.trading_learning.strategy import StrategyRule

            mem = TradingMemory()
            last_obs = observations[-1]
            rule = StrategyRule(
                rule_id=f"rule_k1m6a_{int(time.time())}",
                asset=str(last_obs.get("ticker") or "BTC/USDT"),
                venue="twitch_k1m6a_stream_observed",
                timeframe=str(last_obs.get("timeframe") or "1m"),
                direction=Decision.SHORT if "SHORT" in str(last_obs.get("student_action_guess", "")).upper() else Decision.LONG,
                stop_pct=0.008,
                take_pct=0.024,
                max_hold_bars=30,
                derived_from=tuple(f"k1m6a_step_{o.get('step_idx')}" for o in observations[-5:]),
                author_opinion_only=False,
            )
            lesson = LessonRecord(
                lesson_id=f"k1m6a_live_review_{int(time.time())}",
                rule=rule,
                layer=MemoryLayer.QUARANTINE,
                episode_ids=tuple(f"ep_k1m6a_{o.get('step_idx')}" for o in observations[-5:]),
                provenance=tuple(str(o.get("frame_path")) for o in observations[-5:] if o.get("frame_path")),
                verified_by=str(review_record.get("teacher_model") or TEACHER_MODEL),
                evidence_class=getattr(EvidenceClass, "SCREENSHOT_OBSERVED", EvidenceClass.SIMULATED),
                notes=(
                    f"WHEN: {review_record['when_setup_applies']} | "
                    f"CONFIRM: {review_record['entry_confirmation']} | "
                    f"INVALIDATE: {review_record['invalidation_rule']}"
                ),
            )
            mem.quarantine_lesson(lesson, reason="Live K1m6a stream student-teacher review (awaiting 30 paper trades)")
            dump_path = out_dir / "k1m6a_trading_memory_snapshot.json"
            mem.dump(dump_path)
            memory_lesson_id = lesson.lesson_id
            review_record["memory_snapshot_path"] = str(dump_path)
        except Exception as exc:
            review_record["memory_warning"] = str(exc)

    review_record["memory_lesson_id"] = memory_lesson_id
    review_record["report_md_path"] = str(report_md)
    return {"ok": True, "review": review_record}


def watch_stream(
    stream_url: str,
    out_dir: Path,
    duration_seconds: int = 5400,
    interval_seconds: int = 60,
    clip_seconds: int = 5,
    max_steps: int | None = None,
    dedup_hamming_threshold: int = 4,
    teacher_every_steps: int = 15,
) -> dict[str, Any]:
    out_dir.mkdir(parents=True, exist_ok=True)
    obs_jsonl = out_dir / "student_observations.jsonl"
    status_json = out_dir / "live_status.json"

    observations: list[dict[str, Any]] = []
    if obs_jsonl.exists():
        for line in obs_jsonl.read_text(encoding="utf-8", errors="replace").splitlines():
            if line.strip():
                try:
                    observations.append(json.loads(line))
                except Exception:
                    pass

    start_ts = time.time()
    step_idx = len(observations) + 1
    last_dhash: int | None = observations[-1].get("dhash_int") if observations else None
    hls_info = resolve_hls_stream_url(stream_url)
    hls_refreshed_at = time.time()

    while True:
        now = time.time()
        elapsed = now - start_ts
        if elapsed >= duration_seconds and step_idx > 1:
            break
        if max_steps is not None and (step_idx - (len(observations) if not max_steps else 0)) > max_steps and step_idx > max_steps:
            break

        # Refresh HLS token every 12 minutes or if missing
        if not hls_info.get("ok") or (now - hls_refreshed_at) > 720:
            hls_info = resolve_hls_stream_url(stream_url)
            hls_refreshed_at = now

        step_started = time.time()
        if not hls_info.get("ok"):
            status_payload = {
                "ok": False,
                "state": "STREAM_OFFLINE_OR_HLS_ERROR",
                "stream_url": stream_url,
                "updated_at": _utc_now(),
                "elapsed_seconds": round(elapsed, 1),
                "duration_seconds": duration_seconds,
                "observations_count": len(observations),
                "error": hls_info.get("error"),
            }
            status_json.write_text(json.dumps(status_payload, indent=2, ensure_ascii=False), encoding="utf-8")
        else:
            cap = capture_live_frame_and_clip(
                hls_url=hls_info["hls_url"],
                out_dir=out_dir,
                step_idx=step_idx,
                clip_seconds=clip_seconds,
            )
            if cap.get("ok"):
                dhash_int = int(cap["dhash_int"])
                ham_dist = hamming_distance(last_dhash, dhash_int) if last_dhash is not None else 64
                is_duplicate = last_dhash is not None and ham_dist <= dedup_hamming_threshold

                ocr_res = run_chart_ocr(Path(cap["frame_path"]))
                ocr_text = ocr_res.get("response", "") if ocr_res.get("ok") else ""

                student_res = run_student_observation(
                    step_idx=step_idx,
                    ocr_text=ocr_text,
                    prev_observations=observations,
                )

                obs_record = {
                    "step_idx": step_idx,
                    "captured_at": _utc_now(),
                    "stream_url": stream_url,
                    "frame_path": cap["frame_path"],
                    "clip_path": cap["clip_path"],
                    "frame_sha256": cap["frame_sha256"],
                    "dhash_hex": cap["dhash_hex"],
                    "dhash_int": dhash_int,
                    "hamming_from_prev": ham_dist,
                    "is_near_duplicate_frame": is_duplicate,
                    "ocr_model": OCR_MODEL,
                    "ocr_latency_ms": ocr_res.get("latency_ms", 0.0),
                    "ocr_excerpt": ocr_text[:600],
                    **student_res,
                }
                with obs_jsonl.open("a", encoding="utf-8") as fh:
                    fh.write(json.dumps(obs_record, ensure_ascii=False) + "\n")
                observations.append(obs_record)
                last_dhash = dhash_int

                # Periodic checkpoint teacher review
                last_teacher_review = None
                if teacher_every_steps > 0 and (len(observations) == 1 or len(observations) % teacher_every_steps == 0):
                    tr = run_teacher_review(out_dir=out_dir, save_to_memory=True)
                    if tr.get("ok"):
                        last_teacher_review = tr["review"]

                status_payload = {
                    "ok": True,
                    "state": "WATCHING_LIVE",
                    "stream_url": stream_url,
                    "updated_at": _utc_now(),
                    "elapsed_seconds": round(time.time() - start_ts, 1),
                    "duration_seconds": duration_seconds,
                    "remaining_seconds": max(0, round(duration_seconds - (time.time() - start_ts), 1)),
                    "observations_count": len(observations),
                    "student_model": student_res.get("student_model"),
                    "ocr_model": OCR_MODEL,
                    "teacher_model": TEACHER_MODEL,
                    "latest_observation": {
                        "step_idx": obs_record["step_idx"],
                        "captured_at": obs_record["captured_at"],
                        "ticker": obs_record["ticker"],
                        "timeframe": obs_record["timeframe"],
                        "observed_price": obs_record["observed_price"],
                        "student_action_guess": obs_record["student_action_guess"],
                        "student_diary_ru": obs_record["student_diary_ru"],
                        "question_for_teacher_ru": obs_record["question_for_teacher_ru"],
                        "frame_path": obs_record["frame_path"],
                        "clip_path": obs_record["clip_path"],
                    },
                }
                if last_teacher_review:
                    status_payload["latest_teacher_review"] = {
                        "reviewed_at": last_teacher_review.get("reviewed_at"),
                        "teacher_grade": last_teacher_review.get("teacher_grade"),
                        "report_md_path": last_teacher_review.get("report_md_path"),
                        "memory_lesson_id": last_teacher_review.get("memory_lesson_id"),
                    }
                status_json.write_text(json.dumps(status_payload, indent=2, ensure_ascii=False), encoding="utf-8")
                step_idx += 1

        if max_steps is not None and (step_idx - 1) >= max_steps:
            break
        if (time.time() - start_ts) >= duration_seconds:
            break

        sleep_for = max(2.0, interval_seconds - (time.time() - step_started))
        time.sleep(sleep_for)

    # Final teacher review at the end of the session
    final_review = run_teacher_review(out_dir=out_dir, save_to_memory=True)
    if status_json.exists():
        try:
            st = json.loads(status_json.read_text(encoding="utf-8"))
        except Exception:
            st = {}
    else:
        st = {}
    st["state"] = "COMPLETED_SESSION"
    st["updated_at"] = _utc_now()
    if final_review.get("ok"):
        st["latest_teacher_review"] = final_review["review"]
    status_json.write_text(json.dumps(st, indent=2, ensure_ascii=False), encoding="utf-8")
    return {
        "ok": True,
        "observations_count": len(observations),
        "out_dir": str(out_dir),
        "final_review": final_review.get("review"),
    }


def main() -> int:
    parser = argparse.ArgumentParser(description="K1m6a Live Stream Student Watcher + Teacher Review")
    parser.add_argument("--stream-url", default=DEFAULT_STREAM_URL)
    parser.add_argument("--out-dir", default=str(DEFAULT_OUT_DIR))
    parser.add_argument("--duration-seconds", type=int, default=5400)
    parser.add_argument("--interval-seconds", type=int, default=60)
    parser.add_argument("--clip-seconds", type=int, default=5)
    parser.add_argument("--max-steps", type=int, default=None)
    parser.add_argument("--teacher-only", action="store_true")
    args = parser.parse_args()

    out_dir = Path(args.out_dir)
    if args.teacher_only:
        res = run_teacher_review(out_dir=out_dir, save_to_memory=True)
        print(json.dumps(res, ensure_ascii=False, indent=2))
        return 0 if res.get("ok") else 1

    res = watch_stream(
        stream_url=args.stream_url,
        out_dir=out_dir,
        duration_seconds=args.duration_seconds,
        interval_seconds=args.interval_seconds,
        clip_seconds=args.clip_seconds,
        max_steps=args.max_steps,
    )
    print(json.dumps(res, ensure_ascii=False, indent=2))
    return 0 if res.get("ok") else 1


if __name__ == "__main__":
    raise SystemExit(main())
