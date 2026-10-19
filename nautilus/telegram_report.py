"""Telegram Pult report formatter, CLI entry point, and owner-only delivery for NAUTILUS.

Respects Bossman Eternal Prohibition #1 and Pult owner-only delivery rules (`command-center/bcc/direct_gen/pult.py`):
- Never executes trades or moves funds.
- Sends only to the single verified owner in companion `config.json` (`len(people) == 1` and `role == "owner"`).
- Never leaks bot tokens or chat IDs in logs or error messages.
"""
from __future__ import annotations

import argparse
import asyncio
import html
import json
import sys
from pathlib import Path
from typing import Any

import httpx

from .card import render_nautilus_card, save_nautilus_card
from .metrics import MultiWindowReport, compute_multi_window_report
from .portfolio import NautilusOperationalState, build_operational_state

DEFAULT_FIXTURE_PATH = Path(__file__).resolve().parent / "fixtures" / "nautilus_2026_sample.json"


def load_sample_fixture(path: str | Path | None = None) -> dict[str, Any]:
    """Load the NAUTILUS sample JSON fixture."""
    target = Path(path) if path else DEFAULT_FIXTURE_PATH
    return json.loads(target.read_text(encoding="utf-8"))


def _fmt_signed_pct(val: float) -> str:
    sign = "+" if val > 0 else ""
    return f"{sign}{val:.2f}%"


def _esc(val: Any) -> str:
    """Escape text for Telegram HTML parse_mode."""
    return html.escape(str(val), quote=False)


def is_demo_payload(payload: dict[str, Any] | None) -> bool:
    """Return True if the NAUTILUS payload is a demo/sample fixture (default fail-closed True unless certified live)."""
    if not isinstance(payload, dict):
        return True
    meta = payload.get("metadata") or {}
    if isinstance(meta, dict):
        if meta.get("pult_live_delivery_allowed") is False:
            return True
        if "is_demo" in meta:
            return bool(meta["is_demo"])
        if str(meta.get("data_origin_label", "")).strip().upper() in ("ДЕМО", "DEMO"):
            return True
    if "is_demo" in payload:
        return bool(payload["is_demo"])
    return True


