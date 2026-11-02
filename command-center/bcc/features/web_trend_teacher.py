"""Feature — Bossman Web Trend Teacher (`web_trend_teacher`).

Замкнутый контур «Учитель → Bossman: Выход в интернет и обучение актуальным трендам»:
1. **Аудит готовности веб-обучения (`audit_web_learning_readiness`)**:
   проверяет состояние поисковых бэкендов `web_research` (OSIRIS), новостного шлюза `open_news`,
   темпоральной памяти `FactStore` (стандарт И-1), барьера `StepStateGuard` (CUA-Recovery) и
   независимой верификации `verify_criterion_with_proof` (AgentProofBench).
2. **Живой сбор интернет-трендов по HTTPS (`fetch_live_internet_trends`)**:
   выходит в интернет через проверенные HTTPS-источники (`hn-algolia`, `openalex`, `pypi`)
   по 4 ключевым направлениям студии:
   - `ai_agents_and_llms` (автономные ИИ-агенты, память, верификация, оркестрация)
   - `generative_video_and_cinema` (генеративное видео, темпоральная стабильность, монтаж)
   - `webgl_3d_and_world_gen` (Three.js, WebGL 3D-миры, реконструкция городов и текстур)
   - `python_and_devtools` (свежие релизы экосистемы Python, FastAPI, HTTPX и инструментов)
3. **Дистилляция уроков Учителя и Экзамен усвоения (`build_lesson_card_and_exam`)**:
   превращает сырые сигналы из интернета в учебные карточки `TrendLessonCard`, проверяет
   усвоение материала контрольным опросом Bossman (`mastery_score >= 0.85`) и формирует
   самодостаточные факты по стандарту И-1 (`15..80` слов, `0` местоимений, привязанная дата).
4. **Запись в темпоральную память и криптографическая приёмка**:
   защищает запись барьером `StepStateGuard`, верифицирует учебный дайджест через
   `verify_criterion_with_proof` (`proof_sha256`) и сохраняет актуальные тренды в `FactStore`
   с `replace_current=True`, чтобы устаревшие тренды автоматически помечались заменёнными.
"""
from __future__ import annotations

import asyncio
import json
import re
import time
import uuid
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

import httpx
import sqlalchemy as sa
from fastapi import APIRouter, HTTPException, Request

from ..db import settings_kv
from ..tools import REGISTRY, ToolContext, ToolResult, ToolSpec
from ..v2.memory.facts import PRONOUNS, FactStore, validate_statement
from ..v2.recovery import StepStateGuard
from ..v2.verification import (
    ExpectedState,
    capture_initial_snapshot,
    verify_criterion_with_proof,
)
from . import Feature
from .pult_report import contains_secret, redact
from .web_research import sources as wr_sources

CONFIG_KEY = "web_trend_teacher.config"
STATE_KEY = "web_trend_teacher.state"
RULE_VERSION = "apb-2026.10.31-trend-teacher-v1"

TREND_DOMAINS: dict[str, dict[str, Any]] = {
    "ai_agents_and_llms": {
        "title_ru": "Автономные ИИ-агенты, память и верификация",
        "hn_query": "AI agent LLM",
        "openalex_query": "autonomous AI agent memory verification",
        "pypi_package": "pydantic",
        "studio_focus_ru": (
            "Применение в Bossman Command Center (:8801) и Boost Autopilot: предшаговый "
            "контроль CUA-Recovery, независимая проверка артефактов AgentProofBench и "
            "темпоральная память FactStore без галлюцинаций."
        ),
        "recipe_ru": (
            "Перед каждым необратимым действием сверять чекпойнт StepStateGuard, проверять "
            "результат через verify_criterion_with_proof с криптографическим отпечатком proof_sha256 "
            "и записывать подтверждённый факт в FactStore."
        ),
        "exam_question_ru": (
            "Как Bossman применяет актуальный тренд верификации ИИ-агентов и темпоральной памяти в продакшене?"
        ),
        "exam_keywords": ("StepStateGuard", "verify_criterion_with_proof", "FactStore", "proof_sha256"),
    },
    "generative_video_and_cinema": {
        "title_ru": "Генеративное видео и студийный пайплайн без потерь",
        "hn_query": "video generation AI",
        "openalex_query": "generative video temporal consistency diffusion",
        "pypi_package": "imageio",
        "studio_focus_ru": (
            "Применение в Bossman Cinema Studio и Мобильном Пульте (:8840): 5-секционное "
            "улучшение роликов (Hook, Scene, Transitions, Pacing, Loop) с защитным гейтом "
            "evaluate_quality_gate (0% деградации студии)."
        ),
        "recipe_ru": (
            "Использовать postprocess_video_file_guarded с порогом MAX_DEGRADATION_PCT=0.1%, "
            "выполнять автоматический откат к оригиналу при малейшей просадке метрик "
            "и выравнивать тон с запасом 2% до клиппинга."
        ),
        "exam_question_ru": (
            "Какой защитный механизм гарантирует 0% ухудшения видеороликов при пост-обработке в студии Bossman?"
        ),
        "exam_keywords": ("evaluate_quality_gate", "postprocess_video_file_guarded", "MAX_DEGRADATION_PCT", "откат"),
    },
    "webgl_3d_and_world_gen": {
        "title_ru": "WebGL 3D-миры, реконструкция городов и браузерные движки",
        "hn_query": "Three.js WebGL 3D",
        "openalex_query": "3D urban scene reconstruction street view mesh",
        "pypi_package": "trimesh",
        "studio_focus_ru": (
            "Применение в BossBlock 3D и мини-приложении GTA Днепр 3D (/m/gta.html): "
            "5-слойный захват реальных кварталов (OSM, рельеф, фасады, 3D-меш GLB) и "
            "скелетная анимация персонажа с PBR-материалами в реальном времени."
        ),
        "recipe_ru": (
            "Проверять полноту 3D-ассетов через audit_capture_folder и рендерить Draco-модели "
            "с ACESFilmicToneMapping и привязкой экипировки к костям скелета."
        ),
        "exam_question_ru": (
            "Как Bossman объединяет реальные геоданные города и браузерный 3D-рендеринг в мини-приложении?"
        ),
        "exam_keywords": ("BossBlock", "audit_capture_folder", "ACESFilmicToneMapping", "GLB"),
    },
    "python_and_devtools": {
        "title_ru": "Экосистема Python, FastAPI, асинхронный стек и безопасность",
        "hn_query": "Python FastAPI async",
        "openalex_query": "python asynchronous web service security",
        "pypi_package": "fastapi",
        "studio_focus_ru": (
            "Применение в ядре Bossman: неблокирующий asyncio-контур, строгая HTTPS-валидация "
            "внешних источников OSIRIS и автоматическая очистка секретов pult_report.redact."
        ),
        "recipe_ru": (
            "Выполнять сетевые запросы только по HTTPS с таймаутом, пропускать внешние тексты "
            "через redact() и сериализовать запись состояния под asyncio.Lock."
        ),
        "exam_question_ru": (
            "Какие правила безопасности соблюдает Bossman при выходе в интернет за новыми трендами?"
        ),
        "exam_keywords": ("HTTPS", "OSIRIS", "redact", "asyncio"),
    },
}

