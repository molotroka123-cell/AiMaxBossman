"""Долгосрочный лаунчпад проектов Pump.fun в рамках обучающего и бумажного модуля трейдинга.

Архитектурные принципы (анти-скам / долгосрочные проекты):
1. Никакой самоторговли (wash-trading), `KOTH_PULSE`, `FLOOR_DEFENSE` или маскировки графа кошельков.
   Как доказано в `pump_creator_cohort.py`, самоторговля на кривой платит ~1.25% комиссий ради 0.30%
   creator fee (чистый убыток -0.95% с оборота). Доход создателя приносит только ОРГАНИЧЕСКИЙ объём
   долгосрочного проекта с реальной полезностью (AI-агент, мини-приложение, игра, инструмент).
2. Конвейер состоит из 4 детерминированных стадий внутри модуля трейдинга:
   - `analyze_twitter_trends`: скоринг трендов X/Twitter по скорости роста, органической вовлечённости,
     долговечности нарратива (utility longevity) и анти-скам фильтру.
   - `build_project_launch_plan`: формирование паспорта долгосрочного проекта (дорожная карта 30/60/90 дней,
     манифест метаданных IPFS) и прозрачного генезис-бандла (`GenesisBundlePlan`) с расчётом кривой
     покупки, проскальзывания в lamports, прозрачного вестинга казны и оценки Jito tip.
   - `build_twitter_marketing_campaign`: генерация контент-плана и постов для X/Twitter аккаунта владельца
     (анонс, демонстрация продукта, отчёт о прозрачности казны и комиссий). Каждый пост создаётся в статусе
     `DRAFT_AWAITING_OWNER_APPROVAL` — публикация возможна только после явного подтверждения владельца.
   - `calculate_creator_fee_projections`: точный расчёт дохода на комиссиях торгов (`creator_fee_usd`)
     по таблицам mainnet `pump_fee_tables_20261009.json` (30 bps на кривой + 5..95 bps на PumpSwap по тирам
     капитализации) на горизонтах 1 / 7 / 30 / 90 дней и сравнение с убыточной самоторговлей.
3. Все действия создания бандла и публикации в X/Twitter защищены `PAPER_TRADING_ONLY` и `OWNER_APPROVAL_REQUIRED`.
"""
from __future__ import annotations

import hashlib
import json
import math
from dataclasses import asdict, dataclass, field
from datetime import datetime, timezone
from typing import Any

from .pump_creator_cohort import creator_fee_usd, load_tables
from .safety import (
    EXTERNAL_WRITE_ACTIONS,
    OWNER_APPROVAL_REQUIRED,
    PAPER_TRADING_ONLY,
    TRADING_EXECUTION,
    EvidenceClass,
)

LAMPORTS_PER_SOL = 1_000_000_000
PUMP_INITIAL_VIRTUAL_SOL = 30.0
PUMP_INITIAL_VIRTUAL_TOKENS = 1_073_000_000.0
PUMP_TOTAL_SUPPLY_TOKENS = 1_000_000_000.0
PUMP_MIGRATION_SOL = 85.0
MAX_SLICE_IMPACT_PCT = 1.2
MAX_TRANSPARENT_TREASURY_SOL = 15.0

SCAM_KEYWORDS = frozenset({
    "rug", "honeypot", "pump and dump", "1000x guaranteed", "double your sol",
    "send sol get back", "fake volume", "wash trade", "insider dump",
})

DEFAULT_TWITTER_SIGNALS: tuple[dict[str, Any], ...] = (
    {
        "topic": "Autonomous AI Agents & Utility Copilots",
        "hashtags": ["#AIAgents", "#SolanaAI", "#DePIN", "#OpenSourceAI"],
        "mentions_24h": 48500,
        "velocity_pct_6h": 142.0,
        "organic_author_ratio": 0.84,
        "builder_engagement_score": 0.91,
        "utility_fit": "AI-агент и мобильный пульт автоматизации с открытой телеметрией",
        "suggested_name": "Bossman AI Utility",
        "suggested_ticker": "BOSSAI",
    },
    {
        "topic": "3D Geo-World & Browser Mini-Games on Solana",
        "hashtags": ["#Web3Gaming", "#3DWorld", "#IndieGameDev", "#SolanaGaming"],
        "mentions_24h": 31200,
        "velocity_pct_6h": 96.5,
        "organic_author_ratio": 0.79,
        "builder_engagement_score": 0.86,
        "utility_fit": "3D-мир реальных городов (OSM + glTF меши) с внутриигровыми турнирами",
        "suggested_name": "Aleph City 3D",
        "suggested_ticker": "ALEPH3D",
    },
    {
        "topic": "Zero-Trust Cyber Defense & White-Hat Bounty",
        "hashtags": ["#CyberSecurity", "#BugBounty", "#ZeroTrust", "#SecOps"],
        "mentions_24h": 22900,
        "velocity_pct_6h": 78.0,
        "organic_author_ratio": 0.88,
        "builder_engagement_score": 0.89,
        "utility_fit": "Платформа аудита безопасности смарт-контрактов и белых баунти-отчётов",
        "suggested_name": "Sentinel Audit Guild",
        "suggested_ticker": "SNTNL",
    },
)


@dataclass(frozen=True)
class TwitterTrendCandidate:
    topic: str
    hashtags: tuple[str, ...]
    mentions_24h: int
    velocity_pct_6h: float
    organic_author_ratio: float
    builder_engagement_score: float
    longevity_score: float
    composite_rank_score: float
    anti_scam_passed: bool
    utility_fit: str
    suggested_name: str
    suggested_ticker: str


@dataclass(frozen=True)
class GenesisBundleSlice:
    index: int
    role: str
    purpose_ru: str
    amount_lamports: int
    amount_sol: float
    max_input_lamports: int
    estimated_tokens: float
    share_of_supply_pct: float
    price_impact_pct: float
    lockup_days: int