def format_nautilus_telegram_message(
    report: MultiWindowReport,
    ops: NautilusOperationalState,
) -> str:
    """Format the comprehensive Russian Telegram Pult report covering all multi-window metrics & 5 operational blocks."""
    cs = report.card_strategy
    cb = report.card_benchmark
    ytd = report.ytd_btc
    inc = report.since_inception
    l26 = report.last_26_sessions

    mr = ops.market_regime
    lev = ops.leverage_exposure
    pc = ops.portfolio_comparison
    sa = ops.stress_audit
    sk = pc.silent_killer_comparison

    strat_lbl = _esc(report.card_strategy_label)
    bench_lbl = _esc(report.card_benchmark_label)
    ytd_strat = _esc(ytd.strategy_name)
    ytd_bench = _esc(ytd.benchmark_name)
    inc_strat = _esc(inc.strategy_name)
    inc_bench = _esc(inc.benchmark_name)
    l26_bench = _esc(l26.benchmark_name)

    demo_tag = f"[{_esc(report.demo_label)}] " if report.is_demo else ""
    lines: list[str] = [
        f"🧭 <b>{demo_tag}NAUTILUS / NAUTILUS_BTC — Аналитический отчёт (Shadow Read-Only)</b>",
        "<i>Режим: только аналитика и модельный портфель (Вечный запрет №1: 0 реальных ордеров, 0 ключей)</i>",
    ]
    if report.is_demo:
        demo_warn = _esc(
            report.metadata.get("demo_warning_ru")
            or "ДЕМО: синтетический эталонный снимок доходности (nautilus_2026_sample.json). Запрещено отправлять в Пульт владельца как реальный результат."
        )
        lines.append(f"⚠️ <b>{demo_warn}</b>")
    lines.extend(
        [
            "",
            f"📊 <b>1. Сводка 6 плиток ({strat_lbl} vs {bench_lbl}):</b>",
            f"• <b>CAGR:</b> {cs.cagr_pct:.2f}% (vs {bench_lbl}: {cb.cagr_pct:.2f}%)",
            f"• <b>SHARPE:</b> {cs.sharpe:.2f} (vs {bench_lbl}: {cb.sharpe:.2f})",
            f"• <b>SORTINO:</b> {cs.sortino:.2f} (vs {bench_lbl}: {cb.sortino:.2f})",
            f"• <b>MAX DRAWDOWN:</b> {cs.max_drawdown_pct:.2f}% (vs {bench_lbl}: {cb.max_drawdown_pct:.2f}%)",
            f"• <b>CALMAR:</b> {cs.calmar:.2f} (vs {bench_lbl}: {cb.calmar:.2f})",
            f"• <b>$1 BECOMES:</b> ${cs.dollar_becomes:.2f} (vs {bench_lbl}: ${cb.dollar_becomes:.2f})",
            "",
            "⏱ <b>2. Мульти-оконная динамика:</b>",
            f"• <b>YTD ({ytd_strat} vs {ytd_bench}):</b> {_fmt_signed_pct(ytd.strategy_return_pct)} vs {_fmt_signed_pct(ytd.benchmark_return_pct)} {ytd_bench} | Sharpe {ytd.strategy_sharpe:.2f} vs {ytd.benchmark_sharpe:.2f} | Max DD {ytd.strategy_max_dd_pct:.2f}% vs {ytd.benchmark_max_dd_pct:.2f}% | Аллокация: <code>{_esc(ytd.exposure_delta_note or mr.btc_exposure_transition)}</code>",
            f"• <b>Since Inception:</b> {inc_strat} {_fmt_signed_pct(inc.strategy_return_pct)} vs {inc_bench} {_fmt_signed_pct(inc.benchmark_return_pct)} (Excess {_fmt_signed_pct(inc.excess_return_pct)}) | Sharpe {inc.strategy_sharpe:.2f} | Vol {inc.strategy_volatility_pct:.2f}% | Max DD {inc.strategy_max_dd_pct:.2f}% | Beta {inc.beta:.3f}",
            f"• <b>Last 26 Trading Sessions:</b> Sharpe {l26.strategy_sharpe:.2f} vs {l26.benchmark_sharpe:.2f} {l26_bench} | Vol {l26.strategy_volatility_pct:.2f}% vs {l26.benchmark_volatility_pct:.2f}% {l26_bench} | Max DD {l26.strategy_max_dd_pct:.2f}% vs {l26.benchmark_max_dd_pct:.1f}% {l26_bench} | Beta {l26.beta:.3f}",
            f"• <b>Серия прибыльных недель:</b> 🔥 {_esc(report.winning_weeks_label)} (расчёт по ряду: {report.winning_weeks_streak})",
            "",
            "⚙️ <b>3. Пять операционных блоков NAUTILUS:</b>",
            f"<b>[Блок 1 · Режим рынка (market_regime)]</b> {_esc(mr.regime_code)} — {_esc(mr.regime_label_ru)}. Тренд: <code>{_esc(mr.trend_state)}</code>, волатильность: <code>{_esc(mr.volatility_state)}</code>. Переход BTC: <code>{_esc(mr.btc_exposure_transition)}</code>.",
            f"<b>[Блок 2 · Плечо и экспозиция]</b> Gross Leverage: <code>{lev.gross_leverage:.2f}x</code> | Net Exposure: <code>{lev.net_exposure:.2f}x</code> | Long: <code>{lev.long_pct:.1f}%</code> | Short (APEX hedge): <code>{lev.short_pct:.1f}%</code>",
            "<b>[Блок 3 · Периодический Trade Ticket (ребалансировка, Read-Only)]</b>",
        ]
    )

    for item in ops.trade_ticket:
        delta_str = _fmt_signed_pct(item.delta_weight_pct)
        sym_padded = _esc(f"{item.symbol:<8}")
        side_padded = _esc(f"{item.side:<5}")
        lines.append(
            f"  ▫️ <code>{sym_padded}</code> [{side_padded}] {item.old_weight_pct:>6.1f}% → {item.new_weight_pct:>6.1f}% ({delta_str}, {_esc(item.action)})"
        )

    lines.extend(
        [
            "<b>[Блок 4 · Модельный портфель vs Текущий &amp; Silent-Killer]</b>",
            f"  • Long-рукав: {_esc(pc.long_sleeve_description_ru)}",
            f"  • Short-хедж: {_esc(pc.short_hedge_description_ru)}",
            f"  • Сравнение с <b>{_esc(sk.get('profile_name', 'Silent-Killer'))}</b>: {_esc(sk.get('nautilus_advantage_ru', ''))}",
            "<b>[Блок 5 · Стресс-аудит и устойчивость (stress_audit)]</b>",
            f"  • Комиссии/проскальзывание ({sa.fee_slippage_bps_per_turn:.0f} bps × {sa.annual_turnover_turns:.0f}/год): Net CAGR <code>{sa.net_cagr_after_cost_pct:.2f}%</code> (базовый {sa.base_cagr_pct:.2f}%)",
            f"  • Без {sa.removed_best_n_days} лучших дней: CAGR <code>{sa.cagr_without_best_n_days_pct:.2f}%</code>, Sharpe <code>{sa.sharpe_without_best_n_days:.2f}</code>, Stressed Max DD <code>{sa.max_dd_stressed_pct:.2f}%</code>",
            f"  • ⚠️ {_esc(sa.sample_warning_ru)}",
            f"  • Вывод: {_esc(sa.verdict_ru)}",
        ]
    )
    return "\n".join(lines)