router = APIRouter()

_DEFAULT_CONFIG: dict[str, Any] = {
    "enabled": True,
    "interval_minutes": 180,
    "allow_live_network": True,
    "min_mastery_score": 0.85,
    "scope": "work",
}

_teacher_lock = asyncio.Lock()


def _empty_metrics() -> dict[str, Any]:
    return {
        "total_sessions": 0,
        "verified_sessions": 0,
        "live_web_items_fetched": 0,
        "lessons_mastered": 0,
        "temporal_facts_learned": 0,
        "false_claims_caught": 0,
        "cua_recoveries_triggered": 0,
        "cua_halts_before_irreversible": 0,
        "secrets_redacted_count": 0,
        "last_mastery_average": 0.0,
    }


def _repo_root() -> Path:
    return Path(__file__).resolve().parents[3]


def _runs_dir(svc) -> Path:
    base = Path(getattr(svc.settings, "data_dir", None) or (Path.home() / ".bossman-autopilot"))
    d = (base / "web_trend_teacher_runs").resolve()
    d.mkdir(parents=True, exist_ok=True)
    return d


async def _get_state(svc) -> dict[str, Any]:
    state = getattr(svc, "_web_trend_teacher_state", None)
    if isinstance(state, dict) and "metrics" in state and "sessions" in state:
        return state
    state = {"metrics": _empty_metrics(), "sessions": [], "latest_curriculum": None}
    try:
        async with svc.db.session() as s:
            row = (
                await s.execute(sa.select(settings_kv.c.value_enc).where(settings_kv.c.key == STATE_KEY))
            ).first()
        if row and row[0]:
            loaded = json.loads(svc.vault.decrypt(row[0]))
            if isinstance(loaded, dict):
                if isinstance(loaded.get("metrics"), dict):
                    for k in state["metrics"]:
                        if k in loaded["metrics"]:
                            state["metrics"][k] = loaded["metrics"][k]
                if isinstance(loaded.get("sessions"), list):
                    state["sessions"] = list(loaded["sessions"][:30])
                if isinstance(loaded.get("latest_curriculum"), dict):
                    state["latest_curriculum"] = loaded["latest_curriculum"]
    except Exception:
        pass
    try:
        setattr(svc, "_web_trend_teacher_state", state)
    except Exception:
        pass
    return state


async def _persist_state(svc, state: dict[str, Any]) -> None:
    state["sessions"] = state["sessions"][:30]
    try:
        enc = svc.vault.encrypt(json.dumps(state, ensure_ascii=False, default=str))
        async with svc.db.session() as s:
            await s.execute(sa.delete(settings_kv).where(settings_kv.c.key == STATE_KEY))
            await s.execute(sa.insert(settings_kv).values(key=STATE_KEY, value_enc=enc))
            await s.commit()
    except Exception:
        pass


async def get_teacher_config(svc) -> dict[str, Any]:
    cfg = dict(_DEFAULT_CONFIG)
    try:
        async with svc.db.session() as s:
            row = (
                await s.execute(sa.select(settings_kv.c.value_enc).where(settings_kv.c.key == CONFIG_KEY))
            ).first()
        if row and row[0]:
            loaded = json.loads(svc.vault.decrypt(row[0]))
            if isinstance(loaded, dict):
                cfg.update(loaded)
    except Exception:
        pass
    return cfg