@dataclass(frozen=True)
class GenesisBundlePlan:
    venue: str
    mode: str
    plan_verification_status: str
    allocation_status: str
    project_name: str
    ticker: str
    requested_lamports: int
    allocated_lamports: int
    unallocated_lamports: int
    total_treasury_sol: float
    allocated_treasury_sol: float
    unallocated_treasury_sol: float
    slippage_bps: int
    jito_tip_lamports: int
    total_estimated_tokens: float
    total_supply_share_pct: float
    curve_progress_after_genesis_pct: float
    slices: tuple[GenesisBundleSlice, ...]
    planned_design_constraints: tuple[str, ...]
    anti_rug_guarantees: tuple[str, ...]
    plan_sha256: str


@dataclass(frozen=True)
class TwitterPostDraft:
    post_id: str
    stage: str
    scheduled_offset_hours: int
    text: str
    hashtags: tuple[str, ...]
    status: str = "DRAFT_AWAITING_OWNER_APPROVAL"
    requires_owner_approval: bool = True


def _clean_scam_check(text: str) -> bool:
    low = (text or "").lower()
    return not any(bad in low for bad in SCAM_KEYWORDS)


def analyze_twitter_trends(
    raw_signals: list[dict[str, Any]] | None = None,
    *,
    min_organic_ratio: float = 0.65,
    use_demo_fixtures: bool = True,
) -> dict[str, Any]:
    """Проанализировать сигналы X/Twitter и отобрать долгосрочные тренды (отсеивая скам и ботов).

    Если `raw_signals` не передан:
    - при `use_demo_fixtures=True` явно помечает источник как `DEMO_FIXTURE` (не выдавая за live-поиск);
    - при `use_demo_fixtures=False` возвращает честный статус `N/A` без выдумывания сигналов.
    """
    if raw_signals is None:
        if not use_demo_fixtures:
            return {
                "status": "N/A",
                "data_source": "NONE",
                "evidence_class": EvidenceClass.SIMULATED.value,
                "evaluated_count": 0,
                "accepted_count": 0,
                "rejected_count": 0,
                "winner": None,
                "candidates": [],
                "rejected": [],
                "note": "No live X/Twitter feed provided and use_demo_fixtures=False",
            }
        signals = list(DEFAULT_TWITTER_SIGNALS)
        data_source = "DEMO_FIXTURE"
    else:
        signals = list(raw_signals)
        data_source = "USER_PROVIDED_SIGNALS" if signals else "EMPTY_INPUT"
        if not signals:
            return {
                "status": "N/A",
                "data_source": data_source,
                "evidence_class": EvidenceClass.SIMULATED.value,
                "evaluated_count": 0,
                "accepted_count": 0,
                "rejected_count": 0,
                "winner": None,
                "candidates": [],
                "rejected": [],
                "note": "Empty signals list provided; returning N/A without synthetic fallback",
            }
    accepted: list[TwitterTrendCandidate] = []
    rejected: list[dict[str, Any]] = []

    for item in signals:
        topic = str(item.get("topic") or "").strip()
        if not topic:
            continue
        utility_fit = str(item.get("utility_fit") or "Долгосрочный продуктовый токен с открытой дорожной картой")
        combined_text = f"{topic} {utility_fit} {' '.join(str(h) for h in (item.get('hashtags') or []))}"
        anti_scam = _clean_scam_check(combined_text)
        organic = float(item.get("organic_author_ratio") or 0.0)
        builder = float(item.get("builder_engagement_score") or 0.0)
        velocity = max(0.0, float(item.get("velocity_pct_6h") or 0.0))
        mentions = max(0, int(item.get("mentions_24h") or 0))

        if not anti_scam:
            rejected.append({"topic": topic, "reason": "SCAM_OR_PUMP_AND_DUMP_PATTERN_BLOCKED"})
            continue
        if organic < min_organic_ratio:
            rejected.append({
                "topic": topic,
                "reason": f"LOW_ORGANIC_RATIO ({organic:.2f} < {min_organic_ratio:.2f}) — подозрение на бот-сетку",
            })
            continue

        # Longevity favours real builder engagement + organic ratio over raw short-term velocity spikes
        longevity = round(min(1.0, 0.55 * builder + 0.45 * organic), 4)
        velocity_norm = min(1.0, math.log1p(velocity) / math.log1p(250.0))
        volume_norm = min(1.0, math.log1p(mentions) / math.log1p(100_000.0))
        composite = round(100.0 * (0.45 * longevity + 0.30 * velocity_norm + 0.25 * volume_norm), 2)

        ticker_raw = str(item.get("suggested_ticker") or "BOSS").upper()
        ticker = "".join(ch for ch in ticker_raw if ch.isalnum())[:10] or "BOSS"
        name = str(item.get("suggested_name") or topic[:32]).strip()

        accepted.append(TwitterTrendCandidate(
            topic=topic,
            hashtags=tuple(str(h) for h in (item.get("hashtags") or ())[:6]),
            mentions_24h=mentions,
            velocity_pct_6h=round(velocity, 2),
            organic_author_ratio=round(organic, 4),
            builder_engagement_score=round(builder, 4),
            longevity_score=longevity,
            composite_rank_score=composite,
            anti_scam_passed=True,
            utility_fit=utility_fit,
            suggested_name=name,
            suggested_ticker=ticker,
        ))

    accepted.sort(key=lambda c: c.composite_rank_score, reverse=True)
    winner = accepted[0] if accepted else None
    return {
        "status": "DEMO" if data_source == "DEMO_FIXTURE" else ("OK" if accepted else "N/A"),
        "data_source": data_source,
        "evidence_class": EvidenceClass.SIMULATED.value,
        "evaluated_count": len(signals),
        "accepted_count": len(accepted),
        "rejected_count": len(rejected),
        "winner": asdict(winner) if winner else None,
        "candidates": [asdict(c) for c in accepted],
        "rejected": rejected,
    }