def _load_pult_target(config_path: Path | None = None) -> tuple[str, int, str]:
    """Load owner-only Telegram Pult credentials via command-center's load_target or direct companion config."""
    import os

    repo_root = Path(__file__).resolve().parents[1]
    cc_dir = repo_root / "command-center"
    if str(cc_dir) not in sys.path and cc_dir.is_dir():
        sys.path.insert(0, str(cc_dir))
    from bcc.direct_gen.pult import load_target

    resolved = config_path
    if resolved is None:
        local_app = os.environ.get("LOCALAPPDATA")
        if local_app:
            owner_cfg = Path(local_app) / "Bossman" / "telegram-companion" / "config.json"
            if owner_cfg.is_file():
                resolved = owner_cfg

    return load_target(resolved)


async def send_nautilus_report_to_pult(
    payload: dict[str, Any] | None = None,
    *,
    card_output_path: str | Path | None = None,
    config_path: Path | None = None,
    transport: httpx.AsyncBaseTransport | None = None,
    allow_demo: bool = False,
) -> dict[str, Any]:
    """Render the NAUTILUS 6-tile PNG card + Telegram report and send both to the owner-only Telegram Pult.

    Demo payloads (`nautilus_2026_sample.json` or any payload where `is_demo_payload(data)` is True)
    are strictly forbidden from being sent to the owner's Pult as a real result.
    """
    data = payload if payload is not None else load_sample_fixture()
    is_demo = is_demo_payload(data)
    if is_demo and not allow_demo:
        raise PermissionError(
            "ДЕМО: отправка демонстрационной доходности (nautilus_2026_sample.json) в Пульт владельца как реального результата запрещена."
        )

    report = compute_multi_window_report(data)
    ops = build_operational_state(data)
    message_html = format_nautilus_telegram_message(report, ops)

    png_bytes = render_nautilus_card(
        report.card_strategy,
        report.card_benchmark,
        strategy_label=report.card_strategy_label,
        benchmark_label=report.card_benchmark_label,
        is_demo=is_demo,
    )
    if card_output_path:
        out_file = Path(card_output_path)
        out_file.parent.mkdir(parents=True, exist_ok=True)
        out_file.write_bytes(png_bytes)

    token, chat_id, proxy = _load_pult_target(config_path)
    api_base = f"https://api.telegram.org/bot{token}"

    demo_prefix = "[ДЕМО] " if is_demo else ""
    caption = (
        f"{demo_prefix}NAUTILUS vs {report.card_benchmark_label} · CAGR {report.card_strategy.cagr_pct:.2f}% · "
        f"Sharpe {report.card_strategy.sharpe:.2f} · Sortino {report.card_strategy.sortino:.2f} · "
        f"MaxDD {report.card_strategy.max_drawdown_pct:.2f}% · $1 -> ${report.card_strategy.dollar_becomes:.2f}"
    )

    async with httpx.AsyncClient(
        timeout=httpx.Timeout(120, connect=20),
        follow_redirects=False,
        trust_env=False,
        proxy=(proxy or None) if transport is None else None,
        transport=transport,
    ) as client:
        photo_resp = await client.post(
            f"{api_base}/sendPhoto",
            data={"chat_id": str(chat_id), "caption": caption},
            files={"photo": ("nautilus_dashboard_card.png", png_bytes, "image/png")},
        )
        photo_body = photo_resp.json()
        if not isinstance(photo_body, dict) or photo_body.get("ok") is not True:
            code = photo_body.get("error_code") if isinstance(photo_body, dict) else photo_resp.status_code
            raise RuntimeError(f"Telegram rejected NAUTILUS card photo (code {code})")

        msg_resp = await client.post(
            f"{api_base}/sendMessage",
            json={
                "chat_id": chat_id,
                "text": message_html,
                "parse_mode": "HTML",
                "disable_web_page_preview": True,
            },
        )
        msg_body = msg_resp.json()
        if not isinstance(msg_body, dict) or msg_body.get("ok") is not True:
            code = msg_body.get("error_code") if isinstance(msg_body, dict) else msg_resp.status_code
            raise RuntimeError(f"Telegram rejected NAUTILUS text report (code {code})")

    return {
        "ok": True,
        "is_demo": is_demo,
        "photo_message_id": (photo_body.get("result") or {}).get("message_id"),
        "text_message_id": (msg_body.get("result") or {}).get("message_id"),
        "card_bytes": len(png_bytes),
    }


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(
        prog="python -m nautilus.telegram_report",
        description="Generate NAUTILUS 6-tile dark card and multi-window analytics report (Read-Only).",
    )
    parser.add_argument(
        "--fixture",
        type=Path,
        default=DEFAULT_FIXTURE_PATH,
        help="Path to NAUTILUS JSON fixture (default: nautilus/fixtures/nautilus_2026_sample.json)",
    )
    parser.add_argument(
        "--card-out",
        type=Path,
        default=None,
        help="Optional path to save the rendered 6-tile PNG card",
    )
    parser.add_argument(
        "--send-pult",
        action="store_true",
        help="Send the rendered card and HTML report to the owner's Telegram Pult",
    )
    args = parser.parse_args(argv)

    data = load_sample_fixture(args.fixture)
    report = compute_multi_window_report(data)
    ops = build_operational_state(data)

    if args.card_out:
        save_nautilus_card(
            args.card_out,
            report.card_strategy,
            report.card_benchmark,
            strategy_label=report.card_strategy_label,
            benchmark_label=report.card_benchmark_label,
            is_demo=report.is_demo,
        )

    if hasattr(sys.stdout, "reconfigure"):
        try:
            sys.stdout.reconfigure(encoding="utf-8", errors="replace")
        except Exception:  # noqa: BLE001
            pass

    if args.send_pult:
        result = asyncio.run(
            send_nautilus_report_to_pult(
                data,
                card_output_path=args.card_out,
            )
        )
        print(json.dumps(result, ensure_ascii=False))
    else:
        print(format_nautilus_telegram_message(report, ops))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
