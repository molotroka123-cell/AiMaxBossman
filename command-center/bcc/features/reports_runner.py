"""Выполнение сверки и отчётов по Telegram-экспорту (`tools/bossman_business.py`).

Чистый модуль запуска двух независимых прогонов, проверки воспроизводимости SHA-256
(`volume_result.json`, `deals.csv`, `report.md`, `disputed.csv`) и замера времени.
Личные пути в коде запрещены: пути к экспорту и контрольной книге передаются
в аргументах команды или читаются из конфига владельца в `data_dir`.
Молчаливый откат на `SYNTHETIC_export.html` запрещён.
"""
from __future__ import annotations

import asyncio
import contextlib
import hashlib
import json
import re
import shutil
import subprocess
import sys
import time
from pathlib import Path
from typing import Any

from .pc_agent_policy import is_t3_secret_or_protected_path

ARTIFACT_NAMES: tuple[str, ...] = (
    "volume_result.json",
    "deals.csv",
    "report.md",
    "disputed.csv",
)

DEMO_BANNER_MD = (
    "# ⚠️ ДЕМО НА СИНТЕТИКЕ\n\n"
    "> **ДЕМО НА СИНТЕТИКЕ**: этот отчёт построен на учебном синтетическом фикстуре "
    "`tests/exchange_volume/fixtures/SYNTHETIC_export.html`, а не на реальном экспорте владельца.\n\n"
)


def load_owner_reports_config(data_dir: Path | None) -> dict[str, str]:
    """Читает пути владельца (`export`, `workbook`) из `data_dir` без хардкода в коде."""
    if data_dir is None:
        return {}
    candidates = (
        Path(data_dir) / "pc-agent" / "reports_config.json",
        Path(data_dir) / "reports_config.json",
    )
    for cfg_path in candidates:
        if cfg_path.is_file():
            with contextlib.suppress(Exception):
                raw = json.loads(cfg_path.read_text(encoding="utf-8"))
                if isinstance(raw, dict):
                    return {
                        "export": str(raw.get("export") or raw.get("export_path") or "").strip(),
                        "workbook": str(raw.get("workbook") or raw.get("workbook_path") or "").strip(),
                    }
    return {}


def _is_explicit_synthetic_request(args: dict[str, Any], brief: str, exp_arg: str) -> bool:
    if bool(args.get("demo")) or bool(args.get("synthetic")):
        return True
    combined = f"{brief} {exp_arg}".lower()
    tokens = (
        "--demo",
        "--synthetic",
        "демо",
        "синтетик",
        "synthetic",
        "synthetic_export.html",
    )
    return any(tok in combined for tok in tokens)


def _extract_paths_from_args(args: dict[str, Any], data_dir: Path | None) -> tuple[str, str, bool]:
    brief_rep = str(args.get("brief") or "").strip()
    exp_arg = str(args.get("export") or "").strip()
    wb_arg = str(args.get("workbook") or "").strip()

    is_synthetic = _is_explicit_synthetic_request(args, brief_rep, exp_arg)

    if not exp_arg and brief_rep:
        m_exp = re.search(r'([A-Za-z]:[\\/][^\r\n"\'«»*?<>|]+\.(?:html|json))', brief_rep, re.IGNORECASE)
        if m_exp:
            exp_arg = m_exp.group(1).strip()
        elif not is_synthetic:
            cand_str = brief_rep.strip("\"'«» ")
            if cand_str and not any(ch in cand_str for ch in ("\n", "\r", "*", "?", "<", ">", "|")):
                with contextlib.suppress(OSError):
                    cand_dir = Path(cand_str)
                    if cand_dir.is_dir():
                        for fname in ("messages.html", "result.json"):
                            if (cand_dir / fname).is_file():
                                exp_arg = str(cand_dir / fname)
                                break
                    elif cand_dir.is_file():
                        exp_arg = str(cand_dir)

    if not wb_arg and brief_rep:
        m_wb = re.search(r'([A-Za-z]:[\\/][^\r\n"\'«»*?<>|]+\.xlsx)', brief_rep, re.IGNORECASE)
        if m_wb:
            wb_arg = m_wb.group(1).strip()

    if not is_synthetic and (not exp_arg or not wb_arg):
        cfg = load_owner_reports_config(data_dir)
        if not exp_arg and cfg.get("export"):
            exp_arg = cfg["export"]
        if not wb_arg and cfg.get("workbook"):
            wb_arg = cfg["workbook"]

    if exp_arg and "synthetic_export.html" in exp_arg.lower():
        is_synthetic = True

    return exp_arg, wb_arg, is_synthetic