def _curve_tokens_out(sol_in: float, v_sol: float, v_tok: float) -> float:
    if sol_in <= 0.0 or v_sol <= 0.0 or v_tok <= 0.0:
        return 0.0
    new_sol = v_sol + sol_in
    new_tok = (v_sol * v_tok) / new_sol
    return max(0.0, v_tok - new_tok)


def _price_impact_pct(sol_in: float, v_sol: float) -> float:
    if sol_in <= 0.0 or v_sol <= 0.0:
        return 0.0
    spot_before = v_sol
    spot_after = v_sol + sol_in
    return ((spot_after - spot_before) / spot_before) * 100.0


def build_project_launch_plan(
    *,
    project_name: str,
    ticker: str,
    thesis: str,
    twitter_handle: str = "@owner_project",
    website_url: str = "https://bossman.local/project",
    treasury_sol: float = 3.0,
    slippage_bps: int = 150,
    jito_tip_lamports: int = 100_000,
    max_slices: int = 64,
) -> dict[str, Any]:
    """Сформировать паспорт долгосрочного токена Pump.fun и прозрачный Genesis Bundle Plan (PAPER).

    Инвариант сохранения lamports (Codex Finding #1):
    `requested_lamports == allocated_lamports + unallocated_lamports` (все >= 0).
    Если `max_slices` исчерпан до полного распределения `requested_lamports`, возвращается
    `allocation_status = "PARTIAL"`, а `curve_progress_after_genesis_pct` считается строго
    от реально распределённых `allocated_lamports`.
    """
    clean_name = (project_name or "").strip()[:48]
    clean_ticker = "".join(ch for ch in (ticker or "").upper() if ch.isalnum())[:10]
    if not clean_name or not clean_ticker:
        raise ValueError("project_name and alphanumeric ticker are required")
    if not _clean_scam_check(f"{clean_name} {clean_ticker} {thesis}"):
        raise ValueError("Project description failed anti-scam policy check")
    if not math.isfinite(treasury_sol) or treasury_sol <= 0.0 or treasury_sol > MAX_TRANSPARENT_TREASURY_SOL:
        raise ValueError(f"treasury_sol must be in (0, {MAX_TRANSPARENT_TREASURY_SOL}] SOL for transparent genesis")
    if int(max_slices) < 1:
        raise ValueError("max_slices must be >= 1")

    requested_lamports = int(round(float(treasury_sol) * LAMPORTS_PER_SOL))
    remaining_lamports = requested_lamports

    # Slice total treasury in integer lamports so each step has <= MAX_SLICE_IMPACT_PCT impact on the virtual curve
    v_sol = PUMP_INITIAL_VIRTUAL_SOL
    v_tok = PUMP_INITIAL_VIRTUAL_TOKENS
    roles = (
        ("dev_treasury_vested", "План: казна разработки продукта (целевой вестинг 90 дней, off-chain план)", 90),
        ("ecosystem_grants", "План: фонд грантов комьюнити и контрибьюторов (целевой вестинг 60 дней, off-chain план)", 60),
        ("marketing_partnerships", "План: маркетинг в X/Twitter и интеграции (целевой вестинг 30 дней, off-chain план)", 30),
    )

    slices: list[GenesisBundleSlice] = []
    idx = 0
    total_tokens = 0.0
    allocated_lamports = 0
    while remaining_lamports > 0 and idx < int(max_slices):
        max_step_lamports = max(
            50_000_000,
            int(math.floor(v_sol * (MAX_SLICE_IMPACT_PCT / 100.0) * 0.95 * LAMPORTS_PER_SOL)),
        )
        step_lamports = min(remaining_lamports, max_step_lamports)
        step_sol = step_lamports / LAMPORTS_PER_SOL
        impact = _price_impact_pct(step_sol, v_sol)
        tokens = _curve_tokens_out(step_sol, v_sol, v_tok)
        role_key, role_desc, lock_days = roles[idx % len(roles)]
        max_lamports = (step_lamports * (10_000 + int(slippage_bps)) + 9_999) // 10_000
        supply_pct = (tokens / PUMP_TOTAL_SUPPLY_TOKENS) * 100.0
        slices.append(GenesisBundleSlice(
            index=idx,
            role=role_key,
            purpose_ru=role_desc,
            amount_lamports=step_lamports,
            amount_sol=round(step_sol, 9),
            max_input_lamports=max_lamports,
            estimated_tokens=round(tokens, 2),
            share_of_supply_pct=round(supply_pct, 4),
            price_impact_pct=round(impact, 4),
            lockup_days=lock_days,
        ))
        v_sol += step_sol
        v_tok -= tokens
        total_tokens += tokens
        allocated_lamports += step_lamports
        remaining_lamports -= step_lamports
        idx += 1

    unallocated_lamports = remaining_lamports
    assert requested_lamports == allocated_lamports + unallocated_lamports
    assert allocated_lamports >= 0 and unallocated_lamports >= 0

    allocation_status = "COMPLETE" if unallocated_lamports == 0 else "PARTIAL"
    allocated_sol = round(allocated_lamports / LAMPORTS_PER_SOL, 9)
    unallocated_sol = round(unallocated_lamports / LAMPORTS_PER_SOL, 9)
    total_supply_pct = round((total_tokens / PUMP_TOTAL_SUPPLY_TOKENS) * 100.0, 4)
    curve_progress_pct = round(min(100.0, (allocated_sol / PUMP_MIGRATION_SOL) * 100.0), 2)

    constraints = (
        "ПРОЕКТНОЕ ОГРАНИЧЕНИЕ (PAPER_PLAN_ONLY, не является on-chain доказательством): все транши размечены по фондам",
        "Политика модуля: запрет самоторговли (wash-trading) и скрытых кластеров; проверка концентрации через holder_monitor",
        f"Распределено в плане: {allocated_sol:.6f} SOL ({total_supply_pct:.2f}% от 1 млрд токенов), нераспределённый остаток: {unallocated_sol:.6f} SOL",
        "Публикация транзакций и постов заблокирована до явного подтверждения владельца (OWNER_APPROVAL_REQUIRED)",
    )

    manifest = {
        "name": clean_name,
        "symbol": clean_ticker,
        "description": thesis.strip()[:600],
        "twitter": twitter_handle.strip(),
        "website": website_url.strip(),
        "showName": True,
        "createdOn": "https://pump.fun",
    }
    roadmap = [
        {"horizon": "День 1–7", "milestone": "Генезис-запуск на Pump.fun, публикация открытого манифеста казны и запуск MVP-утилиты"},
        {"horizon": "День 8–30", "milestone": "Миграция на PumpSwap AMM (420 SOL mcap), еженедельные отчёты по creator fee и релиз v1.1"},
        {"horizon": "День 31–90", "milestone": "Масштабирование экосистемы, партнёрские интеграции в X/Twitter и гранты комьюнити из казны"},
    ]

    digest_payload = {
        "name": clean_name,
        "ticker": clean_ticker,
        "requested_lamports": requested_lamports,
        "allocated_lamports": allocated_lamports,
        "unallocated_lamports": unallocated_lamports,
        "slices": [asdict(s) for s in slices],
        "manifest": manifest,
    }
    plan_hash = hashlib.sha256(
        json.dumps(digest_payload, sort_keys=True, ensure_ascii=False).encode("utf-8")
    ).hexdigest()

    bundle_plan = GenesisBundlePlan(
        venue="pumpfun_bonding_curve_paper",
        mode="PAPER_PLAN_ONLY",
        plan_verification_status="UNVERIFIED_OFFCHAIN_PLAN_ONLY",
        allocation_status=allocation_status,
        project_name=clean_name,
        ticker=clean_ticker,
        requested_lamports=requested_lamports,
        allocated_lamports=allocated_lamports,
        unallocated_lamports=unallocated_lamports,
        total_treasury_sol=round(requested_lamports / LAMPORTS_PER_SOL, 9),
        allocated_treasury_sol=allocated_sol,
        unallocated_treasury_sol=unallocated_sol,
        slippage_bps=int(slippage_bps),
        jito_tip_lamports=int(jito_tip_lamports),
        total_estimated_tokens=round(total_tokens, 2),
        total_supply_share_pct=total_supply_pct,
        curve_progress_after_genesis_pct=curve_progress_pct,
        slices=tuple(slices),
        planned_design_constraints=constraints,
        anti_rug_guarantees=constraints,
        plan_sha256=plan_hash,
    )

    return {
        "evidence_class": EvidenceClass.SIMULATED.value,
        "safety": {
            "trading_execution": TRADING_EXECUTION,
            "paper_trading_only": PAPER_TRADING_ONLY,
            "owner_approval_required": OWNER_APPROVAL_REQUIRED,
            "external_write_actions": EXTERNAL_WRITE_ACTIONS,
        },
        "metadata_manifest": manifest,
        "roadmap_30_60_90": roadmap,
        "genesis_bundle": asdict(bundle_plan),
    }