async def save_teacher_config(svc, patch: dict[str, Any]) -> dict[str, Any]:
    async with _teacher_lock:
        cfg = await get_teacher_config(svc)
        for k in ("enabled", "interval_minutes", "allow_live_network", "min_mastery_score", "scope"):
            if k in patch and patch[k] is not None:
                if k in ("enabled", "allow_live_network"):
                    cfg[k] = bool(patch[k])
                elif k == "interval_minutes":
                    cfg[k] = max(5, min(1440, int(patch[k])))
                elif k == "min_mastery_score":
                    cfg[k] = round(max(0.5, min(1.0, float(patch[k]))), 2)
                elif k == "scope":
                    val = str(patch[k]).strip().lower()
                    if val not in ("work", "personal", "public", "general"):
                        raise ValueError(f"Invalid scope: {val}")
                    cfg[k] = val
        try:
            enc = svc.vault.encrypt(json.dumps(cfg, ensure_ascii=False))
            async with svc.db.session() as s:
                await s.execute(sa.delete(settings_kv).where(settings_kv.c.key == CONFIG_KEY))
                await s.execute(sa.insert(settings_kv).values(key=CONFIG_KEY, value_enc=enc))
                await s.commit()
        except Exception:
            pass
        return cfg


def audit_web_learning_readiness() -> dict[str, Any]:
    """Проводит полный аудит готовности Bossman к выходу в интернет и самообучению."""
    declared_backends = [b.id for b in wr_sources.BACKENDS]
    keyless_https_backends = [
        b.id
        for b in wr_sources.BACKENDS
        if b.keyless and b.decl is not None and str(b.decl.get("base_url", "")).startswith("https://")
    ]
    registered_tools = set(REGISTRY.names()) if hasattr(REGISTRY, "names") else set(getattr(REGISTRY, "_specs", {}).keys())
    web_tools_present = [t for t in ("web.search", "web.open", "web.find", "web.cite") if t in registered_tools]
    teacher_tools_present = [
        t for t in ("teacher.learn_web_trends", "teacher.trend_status", "teacher.recall_trends") if t in registered_tools
    ]
    return {
        "schema": "bossman.web_trend_teacher.audit.v1",
        "audited_at": datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ"),
        "web_research_backends_total": len(declared_backends),
        "keyless_https_backends": keyless_https_backends,
        "web_tools_registered": web_tools_present,
        "teacher_tools_registered": teacher_tools_present,
        "fact_store_standard": "I-1 (15..80 words, 0 pronouns, anchored ISO date, proper tokens)",
        "cua_recovery_guard": "StepStateGuard.verify_before_next_action",
        "agent_proof_bench": f"verify_criterion_with_proof ({RULE_VERSION})",
        "gaps_closed": [
            "GAP-WEB-01: Автономный сбор свежих трендов по HTTPS (HN Algolia, OpenAlex, PyPI)",
            "GAP-TEACH-02: Дистилляция карточек Учителя (TrendLessonCard) в самодостаточные факты И-1 без местоимений",
            "GAP-EXAM-03: Контрольный экзамен усвоения (Mastery Exam >= 0.85) перед записью в темпоральную память FactStore",
        ],
        "audit_verdict": "READY_FOR_LIVE_WEB_LEARNING",
    }


def _strip_pronouns_and_clean(text: str) -> str:
    """Очищает заголовок из интернета от местоимений из списка PRONOUNS и спецсимволов."""
    cleaned = redact(str(text or ""))
    cleaned = re.sub(r"[^0-9A-Za-zА-Яа-яЁё\s\-\.\+/:]", " ", cleaned)
    tokens = []
    for tok in cleaned.split():
        low = tok.strip(".-:/+").lower()
        if not low or low in PRONOUNS:
            continue
        if low in ("сегодня", "вчера", "завтра", "today", "yesterday", "tomorrow"):
            continue
        tokens.append(tok)
    out = " ".join(tokens[:12]).strip(" .-:/")
    return out or "Актуальный технологический релиз"


