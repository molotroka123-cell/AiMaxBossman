"""V2.6 — реальные замеры на этом хосте. Никаких выдуманных чисел:
всё, что нельзя измерить без owner hardware/моделей, помечается N/A.
"""
from __future__ import annotations

import gc
import json
import os
import statistics
import sys
import time
import tracemalloc
from pathlib import Path

import psutil

PROC = psutil.Process()
OUT: dict = {}


def rss_mb() -> float:
    return PROC.memory_info().rss / 1024 / 1024


def bench(fn, n=200, warmup=20):
    for _ in range(warmup):
        fn()
    s = []
    for _ in range(n):
        t0 = time.perf_counter()
        fn()
        s.append((time.perf_counter() - t0) * 1000)
    s.sort()
    return {"n": n, "p50": round(statistics.median(s), 4),
            "p95": round(s[int(n * 0.95) - 1], 4),
            "mean": round(statistics.mean(s), 4)}


# ---------- 1. RSS: базовый интерпретатор -> импорт ядра -> импорт V2.6 ----------
OUT["rss_python_baseline_mb"] = round(rss_mb(), 1)

import bossman  # noqa: E402
from bossman import db, runner  # noqa: E402,F401
from bossman.agents import load_all  # noqa: E402
from bossman.context import ContextBudget, ContextBuilder  # noqa: E402
gc.collect()
OUT["rss_after_core_import_mb"] = round(rss_mb(), 1)

from bossman import (capabilities, compute_budget, counterfactual,  # noqa: E402
                     evidence_graph, exec_cache, failure_patterns, file_intel,
                     flight_recorder, personal_context, signals, task_compiler,
                     uncertainty, voice_capability)
from bossman.research import engine as research_engine  # noqa: E402,F401
gc.collect()
OUT["rss_after_v26_import_mb"] = round(rss_mb(), 1)
OUT["v26_import_cost_mb"] = round(OUT["rss_after_v26_import_mb"]
                                  - OUT["rss_after_core_import_mb"], 2)

# ---------- 2. Контроллеры V2.6: латентность ----------
agents = load_all()
TXT_SIMPLE = "посчитай 2+2"
TXT_COMPLEX = "исследуй конкурентов, затем создай таблицу-отчёт и отправь сводку"

OUT["derive_signals_ms"] = bench(lambda: signals.derive_signals(TXT_COMPLEX), n=2000)
_sig = signals.derive_signals(TXT_COMPLEX)
OUT["uncertainty_estimate_ms"] = bench(
    lambda: uncertainty.estimate(risk=0.3, failure_history=0.2, evidence_gap=0.4), n=2000)
OUT["select_level_ms"] = bench(lambda: compute_budget.select_level(_sig), n=2000)
OUT["counterfactual_ms"] = bench(
    lambda: counterfactual.critical_assumptions("browser.confirmed_click", {}), n=2000)

# полный «контроллерный стек» на одну задачу
def full_controller():
    s = signals.derive_signals(TXT_SIMPLE)
    u = uncertainty.estimate(risk=s.risk)
    s = s.with_(uncertainty=u.score)
    compute_budget.select_level(s)

OUT["controller_stack_per_task_ms"] = bench(full_controller, n=2000)

# ---------- 3. Fast path: оркестрация задачи ----------
def fastpath():
    a = runner.pick_agent(agents, TXT_SIMPLE)
    b = ContextBuilder(ContextBudget(window=8192), runner._system_prompt(a))
    runner._tool_schemas(a)
    b.block_tokens(TXT_SIMPLE)
    b.build(TXT_SIMPLE)

OUT["fastpath_orchestration_ms"] = bench(fastpath, n=500)

# ---------- 4. Execution cache: реальный hit-rate и выигрыш ----------
from bossman.config import settings  # noqa: E402
from bossman.llm import real_window  # noqa: E402

cache = exec_cache.get_cache()
cache.hits = cache.misses = 0
OUT["real_window_cold_ms"] = bench(lambda: real_window("bossman-coder"), n=1, warmup=0)
OUT["real_window_warm_ms"] = bench(lambda: real_window("bossman-coder"), n=2000)
stats = cache.stats()
total = stats["hits"] + stats["misses"]
OUT["exec_cache"] = {**stats,
                     "hit_rate": round(stats["hits"] / total, 4) if total else None}

# честный холодный замер: разбор YAML без кэша
import yaml  # noqa: E402
def raw_yaml():
    yaml.safe_load(settings.tools_registry.read_text(encoding="utf-8"))

OUT["registry_yaml_parse_uncached_ms"] = bench(raw_yaml, n=200)

# ---------- 5. Personal Context Router: токены RAW vs selected ----------
mem_samples = []
for agent in agents.values():
    if agent.memory.strip():
        mem_samples.append((agent.name, agent.memory))
pc_rows = []
for name, mem in mem_samples:
    critical, st = personal_context.select_memory(mem)
    rendered = personal_context.render_selected(critical)
    # тот же счёт токенов, что использует ядро
    from bossman.context import estimate_tokens as count_tokens
    raw_t = count_tokens(mem)
    sel_t = count_tokens(rendered)
    pc_rows.append({"agent": name, "raw_tokens": raw_t, "selected_tokens": sel_t,
                    "saved_pct": round(100 * (1 - sel_t / raw_t), 1) if raw_t else 0,
                    "total_lines": st["total_lines"], "kept_lines": st["kept_lines"]})