def build_twitter_marketing_campaign(
    *,
    project_name: str,
    ticker: str,
    thesis: str,
    twitter_handle: str = "@owner_project",
    hashtags: list[str] | None = None,
) -> dict[str, Any]:
    """Сгенерировать серию маркетинговых постов для X/Twitter владельца (все в статусе черновика под одобрение)."""
    clean_name = (project_name or "Bossman Project").strip()
    clean_ticker = "".join(ch for ch in (ticker or "BOSS").upper() if ch.isalnum())[:10] or "BOSS"
    tags = tuple(hashtags[:4]) if hashtags else ("#Solana", "#AIAgents", f"${clean_ticker}")

    posts = (
        TwitterPostDraft(
            post_id=f"tw-{clean_ticker.lower()}-01-launch",
            stage="1_genesis_announcement",
            scheduled_offset_hours=0,
            text=(
                f"Запускаем долгосрочный проект {clean_name} (${clean_ticker}) на Solana!\n\n"
                f"Миссия: {thesis.strip()[:160]}\n"
                f"Прозрачный генезис-бандл с вестингом казны 30–90 дней, без скрытых кошельков и накрутки. "
                f"Дорожная карта и код открыты. {' '.join(tags)}"
            ),
            hashtags=tags,
        ),
        TwitterPostDraft(
            post_id=f"tw-{clean_ticker.lower()}-02-utility",
            stage="2_product_utility_demo",
            scheduled_offset_hours=6,
            text=(
                f"Почему ${clean_ticker} — это продукт, а не однодневный мем:\n"
                f"1) Реальная утилита внутри экосистемы {clean_name}\n"
                f"2) Развитие за счёт прозрачных комиссий пула (creator fee), а не дампа токенов\n"
                f"3) Публичный дашборд метрик и апдейтов для комьюнити. {' '.join(tags)}"
            ),
            hashtags=tags,
        ),
        TwitterPostDraft(
            post_id=f"tw-{clean_ticker.lower()}-03-transparency",
            stage="3_treasury_and_fee_report",
            scheduled_offset_hours=24,
            text=(
                f"Отчёт первых 24ч по проекту {clean_name} (${clean_ticker}):\n"
                f"Казна разработки заблокирована по графику вестинга, комиссии пула направляются "
                f"на развитие инфраструктуры и гранты контрибьюторам. Следующий релиз по плану! {' '.join(tags)}"
            ),
            hashtags=tags,
        ),
        TwitterPostDraft(
            post_id=f"tw-{clean_ticker.lower()}-04-roadmap",
            stage="4_weekly_milestone_thread",
            scheduled_offset_hours=72,
            text=(
                f"Обновление дорожной карты {clean_name} (${clean_ticker}) на 30/60/90 дней:\n"
                f"Публикуем прогресс разработки и открываем приём предложений от держателей. "
                f"Долгосрочное строительство на Solana. {' '.join(tags)}"
            ),
            hashtags=tags,
        ),
    )

    return {
        "evidence_class": EvidenceClass.SIMULATED.value,
        "account_handle": twitter_handle,
        "publish_mode": "OWNER_APPROVAL_GATE_ONLY",
        "auto_post_without_approval": False,
        "posts_count": len(posts),
        "drafts": [asdict(p) for p in posts],
    }