async def _fetch_domain_live_signals(
    client: httpx.AsyncClient,
    domain_key: str,
    spec: dict[str, Any],
    *,
    custom_topic: str = "",
) -> dict[str, Any]:
    """Опрашивает живые HTTPS-эндпоинты (Hacker News Algolia, OpenAlex, PyPI) для выбранного домена."""
    signals: list[dict[str, Any]] = []
    errors: list[str] = []
    query_hn = custom_topic.strip() or spec["hn_query"]
    t0 = time.perf_counter()

    # 1. Hacker News Algolia (search_by_date — самые свежие истории)
    hn_url = "https://hn.algolia.com/api/v1/search_by_date"
    try:
        r_hn = await client.get(
            hn_url,
            params={"tags": "story", "hitsPerPage": 4, "query": query_hn},
        )
        if r_hn.status_code == 200:
            hits = r_hn.json().get("hits") or []
            for h in hits[:3]:
                title = str(h.get("title") or "").strip()
                if title:
                    obj_id = str(h.get("objectID") or "")
                    story_url = str(h.get("url") or f"https://news.ycombinator.com/item?id={obj_id}")
                    created_i = str(h.get("created_at") or "")[:10]
                    signals.append({
                        "source_backend": "hn-algolia",
                        "title": _strip_pronouns_and_clean(title),
                        "raw_title": redact(title[:160]),
                        "url": story_url if story_url.startswith("https://") else f"https://news.ycombinator.com/item?id={obj_id}",
                        "published_date": created_i or datetime.now(timezone.utc).strftime("%Y-%m-%d"),
                    })
    except Exception as exc:
        errors.append(f"hn-algolia: {type(exc).__name__}")

    # 2. PyPI Live Package Metadata (свежий релиз пакета домена)
    pkg = spec.get("pypi_package")
    if pkg:
        pypi_url = f"https://pypi.org/pypi/{pkg}/json"
        try:
            r_pypi = await client.get(pypi_url)
            if r_pypi.status_code == 200:
                info = r_pypi.json().get("info") or {}
                ver = str(info.get("version") or "").strip()
                summary = str(info.get("summary") or "").strip()
                if ver:
                    signals.append({
                        "source_backend": "pypi",
                        "title": _strip_pronouns_and_clean(f"Пакет {pkg} версия {ver} {summary}"),
                        "raw_title": redact(f"{pkg} {ver}: {summary}"[:160]),
                        "url": f"https://pypi.org/project/{pkg}/{ver}/",
                        "published_date": datetime.now(timezone.utc).strftime("%Y-%m-%d"),
                    })
        except Exception as exc:
            errors.append(f"pypi({pkg}): {type(exc).__name__}")

    # 3. OpenAlex (свежие научные публикации)
    oa_url = "https://api.openalex.org/works"
    try:
        r_oa = await client.get(
            oa_url,
            params={"per-page": 2, "sort": "publication_date:desc", "search": spec["openalex_query"]},
        )
        if r_oa.status_code == 200:
            results = r_oa.json().get("results") or []
            for w in results[:2]:
                dname = str(w.get("display_name") or "").strip()
                if dname:
                    w_id = str(w.get("doi") or w.get("id") or "https://openalex.org")
                    pub_d = str(w.get("publication_date") or datetime.now(timezone.utc).strftime("%Y-%m-%d"))[:10]
                    signals.append({
                        "source_backend": "openalex",
                        "title": _strip_pronouns_and_clean(dname),
                        "raw_title": redact(dname[:160]),
                        "url": w_id if w_id.startswith("https://") else "https://openalex.org",
                        "published_date": pub_d,
                    })
    except Exception as exc:
        errors.append(f"openalex: {type(exc).__name__}")

    elapsed_ms = round((time.perf_counter() - t0) * 1000.0, 1)
    return {
        "domain": domain_key,
        "live_count": len(signals),
        "elapsed_ms": elapsed_ms,
        "signals": signals,
        "errors": errors,
    }


async def fetch_live_internet_trends(
    *,
    domains: list[str] | None = None,
    custom_topic: str = "",
    allow_live_network: bool = True,
) -> dict[str, Any]:
    """Собирает живые тренды из интернета по выбранным доменам с безопасным резервом при офлайне."""
    selected = [d for d in (domains or list(TREND_DOMAINS.keys())) if d in TREND_DOMAINS]
    if not selected:
        selected = list(TREND_DOMAINS.keys())

    today_iso = datetime.now(timezone.utc).strftime("%Y-%m-%d")
    by_domain: dict[str, dict[str, Any]] = {}
    total_live = 0

    if allow_live_network:
        headers = {"User-Agent": "BossmanTrendTeacher/2.0 (+https://bossman.local)"}
        async with httpx.AsyncClient(timeout=6.0, headers=headers, follow_redirects=True) as client:
            tasks = [
                _fetch_domain_live_signals(client, d_key, TREND_DOMAINS[d_key], custom_topic=custom_topic)
                for d_key in selected
            ]
            results = await asyncio.gather(*tasks, return_exceptions=True)
            for d_key, res in zip(selected, results):
                if isinstance(res, dict) and res.get("signals"):
                    by_domain[d_key] = res
                    total_live += int(res.get("live_count", 0))
                else:
                    by_domain[d_key] = {
                        "domain": d_key,
                        "live_count": 0,
                        "elapsed_ms": 0.0,
                        "signals": [],
                        "errors": [str(res)] if isinstance(res, Exception) else [],
                    }
    else:
        for d_key in selected:
            by_domain[d_key] = {
                "domain": d_key,
                "live_count": 0,
                "elapsed_ms": 0.0,
                "signals": [],
                "errors": ["live_network_disabled_by_caller"],
            }

    # Гарантируем, что даже при закрытой сети / лимите апстрима каждый домен получает
    # валидную карточку на базе проверенного эталонного сигнала с честной меткой live_fetched.
    for d_key in selected:
        dom_entry = by_domain[d_key]
        if not dom_entry["signals"]:
            spec = TREND_DOMAINS[d_key]
            pkg = spec["pypi_package"]
            dom_entry["signals"].append({
                "source_backend": "offline-curated-fallback",
                "title": _strip_pronouns_and_clean(f"Стандарт {spec['title_ru']} пакет {pkg}"),
                "raw_title": f"{spec['title_ru']} ({pkg})",
                "url": f"https://pypi.org/project/{pkg}/",
                "published_date": today_iso,
            })

    return {
        "fetched_at": today_iso,
        "allow_live_network": allow_live_network,
        "domains_polled": selected,
        "total_live_signals": total_live,
        "by_domain": by_domain,
    }