OUT["personal_context"] = pc_rows

# ---------- 6. File Intelligence: латентность парсинга по форматам ----------
import csv as _csv  # noqa: E402
import io  # noqa: E402
import zipfile  # noqa: E402

tmp = Path("/tmp/v26_measure")
tmp.mkdir(exist_ok=True)
# CSV 1000 строк
csv_p = tmp / "big.csv"
with csv_p.open("w", encoding="utf-8", newline="") as f:
    w = _csv.writer(f)
    w.writerow(["id", "name", "value"])
    for i in range(1000):
        w.writerow([i, f"item-{i}", i * 3.14])
# XLSX минимальный
xlsx_p = tmp / "s.xlsx"
with zipfile.ZipFile(xlsx_p, "w") as z:
    z.writestr("xl/workbook.xml",
               '<workbook xmlns="http://schemas.openxmlformats.org/spreadsheetml/2006/main">'
               '<sheets><sheet name="Данные" sheetId="1"/></sheets></workbook>')
    rows = "".join(
        f'<row r="{i}"><c r="A{i}"><v>{i}</v></c><c r="B{i}"><v>{i*2}</v></c></row>'
        for i in range(1, 201))
    z.writestr("xl/worksheets/sheet1.xml",
               f'<worksheet xmlns="http://schemas.openxmlformats.org/spreadsheetml/2006/main">'
               f'<sheetData>{rows}</sheetData></worksheet>')
md_p = tmp / "doc.md"
md_p.write_text("\n".join(f"## Раздел {i}\nтекст раздела {i}" for i in range(100)),
                encoding="utf-8")

parse_rows = {}
for label, p in (("csv_1000_rows", csv_p), ("xlsx_200_rows", xlsx_p), ("md_100_sections", md_p)):
    cache.invalidate("parsed_file:")
    cold = bench(lambda p=p: (cache.invalidate("parsed_file:"), file_intel.parse_file(p)),
                 n=20, warmup=2)
    warm = bench(lambda p=p: file_intel.parse_file(p), n=200)
    art = file_intel.parse_file(p)
    parse_rows[label] = {"cold_ms": cold["p50"], "cached_ms": warm["p50"],
                         "sections": len(art.sections), "bytes": p.stat().st_size}
OUT["file_parse"] = parse_rows

# ---------- 7. Пик RSS под нагрузкой парсинга ----------
tracemalloc.start()
before = rss_mb()
for _ in range(50):
    cache.invalidate("parsed_file:")
    file_intel.parse_file(csv_p)
peak_py = tracemalloc.get_traced_memory()[1] / 1024 / 1024
tracemalloc.stop()
OUT["parse_workload_rss_delta_mb"] = round(rss_mb() - before, 2)
OUT["parse_workload_python_peak_mb"] = round(peak_py, 2)
OUT["rss_final_mb"] = round(rss_mb(), 1)

# ---------- 8. Research engine: латентность на фиктивных источниках ----------
import asyncio  # noqa: E402

from bossman.research.models import QUICK as MODE_QUICK, Source  # noqa: E402

DOCS = {
    "s1": "Сервис X поддерживает офлайн режим. Цена лицензии 100 долларов в год.",
    "s2": "Сервис X не поддерживает офлайн режим. Лицензия стоит 120 долларов.",
    "s3": "Сервис X имеет офлайн режим и стоит 100 долларов ежегодно.",
}


async def fake_fetch(src):
    return DOCS[src.url_or_ref]


def run_research():
    eng = research_engine.ResearchEngine(fake_fetch)
    srcs = [Source(url_or_ref=k, kind="web", trust=0.8) for k in DOCS]
    return asyncio.run(eng.run("поддерживает ли сервис X офлайн режим и сколько стоит",
                               srcs, MODE_QUICK))

rep = run_research()
OUT["research_quick_ms"] = bench(run_research, n=30, warmup=3)
OUT["research_report"] = {"claims": len(rep.claims), "sources": len(rep.sources),
                          "contradictions": len(rep.contradictions),
                          "rounds_used": rep.rounds_used}

# ---------- 9. Voice probe (реальная доступность на хосте) ----------
vc = voice_capability.probe()
OUT["voice"] = {"stt_available": vc.stt_available, "tts_available": vc.tts_available,
                "stt_provider": vc.stt_provider, "tts_provider": vc.tts_provider}

# ---------- 10. Что измерить нельзя ----------
OUT["not_measurable_here"] = {
    "VRAM": "GPU отсутствует на хосте (nvidia-smi нет) — не 'не измерено', а физически нет",
    "VerifiedSuccess A/B": "нет локальных/облачных моделей (config/gateway.yaml отсутствует, "
                           "в репо только example с REPLACE_WITH_*)",
    "IntelligenceRetention": "то же: требует same-model прогона Raw vs Model+Bossman",
}

print(json.dumps(OUT, ensure_ascii=False, indent=1))