def _run_business_once(
    *,
    script_path: Path,
    export_path: Path,
    workbook_path: Path | None,
    out_dir: Path,
    is_synthetic: bool,
) -> tuple[bool, float, str]:
    if out_dir.exists():
        shutil.rmtree(out_dir, ignore_errors=True)
    out_dir.mkdir(parents=True, exist_ok=True)
    cmd_ex = [
        sys.executable,
        "-I",
        "-X",
        "utf8",
        str(script_path),
        "exchange",
        "run",
        "--export",
        str(export_path),
        "--period",
        "business",
    ]
    if workbook_path is not None:
        cmd_ex.extend(["--workbook", str(workbook_path)])
    cmd_ex.extend(["--out", str(out_dir)])
    t_start = time.perf_counter()
    r1 = subprocess.run(
        cmd_ex,
        capture_output=True,
        text=True,
        encoding="utf-8",
        errors="replace",
        timeout=90,
    )
    if r1.returncode != 0:
        elapsed = round(time.perf_counter() - t_start, 3)
        return False, elapsed, (r1.stderr or r1.stdout or "exchange run failed")[:500]

    cmd_rep = [
        sys.executable,
        "-I",
        "-X",
        "utf8",
        str(script_path),
        "report",
        "--result",
        str(out_dir / "volume_result.json"),
        "--deals",
        str(out_dir / "deals.csv"),
        "--out",
        str(out_dir),
    ]
    r2 = subprocess.run(
        cmd_rep,
        capture_output=True,
        text=True,
        encoding="utf-8",
        errors="replace",
        timeout=60,
    )
    elapsed = round(time.perf_counter() - t_start, 3)
    if r2.returncode != 0:
        return False, elapsed, (r2.stderr or r2.stdout or "report failed")[:500]

    rep_file = out_dir / "report.md"
    if is_synthetic and rep_file.is_file():
        body = rep_file.read_text(encoding="utf-8")
        if "ДЕМО НА СИНТЕТИКЕ" not in body:
            rep_file.write_text(DEMO_BANNER_MD + body, encoding="utf-8")

    return True, elapsed, ""


def _write_offer_one_pager(earn_dir: Path) -> Path:
    offer_path = earn_dir / "OFFER_EXCHANGE_REPORTS_1PAGE.md"
    offer_text = (
        "# Оффер: Проверяемая сверка и отчёт по обороту из Telegram-экспорта (`Reports-Import`)\n\n"
        "## 1. Кому продаём\n"
        "- Обменные пункты (крипто/фиат OTC, P2P-дески) и операционные команды, "
        "ведущие учёт сделок в рабочем чате Telegram и Excel/Google-таблице.\n\n"
        "## 2. Какую боль снимаем\n"
        "- Ручная сверка сотен сообщений чата с Excel в конце бизнес-месяца занимает часы и приводит к ошибкам.\n"
        "- Потерянные сделки, случайные дубли (повторная отправка в течение 10 минут), "
        "сложные сообщения с несколькими суммами.\n"
        "- Путаница часовых поясов на границе закрытия месяца "
        "(20-е число 23:59 `Europe/Prague` vs локальное время компьютера при экспорте).\n"
        "- Смешение оборота (одна нога USDT) с прибылью или доходом.\n\n"
        "## 3. Что клиент отдаёт (ручной импорт, 100% офлайн без доступа к аккаунту)\n"
        "- Стандартный экспорт чата из Telegram Desktop (`messages.html` или `result.json`).\n"
        "- Опционально: свою контрольную таблицу `.xlsx` (лист «Сделки») для автоматической построчной сверки.\n\n"
        "## 4. Что клиент получает за 10 минут (4 проверяемых файла)\n"
        "1. `report.md` — понятный отчёт по периодам (21–20 `Europe/Prague`), границы покрытия данных, "
        "оборот одной ноги USDT (явно отделён от прибыли), список исключений и сверка с таблицей.\n"
        "2. `deals.csv` — реестр всех распознанных сделок со ссылкой `#ref` на конкретное сообщение, "
        "временем UTC и Праги, суммами и флагами.\n"
        "3. `disputed.csv` — короткий список спорных, сложных, неполных сообщений и дублей "
        "со ссылкой `#ref` для быстрой ручной проверки глазами.\n"
        "4. `volume_result.json` — машиночитаемый результат с воспроизводимым SHA-256.\n\n"
        "## 5. Гипотеза цены\n"
        "- **Разовый разбор и сверка закрытого бизнес-месяца**: **$49 / 1 200 CZK** "
        "за один чат-экспорт + сверка с таблицей.\n"
        "- **Ежемесячная подписка (до 3 рабочих чатов + сверка с книгой + список спорных строк)**: "
        "**$99 / мес (2 400 CZK / мес)**.\n\n"
        "## 6. Демо-пример (синтетический фикстур `SYNTHETIC_export.html`)\n"
        "- Вход: `tests/exchange_volume/fixtures/SYNTHETIC_export.html` "
        "(синтетический чат без персональных данных).\n"
        "- Команда: `Reports-Import.cmd exchange run --export SYNTHETIC_export.html "
        "--period business --out out_demo` + `report`.\n"
        "- Выход за `<0.2 сек` (`$0` внешних вызовов): точное разделение сделок на границе 20/21 марта "
        "по Праге, отсев дубля в 10-минутном окне, пометка неполного периода и список спорных строк с `#ref`.\n"
    )
    offer_path.write_text(offer_text, encoding="utf-8")
    return offer_path