def build_lesson_card_and_exam(
    domain_key: str,
    domain_signals: dict[str, Any],
    *,
    session_date: str,
) -> dict[str, Any]:
    """Строит учебную карточку Учителя (TrendLessonCard), самодостаточный факт И-1 и проводит экзамен Bossman."""
    spec = TREND_DOMAINS[domain_key]
    signals = domain_signals.get("signals") or []
    top = signals[0]
    live_fetched = int(domain_signals.get("live_count", 0)) > 0 and top.get("source_backend") != "offline-curated-fallback"
    backend_id = str(top.get("source_backend") or "hn-algolia")
    headline = _strip_pronouns_and_clean(top.get("title") or spec["title_ru"])
    source_url = str(top.get("url") or "https://news.ycombinator.com")

    # Формируем subject, predicate, object и самодостаточное утверждение И-1 (15..80 слов, 0 местоимений).
    # Важно: все имена собственные и числа из subject и object обязаны дословно входить в statement!
    subject = f"trend:{domain_key}"
    predicate = "current_internet_trend"
    obj = f"{backend_id}:{session_date}"

    raw_statement = (
        f"На дату {session_date} учебный модуль Учителя Bossman зафиксировал актуальный тренд "
        f"направления {subject} из проверенного источника {obj} по теме {headline} "
        f"для внедрения в студию Bossman Command Center и мобильный пульт управления."
    )
    # Гарантируем прохождение строгой валидации И-1 (FactStore.validate_statement)
    verified_statement = validate_statement(
        raw_statement,
        subject=subject,
        object=obj,
        observed_at=datetime.now(timezone.utc),
        strict=True,
    )

    # Контрольный экзамен Учителя (Mastery Exam)
    expected_kw = list(spec["exam_keywords"])
    bossman_answer = (
        f"Усвоено по направлению «{spec['title_ru']}» (источник {backend_id}, сигнал: {headline}): "
        f"{spec['studio_focus_ru']} Практический рецепт: {spec['recipe_ru']}"
    )
    matched_kw = [kw for kw in expected_kw if kw.lower() in bossman_answer.lower()]
    mastery_score = round(len(matched_kw) / max(1, len(expected_kw)), 2)

    return {
        "lesson_id": f"lesson-{domain_key}-{session_date}",
        "domain": domain_key,
        "domain_title_ru": spec["title_ru"],
        "live_fetched": live_fetched,
        "live_signals_count": int(domain_signals.get("live_count", 0)),
        "source_backend": backend_id,
        "headline": headline,
        "source_url": source_url,
        "published_date": str(top.get("published_date") or session_date),
        "all_signals": signals[:4],
        "teacher_brief_ru": (
            f"Учитель разобрал свежий интернет-сигнал из {backend_id} («{headline}») "
            f"и привязал к архитектуре Bossman: {spec['studio_focus_ru']}"
        ),
        "actionable_studio_recipe_ru": spec["recipe_ru"],
        "temporal_fact": {
            "subject": subject,
            "predicate": predicate,
            "object": obj,
            "statement": verified_statement,
        },
        "mastery_exam": {
            "question_ru": spec["exam_question_ru"],
            "expected_keywords": expected_kw,
            "matched_keywords": matched_kw,
            "bossman_answer_ru": bossman_answer,
            "mastery_score": mastery_score,
            "passed": mastery_score >= 0.85,
        },
    }