def calculate_creator_fee_projections(
    *,
    daily_curve_volume_usd: float | None = 25_000.0,
    daily_pumpswap_volume_usd: float | None = 120_000.0,
    market_cap_usd: float | None = 85_000.0,
    sol_price_usd: float | None = 180.0,
    daily_expenses_usd: float = 0.0,
) -> dict[str, Any]:
    """Рассчитать оценку комиссий создателя по историческому снимку `pump_fee_tables_20261009.json`.

    Отделяет (Codex Finding #5):
    - `estimated_gross_usd` (модельная оценка по исторической таблице),
    - `accrued_usd` ("N/A" без on-chain выписки),
    - `claimed_received_usd` ("N/A" без подтверждённого поступления),
    - `estimated_expenses_usd` (документированные расходы),
    - `realized_net_usd` ("N/A" в paper-режиме без реальных выплат).
    """
    tables = load_tables()
    if (
        daily_curve_volume_usd is None
        or daily_pumpswap_volume_usd is None
        or market_cap_usd is None
        or sol_price_usd is None
    ):
        return {
            "status": "N/A",
            "evidence_class": EvidenceClass.SIMULATED.value,
            "fee_table_provenance": {
                "source": "HISTORICAL_SNAPSHOT",
                "file": "pump_fee_tables_20261009.json",
                "fetched_at": tables.get("fetched_at"),
            },
            "reason": "Missing required market_cap_usd, sol_price_usd, or volume inputs",
        }
    if (
        not math.isfinite(daily_curve_volume_usd)
        or not math.isfinite(daily_pumpswap_volume_usd)
        or not math.isfinite(market_cap_usd)
        or not math.isfinite(sol_price_usd)
        or daily_curve_volume_usd < 0.0
        or daily_pumpswap_volume_usd < 0.0
        or market_cap_usd < 0.0
        or sol_price_usd <= 0.0
        or daily_expenses_usd < 0.0
    ):
        raise ValueError("Volume, market_cap_usd, expenses (>=0) and sol_price_usd (>0) must be finite")

    daily = creator_fee_usd(
        curve_vol_usd=float(daily_curve_volume_usd),
        swap_vol_usd=float(daily_pumpswap_volume_usd),
        mcap_usd=float(market_cap_usd),
        sol_usd=float(sol_price_usd),
        tables=tables,
    )
    daily_total = float(daily["total_usd"])
    daily_net_after_est_expenses = round(daily_total - float(daily_expenses_usd), 2)

    # Contrast with wash-trading (self-volume pays ~125 bps total fee to get 30 bps back = -95 bps net loss)
    total_vol = float(daily_curve_volume_usd) + float(daily_pumpswap_volume_usd)
    wash_trade_net_loss_usd = round(total_vol * (95.0 / 10_000.0), 2)

    horizons = {
        "1d_usd": round(daily_total, 2),
        "7d_usd": round(daily_total * 7, 2),
        "30d_usd": round(daily_total * 30, 2),
        "90d_usd": round(daily_total * 90, 2),
    }

    return {
        "status": "SIMULATED",
        "evidence_class": EvidenceClass.SIMULATED.value,
        "fee_tables_snapshot": tables.get("fetched_at"),
        "fee_table_provenance": {
            "source": "HISTORICAL_SNAPSHOT",
            "file": "pump_fee_tables_20261009.json",
            "fetched_at": tables.get("fetched_at"),
            "note": "Historical snapshot only; live protocol/platform/creator fee sharing rules must be re-verified at execution time",
        },
        "inputs": {
            "daily_curve_volume_usd": round(float(daily_curve_volume_usd), 2),
            "daily_pumpswap_volume_usd": round(float(daily_pumpswap_volume_usd), 2),
            "market_cap_usd": round(float(market_cap_usd), 2),
            "sol_price_usd": round(float(sol_price_usd), 2),
            "market_cap_sol": round(float(market_cap_usd) / float(sol_price_usd), 2),
        },
        "rates_bps": {
            "bonding_curve_creator_bps": daily["curve_bps"],
            "pumpswap_creator_bps": daily["swap_bps"],
        },
        "daily_breakdown_usd": {
            "curve_fee_usd": round(float(daily["curve_usd"]), 2),
            "pumpswap_fee_usd": round(float(daily["swap_usd"]), 2),
            "total_creator_fee_usd": round(daily_total, 2),
        },
        "accounting_ledger": {
            "estimated_gross_usd": round(daily_total, 2),
            "accrued_usd": "N/A",
            "claimed_received_usd": "N/A",
            "estimated_expenses_usd": round(float(daily_expenses_usd), 2),
            "estimated_net_model_usd": daily_net_after_est_expenses,
            "realized_net_usd": "N/A",
        },
        "projections_usd": horizons,
        "anti_scam_economics": {
            "organic_project_daily_net_usd": daily_net_after_est_expenses,
            "wash_trading_same_volume_daily_net_usd": -wash_trade_net_loss_usd,
            "verdict": (
                "Модельная оценка по исторической таблице: добровольный органический объём даёт положительное ожидание "
                "creator fee до вычета расходов, тогда как самоторговля (накрутка) убыточна (-0.95% с оборота)."
            ),
        },
    }