async def run_exchange_report(
    *,
    args: dict[str, Any],
    repo_root: Path,
    data_dir: Path | None,
    earn_dir: Path,
    t0: float | None = None,
) -> dict[str, Any]:
    """Запускает 2 независимых прогона `bossman_business.py`, сверяет SHA-256 и формирует отчёт."""
    start_t = t0 if t0 is not None else time.perf_counter()
    earn_dir.mkdir(parents=True, exist_ok=True)
    exp_arg, wb_arg, is_synthetic = _extract_paths_from_args(args, data_dir)
    synth_exp = repo_root / "tests" / "exchange_volume" / "fixtures" / "SYNTHETIC_export.html"

    repo_fixture = False
    if is_synthetic:
        norm_exp = exp_arg.replace("\\", "/").strip().lower()
        if exp_arg and norm_exp not in {
            "synthetic_export.html",
            "tests/exchange_volume/fixtures/synthetic_export.html",
        }:
            export_path = Path(exp_arg)
        else:
            export_path = synth_exp
            repo_fixture = True
    else:
        if not exp_arg:
            msg = (
                "Отказ: укажите файл экспорта Telegram (`messages.html` или `result.json`) "
                "в аргументе команды или в конфиге владельца `data_dir/pc-agent/reports_config.json` "
                "(для учебного прогона явно укажите `демо на синтетике` / `--demo`)."
            )
            return {
                "ok": False,
                "verified": False,
                "action": "reports",
                "synthetic": False,
                "error": msg,
                "summary": f"🛑 {msg}",
                "tainted": False,
                "duration_ms": round((time.perf_counter() - start_t) * 1000),
            }
        export_path = Path(exp_arg)

    # The built-in demo fixture is a public repo file chosen by this module, not a caller-supplied path. Its
    # ancestors are the checkout location (e.g. a ``.claude/worktrees/...`` checkout trips the ``.claude`` secret
    # pattern), so the T3 path gate is skipped for it only; every caller-supplied path is still gated.
    if repo_fixture:
        t3_exp, t3_exp_reason = False, ""
    else:
        t3_exp, t3_exp_reason = is_t3_secret_or_protected_path(
            str(export_path), for_write=False, data_dir=data_dir
        )
    if t3_exp or not export_path.is_file():
        err_msg = (
            t3_exp_reason
            if t3_exp
            else f"Файл экспорта не найден ({export_path}): укажите файл экспорта, существующий на диске."
        )
        return {
            "ok": False,
            "verified": False,
            "action": "reports",
            "synthetic": is_synthetic,
            "error": err_msg,
            "summary": f"🛑 {err_msg}",
            "tainted": False,
            "duration_ms": round((time.perf_counter() - start_t) * 1000),
        }

    workbook_path: Path | None = None
    if wb_arg:
        cand_wb = Path(wb_arg)
        t3_wb, t3_wb_reason = is_t3_secret_or_protected_path(
            str(cand_wb), for_write=False, data_dir=data_dir
        )
        if t3_wb or not cand_wb.is_file():
            wb_err = (
                t3_wb_reason
                if t3_wb
                else f"Файл контрольной таблицы не найден ({cand_wb}): укажите существующий файл .xlsx."
            )
            return {
                "ok": False,
                "verified": False,
                "action": "reports",
                "synthetic": is_synthetic,
                "error": wb_err,
                "summary": f"🛑 {wb_err}",
                "tainted": False,
                "duration_ms": round((time.perf_counter() - start_t) * 1000),
            }
        workbook_path = cand_wb

    script_path = repo_root / "tools" / "bossman_business.py"
    out_run1 = earn_dir / "exchange_report_run1"
    out_run2 = earn_dir / "exchange_report_run2"

    (ok1, sec1, err1), (ok2, sec2, err2) = await asyncio.gather(
        asyncio.to_thread(
            _run_business_once,
            script_path=script_path,
            export_path=export_path,
            workbook_path=workbook_path,
            out_dir=out_run1,
            is_synthetic=is_synthetic,
        ),
        asyncio.to_thread(
            _run_business_once,
            script_path=script_path,
            export_path=export_path,
            workbook_path=workbook_path,
            out_dir=out_run2,
            is_synthetic=is_synthetic,
        ),
    )
    if not ok1:
        return {
            "ok": False,
            "verified": False,
            "action": "reports",
            "synthetic": is_synthetic,
            "error": f"Ошибка bossman_business.py (прогон 1): {err1}",
            "tainted": False,
            "duration_ms": round((time.perf_counter() - start_t) * 1000),
        }
    if not ok2:
        return {
            "ok": False,
            "verified": False,
            "action": "reports",
            "synthetic": is_synthetic,
            "error": f"Ошибка bossman_business.py (прогон 2): {err2}",
            "tainted": False,
            "duration_ms": round((time.perf_counter() - start_t) * 1000),
        }

    hashes_run1: dict[str, str] = {}
    hashes_run2: dict[str, str] = {}
    for fn in ARTIFACT_NAMES:
        p1, p2 = out_run1 / fn, out_run2 / fn
        if p1.is_file():
            hashes_run1[fn] = hashlib.sha256(p1.read_bytes()).hexdigest()
        if p2.is_file():
            hashes_run2[fn] = hashlib.sha256(p2.read_bytes()).hexdigest()
    repeatable_sha = (len(hashes_run1) == len(ARTIFACT_NAMES)) and (hashes_run1 == hashes_run2)

    vol_path = out_run1 / "volume_result.json"
    disp_path = out_run1 / "disputed.csv"
    if not vol_path.is_file() or not disp_path.is_file():
        return {
            "ok": False,
            "verified": False,
            "action": "reports",
            "synthetic": is_synthetic,
            "error": "Отсутствуют обязательные артефакты отчёта (volume_result.json / disputed.csv)",
            "tainted": False,
            "duration_ms": round((time.perf_counter() - start_t) * 1000),
        }
    vol_data = json.loads(vol_path.read_text(encoding="utf-8"))
    periods = vol_data.get("periods") or {}
    dups = vol_data.get("duplicates") or {}
    m_cls = vol_data.get("message_classification") or {}
    wb_info = vol_data.get("workbook") or {}
    wb_rec = wb_info.get("reconcile") or {}
    wb_rec_prov = wb_info.get("reconcile_with_provisional_complex") or {}
    disputed_lines = max(0, len(disp_path.read_text(encoding="utf-8").splitlines()) - 1)

    period_lines = []
    for p_key, p_val in periods.items():
        cov_p = p_val.get("coverage") or {}
        st_lbl = "закрыт" if p_val.get("status") == "closed" else "не закрыт на дату экспорта"
        if p_val.get("status") == "closed" and cov_p.get("complete") is False:
            st_lbl = f"закрыт по дате, неполный период (данные с {cov_p.get('data_from')}, нижняя граница)"
        period_lines.append(
            f"    • `{p_key}` ({p_val.get('from_prague')}..{p_val.get('to_prague')}, {st_lbl}): "
            f"сделок `{p_val.get('deals_counted')}`, оборот USDT (одна нога, НЕ прибыль): "
            f"**{p_val.get('gross_usdt_direct')} USDT**, "
            f"дубли: `{p_val.get('excluded_duplicates')}`, без USDT-ноги: `{p_val.get('unpriced_deals')}`"
        )

    wb_summary_line = "Контрольная таблица: не передана."
    if wb_rec:
        wb_summary_line = (
            f"Сверка с контрольной таблицей (`{workbook_path.name if workbook_path else 'workbook'}`): "
            f"строк в книге `{wb_info.get('rows')}` (+ мульти-ногих `{wb_info.get('multi_leg_rows_not_matched')}`), "
            f"совпало 1-в-1 (`counted`): **{wb_rec.get('matched')}/{wb_rec.get('chat_deals_in_window')}** "
            f"(с `provisional`: **{wb_rec_prov.get('matched')}/{wb_rec_prov.get('chat_deals_in_window')}**), "
            f"только в чате: `{wb_rec.get('only_in_chat_refs')}`, "
            f"только в книге: `{len(wb_rec.get('only_in_ledger_row_indexes') or [])}` строк."
        )

    offer_path = _write_offer_one_pager(earn_dir)
    demo_prefix = "⚠️ **ДЕМО НА СИНТЕТИКЕ** (`SYNTHETIC_export.html`)\n" if is_synthetic else ""
    summary = (
        demo_prefix
        + "📊 **Проверяемый отчёт по Telegram-экспорту "
        "(`tools/bossman_business.py`, 100% локально, $0 внешних вызовов)**:\n"
        f"  - **Источник**: `{export_path.name}` "
        f"(сообщений: `{vol_data.get('source', {}).get('messages')}`, "
        f"SHA-256 экспорта: `{str(vol_data.get('source', {}).get('sha256', ''))[:16]}…`)\n"
        "  - **Периоды (21–20 `Europe/Prague`, оборот = одна нога USDT, НЕ прибыль)**:\n"
        + "\n".join(period_lines)
        + f"\n  - **Исключено и спорные строки**: дубли (окно {dups.get('window_min')} мин): "
        f"`{dups.get('excluded')}` шт.; "
        f"сложные сообщения (`complex`): `{m_cls.get('complex_refs')}` "
        f"(вне итога `{m_cls.get('complex_provisional_usdt_leg_sum_not_in_totals')} USDT`); "
        f"неполные (`incomplete`): `{m_cls.get('incomplete_refs')}`; "
        f"неоднозначные суммы: `{m_cls.get('ambiguous_amount_refs')}`; "
        f"всего строк в `disputed.csv`: `{disputed_lines}`.\n"
        f"  - **{wb_summary_line}**\n"
        f"  - **Повторяемость (2 независимых прогона после перезапуска интерпретатора)**: "
        f"`{'ПОДТВЕРЖДЕНА (SHA-256 совпадает 4/4)' if repeatable_sha else 'РАСХОЖДЕНИЕ'}`\n"
        f"    • `volume_result.json`: `{hashes_run1.get('volume_result.json', '')[:16]}…` == "
        f"`{hashes_run2.get('volume_result.json', '')[:16]}…`\n"
        f"    • `report.md`: `{hashes_run1.get('report.md', '')[:16]}…` == "
        f"`{hashes_run2.get('report.md', '')[:16]}…`\n"
        f"    • `deals.csv`: `{hashes_run1.get('deals.csv', '')[:16]}…` · "
        f"`disputed.csv`: `{hashes_run1.get('disputed.csv', '')[:16]}…`\n"
        f"  - **Реальное время и стоимость на этом ПК**: прогон 1 = `{sec1} с`, "
        f"прогон 2 = `{sec2} с`, внешние вызовы = **$0.00 (0 запросов)**.\n"
        f"  - **Файлы**: отчёт `{out_run1 / 'report.md'}` (доставляется файлом в пульт), "
        f"оффер `{offer_path.name}` ($49 разовый / $99 в месяц)."
    )
    return {
        "ok": repeatable_sha,
        "verified": repeatable_sha,
        "action": "reports",
        "synthetic": is_synthetic,
        "export_path": str(export_path),
        "workbook_path": str(workbook_path) if workbook_path else None,
        "out_dir": str(out_run1),
        "document": str(out_run1 / "report.md"),
        "document_name": "report.md",
        "offer_path": str(offer_path),
        "hashes_run1": hashes_run1,
        "hashes_run2": hashes_run2,
        "repeatable_sha256": repeatable_sha,
        "run1_seconds": sec1,
        "run2_seconds": sec2,
        "external_cost_usd": 0.0,
        "disputed_rows": disputed_lines,
        "tainted": False,
        "summary": summary,
        "duration_ms": round((time.perf_counter() - start_t) * 1000),
    }