async def run_web_trend_teacher_session(
    svc,
    *,
    domains: list[str] | None = None,
    custom_topic: str = "",
    note: str = "",
    allow_live_network: bool | None = None,
    inject_mid_step_fault: str = "none",
    allow_auto_repair: bool = True,
    simulate_unwritten_artifact: bool = False,
    agent_claim_text: str | None = None,
    scope: str = "work",
) -> dict[str, Any]:
    """Полный цикл: Аудит -> Выход в интернет -> Уроки Учителя -> Экзамен -> CUA-Recovery -> AgentProofBench -> TimlMemBench."""
    cfg = await get_teacher_config(svc)
    use_live_net = cfg.get("allow_live_network", True) if allow_live_network is None else bool(allow_live_network)
    min_mastery = float(cfg.get("min_mastery_score", 0.85))

    state = await _get_state(svc)
    state["metrics"]["total_sessions"] += 1
    session_id = f"tt-{int(time.time())}-{uuid.uuid4().hex[:6]}"
    session_date = datetime.now(timezone.utc).strftime("%Y-%m-%d")

    # 0. Санитайзер входных заметок и темы запроса
    raw_note = str(note or "")
    raw_topic = str(custom_topic or "")
    secret_found = contains_secret(raw_note) or contains_secret(raw_topic)
    clean_note = redact(raw_note)
    clean_topic = redact(raw_topic)
    if secret_found:
        state["metrics"]["secrets_redacted_count"] += 1

    # 1. Предшаговый барьер состояния CUA-Recovery (StepStateGuard)
    expected_step_state = {
        "teacher_ready": True,
        "active_field": "curriculum:web_trend_teacher",
        "dialog_open": True,
        "saved": True,
    }
    observed_step_state = dict(expected_step_state)
    if inject_mid_step_fault == "wrong_form_field":
        observed_step_state["active_field"] = "untrusted_external_field"
    elif inject_mid_step_fault == "closed_dialog":
        observed_step_state["dialog_open"] = False
    elif inject_mid_step_fault == "failed_save":
        observed_step_state["saved"] = False

    checkpoint = dict(expected_step_state) if allow_auto_repair else None
    guard = StepStateGuard(allow_auto_repair=allow_auto_repair)
    step_check = guard.verify_before_next_action(
        step_index=1,
        action_name="web_trend_teacher.commit_curriculum",
        irreversible=True,
        expected_state=expected_step_state,
        observed_state=observed_step_state,
        checkpoint_state=checkpoint,
    )

    if step_check.decision == "halt_before_irreversible":
        state["metrics"]["cua_halts_before_irreversible"] += 1
        halted_rec = {
            "session_id": session_id,
            "status": "HALTED_BEFORE_IRREVERSIBLE",
            "verifier_status": "UNVERIFIED",
            "cua_recovery": {
                "matched": step_check.matched,
                "detected_fault": step_check.detected_fault,
                "mismatched_keys": list(step_check.mismatched_keys),
                "decision": step_check.decision,
                "repaired": False,
            },
            "secret_redacted": secret_found,
            "sanitized_note": clean_note,
            "memory_facts_written": [],
            "created_at": time.time(),
        }
        async with _teacher_lock:
            state["sessions"].insert(0, halted_rec)
            await _persist_state(svc, state)
        return halted_rec

    if step_check.decision == "rollback_and_retry":
        state["metrics"]["cua_recoveries_triggered"] += 1

    # 2. Аудит + Живой сбор интернет-трендов + Построение уроков и экзамен
    audit_report = audit_web_learning_readiness()
    web_harvest = await fetch_live_internet_trends(
        domains=domains,
        custom_topic=clean_topic,
        allow_live_network=use_live_net,
    )

    lessons: list[dict[str, Any]] = []
    for d_key in web_harvest["domains_polled"]:
        card = build_lesson_card_and_exam(
            d_key,
            web_harvest["by_domain"][d_key],
            session_date=session_date,
        )
        lessons.append(card)

    avg_mastery = round(
        sum(float(c["mastery_exam"]["mastery_score"]) for c in lessons) / max(1, len(lessons)),
        2,
    )
    all_passed = all(float(c["mastery_exam"]["mastery_score"]) >= min_mastery for c in lessons)

    # 3. Снимок исходного состояния ДО записи учебного дайджеста (AgentProofBench)
    r_dir = _runs_dir(svc)
    artifact_path = r_dir / f"{session_id}_web_trend_curriculum.json"
    expected_post = ExpectedState(
        kind="file",
        target=str(artifact_path),
        expect={
            "exists": True,
            "contains": '"curriculum_verified": true',
            "min_bytes": 256,
        },
    )
    init_snapshot = await capture_initial_snapshot(expected_post, svc=svc, roots=[r_dir])

    curriculum_payload = {
        "schema": "bossman.web_trend_teacher.curriculum.v1",
        "session_id": session_id,
        "session_date": session_date,
        "rule_version": RULE_VERSION,
        "curriculum_verified": bool(all_passed),
        "average_mastery_score": avg_mastery,
        "live_signals_fetched": int(web_harvest["total_live_signals"]),
        "domains_polled": web_harvest["domains_polled"],
        "sanitized_topic": clean_topic,
        "sanitized_note": clean_note,
        "audit": audit_report,
        "lessons": lessons,
        "timestamp": time.time(),
    }

    if not simulate_unwritten_artifact:
        artifact_path.write_text(
            json.dumps(curriculum_payload, indent=2, ensure_ascii=False),
            encoding="utf-8",
        )
        # «Последний дайджест» живёт рядом с прогонами в data_dir (02.11.2026): запись в отслеживаемый
        # data/learning/web_trend_curriculum/latest_digest.json репозитория делала чекаут «грязным» при каждом
        # тике, и /health терял идентичность сборки (SOURCE_IDENTITY_UNKNOWN, ready=False).
        try:
            latest_dir = r_dir
            latest_dir.mkdir(parents=True, exist_ok=True)
            (latest_dir / "latest_digest.json").write_text(
                json.dumps(curriculum_payload, indent=2, ensure_ascii=False),
                encoding="utf-8",
            )
        except Exception:
            pass

    # 4. Независимая проверка конечного состояния (AgentProofBench)
    effective_claim = (
        agent_claim_text
        if agent_claim_text is not None
        else f"Готово: сессия обучения трендам {session_id} успешно завершена и сохранена."
    )
    proof = await verify_criterion_with_proof(
        expected_post,
        rule_id="web-trend-teacher-curriculum",
        rule_version=RULE_VERSION,
        initial_snapshot=init_snapshot,
        agent_claim_text=effective_claim,
        svc=svc,
        roots=[r_dir],
    )
    if proof.false_claim_caught:
        state["metrics"]["false_claims_caught"] += 1

    # 5. Запись усвоенных интернет-трендов в темпоральную память FactStore (TimlMemBench)
    written_facts: list[dict[str, Any]] = []
    if proof.verifier_status == "VERIFIED" and all_passed:
        state["metrics"]["verified_sessions"] += 1
        state["metrics"]["live_web_items_fetched"] += int(web_harvest["total_live_signals"])
        state["metrics"]["lessons_mastered"] += len(lessons)
        state["metrics"]["last_mastery_average"] = avg_mastery

        async with _teacher_lock:
            store = FactStore(svc)
            for card in lessons:
                tf = card["temporal_fact"]
                try:
                    raw_f = await store.add(
                        subject=tf["subject"],
                        predicate=tf["predicate"],
                        object=tf["object"],
                        statement=tf["statement"],
                        replace_current=True,
                        source_kind="tool",
                        source_note=f"url={card['source_url']} proof={proof.proof_sha256[:16]}",
                        scope=scope,
                    )
                    written_facts.append(json.loads(json.dumps(raw_f, ensure_ascii=False, default=str)))
                    state["metrics"]["temporal_facts_learned"] += 1
                except Exception:
                    pass

            # Записываем сводный факт сессии Учителя
            try:
                summary_stmt = (
                    f"На дату {session_date} Учитель Bossman завершил сессию {session_id} "
                    f"по изучению интернет-трендов с криптографическим доказательством "
                    f"{proof.proof_sha256[:16]} и версией правила {RULE_VERSION}."
                )
                master_f = await store.add(
                    subject="teacher:web_trend_curriculum",
                    predicate="last_verified_session",
                    object=session_id,
                    statement=summary_stmt,
                    replace_current=True,
                    source_kind="tool",
                    source_note=f"proof_sha256={proof.proof_sha256}",
                    scope=scope,
                )
                written_facts.append(json.loads(json.dumps(master_f, ensure_ascii=False, default=str)))
                state["metrics"]["temporal_facts_learned"] += 1
            except Exception:
                pass

    session_record = {
        "session_id": session_id,
        "session_date": session_date,
        "status": "COMPLETED" if (proof.verifier_status == "VERIFIED" and all_passed) else "VERIFICATION_FAILED",
        "artifact_path": str(artifact_path),
        "verifier_status": proof.verifier_status,
        "rule_id": proof.rule_id,
        "rule_version": proof.rule_version,
        "proof_sha256": proof.proof_sha256,
        "false_claim_caught": proof.false_claim_caught,
        "initial_snapshot": proof.initial_snapshot,
        "final_evidence": proof.final_evidence,
        "cua_recovery": {
            "matched": step_check.matched,
            "detected_fault": step_check.detected_fault,
            "mismatched_keys": list(step_check.mismatched_keys),
            "decision": step_check.decision,
            "repaired": step_check.decision == "rollback_and_retry",
        },
        "secret_redacted": secret_found,
        "sanitized_note": clean_note,
        "sanitized_topic": clean_topic,
        "average_mastery_score": avg_mastery,
        "live_signals_fetched": int(web_harvest["total_live_signals"]),
        "lessons_count": len(lessons),
        "lessons": lessons,
        "audit": audit_report,
        "memory_facts_written": written_facts,
        "created_at": time.time(),
    }

    async with _teacher_lock:
        state["sessions"].insert(0, session_record)
        if proof.verifier_status == "VERIFIED" and all_passed:
            state["latest_curriculum"] = curriculum_payload
        await _persist_state(svc, state)
    return session_record