def launchpad_overview() -> dict[str, Any]:
    """Полная сводка возможностей Pump.fun Project Launchpad внутри модуля трейдинга."""
    trends = analyze_twitter_trends()
    winner = trends.get("winner") or {}
    sample_plan = build_project_launch_plan(
        project_name=winner.get("suggested_name") or "Bossman AI Utility",
        ticker=winner.get("suggested_ticker") or "BOSSAI",
        thesis=winner.get("utility_fit") or "Долгосрочный AI-проект с открытой дорожной картой",
        treasury_sol=3.0,
    )
    sample_campaign = build_twitter_marketing_campaign(
        project_name=sample_plan["metadata_manifest"]["name"],
        ticker=sample_plan["metadata_manifest"]["symbol"],
        thesis=sample_plan["metadata_manifest"]["description"],
        hashtags=list(winner.get("hashtags") or ["#Solana", "#AIAgents"]),
    )
    sample_fees = calculate_creator_fee_projections()
    five_wallets = simulate_five_wallet_genesis_launch(
        project_name=sample_plan["metadata_manifest"]["name"],
        ticker=sample_plan["metadata_manifest"]["symbol"],
        total_sol_budget=3.0,
    )
    volume_control = simulate_volume_and_fee_control(
        ticker=sample_plan["metadata_manifest"]["symbol"],
    )
    return {
        "available": True,
        "module": "bossman.trading_learning.pump_project_launchpad",
        "mode": "LONG_TERM_PROJECT_PAPER_LAUNCHPAD",
        "evidence_class": EvidenceClass.SIMULATED.value,
        "badge": "PAPER",
        "safety": {
            "trading_execution": TRADING_EXECUTION,
            "paper_trading_only": PAPER_TRADING_ONLY,
            "owner_approval_required": OWNER_APPROVAL_REQUIRED,
            "external_write_actions": EXTERNAL_WRITE_ACTIONS,
            "wash_trading_forbidden": True,
        },
        "trend_analysis": trends,
        "sample_project_plan": sample_plan,
        "five_wallet_genesis": five_wallets,
        "volume_and_fee_control": volume_control,
        "sample_twitter_campaign": sample_campaign,
        "sample_fee_projection": sample_fees,
        "updated_at": datetime.now(timezone.utc).isoformat(timespec="seconds"),
    }


FIVE_WALLET_ROLES: tuple[tuple[str, str, float, int], ...] = (
    ("wallet_1_creator_seed", "Кошелёк #1 — Creator Genesis Seed (публичный вестинг 90 дн.)", 0.25, 90),
    ("wallet_2_dev_treasury", "Кошелёк #2 — Казна R&D и инфраструктуры (публичный вестинг 90 дн.)", 0.25, 90),
    ("wallet_3_ecosystem_grants", "Кошелёк #3 — Фонд грантов и наград комьюнити (вестинг 60 дн.)", 0.20, 60),
    ("wallet_4_marketing_kol_escrow", "Кошелёк #4 — Маркетинг X/Twitter и партнёрства (вестинг 30 дн.)", 0.15, 30),
    ("wallet_5_liquidity_reserve", "Кошелёк #5 — Резерв ликвидности под миграцию PumpSwap (лок 60 дн.)", 0.15, 60),
)