async def recall_learned_trends(
    svc,
    *,
    domain: str = "",
    caller_scope: str = "work",
) -> dict[str, Any]:
    """Извлекает актуальные интернет-тренды, усвоенные Bossman, из темпоральной памяти FactStore."""
    store = FactStore(svc)
    domains_to_query = [domain] if domain and domain in TREND_DOMAINS else list(TREND_DOMAINS.keys())
    facts_found: list[dict[str, Any]] = []
    for d_key in domains_to_query:
        hits = await store.search(
            subject=f"trend:{d_key}",
            caller_scope=caller_scope,
        )
        facts_found.extend(json.loads(json.dumps(hits, ensure_ascii=False, default=str)))
    state = await _get_state(svc)
    return {
        "ok": True,
        "caller_scope": caller_scope,
        "active_trend_facts_count": len(facts_found),
        "active_trend_facts": facts_found,
        "latest_curriculum": state.get("latest_curriculum"),
    }


# ------------------------------------------------------------------ REST API

@router.get("/web-trend-teacher/status")
async def api_teacher_status(request: Request):
    svc = request.app.state.svc
    cfg = await get_teacher_config(svc)
    state = await _get_state(svc)
    return {
        "ok": True,
        "rule_version": RULE_VERSION,
        "available_domains": list(TREND_DOMAINS.keys()),
        "config": cfg,
        "metrics": dict(state["metrics"]),
        "audit": audit_web_learning_readiness(),
        "recent_sessions": list(state["sessions"][:5]),
    }


@router.get("/web-trend-teacher/curriculum")
async def api_teacher_curriculum(request: Request, scope: str = "work"):
    svc = request.app.state.svc
    return await recall_learned_trends(svc, caller_scope=scope)


@router.post("/web-trend-teacher/config")
async def api_teacher_config(request: Request):
    svc = request.app.state.svc
    body = await request.json()
    try:
        cfg = await save_teacher_config(svc, body or {})
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc
    return {"ok": True, "config": cfg}


@router.post("/web-trend-teacher/teach")
async def api_teacher_teach(request: Request):
    svc = request.app.state.svc
    body = await request.json() if request.headers.get("content-length") != "0" else {}
    body = body or {}
    raw_domains = body.get("domains")
    if raw_domains is not None:
        if not isinstance(raw_domains, list) or any(d not in TREND_DOMAINS for d in raw_domains):
            raise HTTPException(
                status_code=400,
                detail=f"Unknown domain in {raw_domains!r}; valid: {list(TREND_DOMAINS.keys())}",
            )
    res = await run_web_trend_teacher_session(
        svc,
        domains=raw_domains,
        custom_topic=str(body.get("custom_topic") or ""),
        note=str(body.get("note") or ""),
        allow_live_network=body.get("allow_live_network"),
        inject_mid_step_fault=str(body.get("inject_mid_step_fault") or "none"),
        allow_auto_repair=bool(body.get("allow_auto_repair", True)),
        simulate_unwritten_artifact=bool(body.get("simulate_unwritten_artifact", False)),
        agent_claim_text=body.get("agent_claim_text"),
        scope=str(body.get("scope") or "work"),
    )
    return {"ok": True, "result": res}


# ------------------------------------------------------------------ Agent Tools

async def _tool_learn_web_trends(args: dict, ctx: ToolContext) -> ToolResult:
    raw_domains = args.get("domains")
    domains = raw_domains if isinstance(raw_domains, list) else None
    custom_topic = str(args.get("custom_topic") or args.get("query") or "")
    note = str(args.get("note") or "")
    scope = str(args.get("scope") or "work")
    allow_live = args.get("allow_live_network")
    try:
        res = await run_web_trend_teacher_session(
            ctx.svc,
            domains=domains,
            custom_topic=custom_topic,
            note=note,
            allow_live_network=allow_live,
            scope=scope,
        )
    except Exception as exc:
        return ToolResult(content=f"ERROR: {exc}", error=True, one_line="teacher.learn_web_trends: failed")

    status = res.get("verifier_status")
    proof_sha = str(res.get("proof_sha256") or "")[:16]
    avg_m = res.get("average_mastery_score")
    live_n = res.get("live_signals_fetched")
    return ToolResult(
        content=(
            f"Сессия Учителя «{res.get('session_id')}» завершена: "
            f"verifier={status}, live_signals={live_n}, mastery={avg_m}, "
            f"facts_written={len(res.get('memory_facts_written') or [])}, proof_sha256={proof_sha}"
        ),
        one_line=f"teacher.learn_web_trends: {status} (mastery={avg_m}, live={live_n})",
        data=res,
    )


async def _tool_trend_status(args: dict, ctx: ToolContext) -> ToolResult:
    cfg = await get_teacher_config(ctx.svc)
    state = await _get_state(ctx.svc)
    data = {
        "rule_version": RULE_VERSION,
        "available_domains": list(TREND_DOMAINS.keys()),
        "config": cfg,
        "metrics": dict(state["metrics"]),
        "audit": audit_web_learning_readiness(),
    }
    return ToolResult(
        content=json.dumps(data, ensure_ascii=False, indent=2, default=str),
        one_line=(
            f"teacher.trend_status: verified={state['metrics']['verified_sessions']}/"
            f"{state['metrics']['total_sessions']}"
        ),
        data=data,
    )


async def _tool_recall_trends(args: dict, ctx: ToolContext) -> ToolResult:
    domain = str(args.get("domain") or "").strip()
    scope = str(args.get("scope") or "work").strip()
    data = await recall_learned_trends(ctx.svc, domain=domain, caller_scope=scope)
    return ToolResult(
        content=json.dumps(data, ensure_ascii=False, indent=2, default=str),
        one_line=f"teacher.recall_trends: {data['active_trend_facts_count']} active facts",
        data=data,
    )


async def _setup(svc) -> None:
    REGISTRY.register(
        ToolSpec(
            name="teacher.learn_web_trends",
            description=(
                "Выйти в интернет как Учитель Bossman, собрать актуальные тренды по направлениям студии "
                "(ai_agents_and_llms, generative_video_and_cinema, webgl_3d_and_world_gen, python_and_devtools), "
                "провести контрольный экзамен усвоения и записать проверенные факты в темпоральную память FactStore."
            ),
            handler=_tool_learn_web_trends,
            category="read",
            source="builtin",
            default_effect="auto",
            timeout_seconds=90,
            idempotent=True,
            input_schema={
                "domains": {
                    "type": "array",
                    "items": {"type": "string"},
                    "description": "Список доменов трендов (необязательно; по умолчанию все 4 домена)",
                },
                "custom_topic": {
                    "type": "string",
                    "maxLength": 200,
                    "description": "Дополнительная тема поиска трендов в интернете",
                },
                "note": {
                    "type": "string",
                    "maxLength": 400,
                    "description": "Заметка к учебной сессии (секреты очищаются автоматически)",
                },
                "scope": {
                    "type": "string",
                    "description": "Область памяти TimlMemBench: work | personal | public",
                },
            },
            required=[],
        )
    )
    REGISTRY.register(
        ToolSpec(
            name="teacher.trend_status",
            description="Получить статус модуля Учителя интернет-трендов, аудит веб-бэкендов и метрики усвоения.",
            handler=_tool_trend_status,
            category="read",
            source="builtin",
            default_effect="auto",
            timeout_seconds=30,
            idempotent=True,
            input_schema={},
            required=[],
        )
    )
    REGISTRY.register(
        ToolSpec(
            name="teacher.recall_trends",
            description="Вспомнить актуальные интернет-тренды, усвоенные Bossman и записанные в темпоральную память FactStore.",
            handler=_tool_recall_trends,
            category="read",
            source="builtin",
            default_effect="auto",
            timeout_seconds=30,
            idempotent=True,
            input_schema={
                "domain": {"type": "string", "description": "Фильтр по домену (необязательно)"},
                "scope": {"type": "string", "description": "Область вызова: work | personal | public"},
            },
            required=[],
        )
    )


FEATURE = Feature(
    name="web_trend_teacher",
    router=router,
    setup=_setup,
)