def simulate_five_wallet_genesis_launch(
    *,
    project_name: str = "Bossman AI Utility",
    ticker: str = "BOSSAI",
    total_sol_budget: float = 3.0,
    slippage_bps: int = 150,
    sol_price_usd: float = 180.0,
) -> dict[str, Any]:
    """Симуляция стартового распределения по 5 публичным целевым кошелькам проекта в первые секунды (Block 0..2).

    Вместо скрытого снайпинга все 5 кошельков публично декларируются в манифесте проекта,
    а их ордера нарезаются траншами с ценовым влиянием <= 1.2% (120 bps) на шаг кривой Pump.fun.
    """
    from .holder_monitor import Holder, analyse as analyse_holders

    if not math.isfinite(total_sol_budget) or total_sol_budget <= 0.0 or total_sol_budget > MAX_TRANSPARENT_TREASURY_SOL:
        raise ValueError(f"total_sol_budget must be in (0, {MAX_TRANSPARENT_TREASURY_SOL}] SOL")

    v_sol = PUMP_INITIAL_VIRTUAL_SOL
    v_tok = PUMP_INITIAL_VIRTUAL_TOKENS
    wallets_report: list[dict[str, Any]] = []
    all_tranches: list[dict[str, Any]] = []
    tranche_seq = 0
    total_tokens_bought = 0.0
    total_protocol_fee_sol = 0.0
    total_creator_rebate_sol = 0.0

    for w_idx, (role_id, label_ru, weight, lock_days) in enumerate(FIVE_WALLET_ROLES, start=1):
        wallet_sol = round(float(total_sol_budget) * weight, 6)
        rem = wallet_sol
        w_tokens = 0.0
        w_tranches = 0
        first_price_sol = v_sol / v_tok
        while rem > 1e-6 and tranche_seq < 60:
            max_step = max(0.05, (v_sol * (MAX_SLICE_IMPACT_PCT / 100.0)) * 0.95)
            step_sol = round(min(rem, max_step), 6)
            impact_pct = _price_impact_pct(step_sol, v_sol)
            impact_bps = round(impact_pct * 100.0, 2)
            tokens_out = _curve_tokens_out(step_sol, v_sol, v_tok)
            lamports_in = int(round(step_sol * LAMPORTS_PER_SOL))
            max_lamports = (lamports_in * (10_000 + int(slippage_bps)) + 9_999) // 10_000
            protocol_fee = round(step_sol * 0.010, 6)
            creator_rebate = round(step_sol * 0.003, 6)

            v_sol += step_sol
            v_tok -= tokens_out
            w_tokens += tokens_out
            total_tokens_bought += tokens_out
            total_protocol_fee_sol += protocol_fee
            total_creator_rebate_sol += creator_rebate
            rem = round(rem - step_sol, 6)
            w_tranches += 1
            tranche_seq += 1

            all_tranches.append({
                "seq": tranche_seq,
                "second_offset": round((tranche_seq - 1) * 0.35, 2),
                "wallet_index": w_idx,
                "wallet_role": role_id,
                "amount_sol": step_sol,
                "max_input_lamports": max_lamports,
                "tokens_out": round(tokens_out, 2),
                "price_impact_bps": impact_bps,
                "liquidity_gate_pass": impact_bps <= 120.0,
                "execution_allowed_live": False,
            })

        last_price_sol = v_sol / v_tok
        supply_pct = round((w_tokens / PUMP_TOTAL_SUPPLY_TOKENS) * 100.0, 4)
        pub_addr = f"BossVault{w_idx}{hashlib.sha256(f'{ticker}:{role_id}'.encode()).hexdigest()[:24]}"
        wallets_report.append({
            "wallet_index": w_idx,
            "wallet_role": role_id,
            "public_address_tag": pub_addr,
            "label_ru": label_ru,
            "allocated_sol": wallet_sol,
            "allocated_usd": round(wallet_sol * sol_price_usd, 2),
            "tranches_count": w_tranches,
            "tokens_acquired": round(w_tokens, 2),
            "share_of_total_supply_pct": supply_pct,
            "avg_entry_price_sol": round(first_price_sol, 10),
            "post_wallet_price_sol": round(last_price_sol, 10),
            "lockup_days": lock_days,
            "disclosed_in_manifest": True,
        })

    # Verify holder distribution via holder_monitor.analyse (including bonding curve pool account)
    remaining_curve_tokens = max(0.0, PUMP_TOTAL_SUPPLY_TOKENS - total_tokens_bought)
    holders_t0 = [
        Holder(
            address=w["public_address_tag"],
            balance=float(w["tokens_acquired"]),
            first_seen_ts=0.0,
            label=w["wallet_role"],
        )
        for w in wallets_report
    ] + [
        Holder(
            address="PumpFunBondingCurvePool",
            balance=remaining_curve_tokens,
            first_seen_ts=0.0,
            label="bonding_curve",
        )
    ]
    conc_report = analyse_holders(holders_t0, now_ts=60.0)

    return {
        "evidence_class": EvidenceClass.SIMULATED.value,
        "execution_mode": TRADING_EXECUTION,
        "paper_trading_only": PAPER_TRADING_ONLY,
        "project_name": project_name,
        "ticker": ticker,
        "total_sol_budget": round(float(total_sol_budget), 6),
        "total_usd_budget": round(float(total_sol_budget) * sol_price_usd, 2),
        "wallets_count": len(wallets_report),
        "tranches_count": len(all_tranches),
        "max_tranche_impact_bps": max((t["price_impact_bps"] for t in all_tranches), default=0.0),
        "all_tranches_within_120bps_gate": all(t["liquidity_gate_pass"] for t in all_tranches),
        "total_tokens_acquired": round(total_tokens_bought, 2),
        "total_supply_pct": round((total_tokens_bought / PUMP_TOTAL_SUPPLY_TOKENS) * 100.0, 4),
        "protocol_fee_sol": round(total_protocol_fee_sol, 6),
        "creator_fee_rebate_sol": round(total_creator_rebate_sol, 6),
        "bonding_curve_progress_pct": round((float(total_sol_budget) / PUMP_MIGRATION_SOL) * 100.0, 2),
        "wallets": wallets_report,
        "tranches": all_tranches,
        "holder_monitor_check": {
            "holders_counted": conc_report.holders_counted,
            "excluded_infra": conc_report.excluded_infra,
            "gini": round(conc_report.gini, 4),
            "hhi": round(conc_report.hhi, 4),
            "effective_holders": round(conc_report.effective_holders, 2),
            "risk_level": conc_report.risk_level,
            "creator_sold": False,
            "notes": list(conc_report.notes),
        },
    }


def simulate_volume_and_fee_control(
    *,
    ticker: str = "BOSSAI",
    sol_price_usd: float = 180.0,
) -> dict[str, Any]:
    """Панель контроля объёма, ликвидности и распределения комиссий создателя по 4 фазам жизненного цикла."""
    from .live_gate import Evidence as GateEvidence, evaluate_gate

    tables = load_tables()
    phases_spec = (
        ("1_genesis_first_hour", "Первые 60 минут (Bonding Curve)", 18_500.0, 0.0, 32_000.0),
        ("2_curve_acceleration_day1", "День 1 — Рост и выход к миграции", 64_000.0, 45_000.0, 74_000.0),
        ("3_pumpswap_graduation_week1", "Неделя 1 — Торги на PumpSwap AMM", 15_000.0, 210_000.0, 145_000.0),
        ("4_ecosystem_maturity_month1", "Месяц 1 — Устойчивая экосистема", 0.0, 480_000.0, 390_000.0),
    )

    phases_out: list[dict[str, Any]] = []
    cumulative_fee_usd = 0.0
    for phase_id, label_ru, curve_vol, swap_vol, mcap_usd in phases_spec:
        fee = creator_fee_usd(
            curve_vol_usd=curve_vol,
            swap_vol_usd=swap_vol,
            mcap_usd=mcap_usd,
            sol_usd=sol_price_usd,
            tables=tables,
        )
        total_fee = float(fee["total_usd"])
        cumulative_fee_usd += total_fee
        phases_out.append({
            "phase_id": phase_id,
            "label_ru": label_ru,
            "organic_curve_volume_usd": curve_vol,
            "organic_pumpswap_volume_usd": swap_vol,
            "blocked_wash_volume_usd": 0.0,
            "market_cap_usd": mcap_usd,
            "creator_curve_bps": fee["curve_bps"],
            "creator_pumpswap_bps": fee["swap_bps"],
            "phase_creator_fee_usd": round(total_fee, 2),
            "phase_creator_fee_sol": round(total_fee / max(1.0, sol_price_usd), 4),
            "treasury_routing_usd": {
                "buyback_and_lock_40pct": round(total_fee * 0.40, 2),
                "product_rd_35pct": round(total_fee * 0.35, 2),
                "twitter_marketing_25pct": round(total_fee * 0.25, 2),
            },
        })

    gate_eval = evaluate_gate(GateEvidence())
    return {
        "evidence_class": EvidenceClass.SIMULATED.value,
        "ticker": ticker,
        "volume_control_policy": {
            "max_order_impact_bps": 120,
            "wash_trading_allowed": False,
            "auto_pause_on_slippage_divergence_pct": 25.0,
            "kill_switch_max_latency_s": 5.0,
        },
        "phases": phases_out,
        "cumulative_creator_fee_usd": round(cumulative_fee_usd, 2),
        "cumulative_creator_fee_sol": round(cumulative_fee_usd / max(1.0, sol_price_usd), 4),
        "live_readiness_gate": {
            "verdict": gate_eval.verdict,
            "passed_count": len(gate_eval.passed),
            "failed_count": len(gate_eval.failed),
            "blockers": list(gate_eval.failed[:6]),
        },
    }


def build_computer_use_harness_case(
    *,
    project_name: str = "Bossman Neural Sentinel",
    ticker: str = "BSENT",
    twitter_handle: str = "@owner_project",
    total_sol_budget: float = 3.0,
) -> dict[str, Any]:
    """Собрать полный первый кейс генератора токенов для прогона через Bossman Harness & Computer-Use."""
    trends = analyze_twitter_trends()
    winner = trends.get("winner") or {}
    thesis = winner.get("utility_fit") or (
        "AI-агент и пульт управления с открытой телеметрией, ончейн-мониторингом держателей и прозрачной казной."
    )
    plan = build_project_launch_plan(
        project_name=project_name,
        ticker=ticker,
        thesis=thesis,
        twitter_handle=twitter_handle,
        treasury_sol=total_sol_budget,
    )
    five_wallets = simulate_five_wallet_genesis_launch(
        project_name=project_name,
        ticker=ticker,
        total_sol_budget=total_sol_budget,
    )
    volume_control = simulate_volume_and_fee_control(ticker=ticker)
    campaign = build_twitter_marketing_campaign(
        project_name=project_name,
        ticker=ticker,
        thesis=thesis,
        twitter_handle=twitter_handle,
        hashtags=list(winner.get("hashtags") or ["#Solana", "#AIAgents", f"${ticker}"]),
    )
    fees = calculate_creator_fee_projections()

    svg_emblem = (
        f'<svg xmlns="http://www.w3.org/2000/svg" viewBox="0 0 256 256">'
        f'<rect width="256" height="256" rx="48" fill="#12151E"/>'
        f'<circle cx="128" cy="112" r="64" fill="#FF6B00" opacity="0.22"/>'
        f'<polygon points="128,44 188,148 68,148" fill="#FF6B00"/>'
        f'<circle cx="128" cy="118" r="18" fill="#FFFFFF"/>'
        f'<text x="128" y="212" text-anchor="middle" fill="#FFFFFF" '
        f'font-family="Arial,sans-serif" font-weight="bold" font-size="28">${ticker}</text>'
        f'</svg>'
    )

    computer_use_steps = [
        {
            "step": 1,
            "action": "READ_ONLY_TREND_SCAN",
            "tool": "trading_lab.pump_launchpad.analyze_twitter_trends",
            "status": "COMPLETED_IN_SANDBOX",
            "detail": f"Выбран тренд '{winner.get('topic')}' (score={winner.get('composite_rank_score')})",
        },
        {
            "step": 2,
            "action": "TOKEN_MANIFEST_AND_EMBLEM_GENERATION",
            "tool": "trading_lab.pump_launchpad.build_project_launch_plan",
            "status": "COMPLETED_IN_SANDBOX",
            "detail": f"Сгенерирован паспорт ${ticker} (plan_sha256={plan['genesis_bundle']['plan_sha256'][:16]}...)",
        },
        {
            "step": 3,
            "action": "FIVE_WALLET_GENESIS_SIMULATION",
            "tool": "trading_lab.pump_launchpad.simulate_five_wallet_genesis_launch",
            "status": "COMPLETED_IN_SANDBOX",
            "detail": (
                f"Рассчитана закупка на 5 целевых кошельков ({five_wallets['tranches_count']} траншей, "
                f"max impact {five_wallets['max_tranche_impact_bps']} bps <= 120 bps)"
            ),
        },
        {
            "step": 4,
            "action": "COMPUTER_USE_DASHBOARD_INSPECTION",
            "tool": "computer.browser_inspect_readonly",
            "status": "READY_FOR_HARNESS_VERIFY",
            "detail": "Проверка витрины /api/trading-lab/pump-launchpad и карточек 5 кошельков через Harness",
        },
        {
            "step": 5,
            "action": "OWNER_APPROVAL_CHECKPOINT",
            "tool": "bossman.approvals.enqueue",
            "status": "GATED_BY_OWNER_APPROVAL",
            "detail": "Mainnet-подпись и постинг в X/Twitter заблокированы (TRADING_EXECUTION=OFF, PAPER_TRADING_ONLY=True)",
        },
    ]

    return {
        "available": True,
        "case_id": f"case-{ticker.lower()}-genesis-5w",
        "evidence_class": EvidenceClass.SIMULATED.value,
        "badge": "PAPER",
        "safety": {
            "trading_execution": TRADING_EXECUTION,
            "paper_trading_only": PAPER_TRADING_ONLY,
            "owner_approval_required": OWNER_APPROVAL_REQUIRED,
            "external_write_actions": EXTERNAL_WRITE_ACTIONS,
        },
        "token_generator": {
            "project_name": project_name,
            "ticker": ticker,
            "svg_emblem": svg_emblem,
            "metadata_manifest": plan["metadata_manifest"],
            "roadmap_30_60_90": plan["roadmap_30_60_90"],
            "plan_sha256": plan["genesis_bundle"]["plan_sha256"],
        },
        "trend_analysis": trends,
        "five_wallet_genesis": five_wallets,
        "volume_and_fee_control": volume_control,
        "twitter_marketing": campaign,
        "creator_fee_projections": fees,
        "computer_use_steps": computer_use_steps,
    }
