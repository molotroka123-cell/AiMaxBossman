"""Owner-only market messages through the existing Telegram Companion transport."""
from __future__ import annotations

import asyncio
import json
import os
import re
import time
import urllib.request
from pathlib import Path

from .analyzer import latest_analysis
from .extract import _is_loopback
from .ledger import Ledger


def explain_local(analysis: dict, *, url: str = "http://127.0.0.1:11434",
                  model: str | None = None) -> str:
    """The model supplies only prose; all numbers and labels remain deterministic."""
    if not _is_loopback(url):
        raise ValueError("local explanation endpoint must be loopback")
    model = model or os.environ.get("BOSSMAN_MARKET_TEXT_MODEL", "bossman-main-qwen38-27b-q5:latest")
    payload = {"model": model, "stream": False, "think": False,
               "options": {"temperature": 0, "num_predict": 100},
               "messages": [{"role": "system", "content":
                             "Ты краткий order-flow комментатор Bossman. Объясни по-русски в 1-2 простых "
                             "предложениях только то, что следует из Price/CVD/OI и причин классификатора. "
                             "Не называй отсутствующие уровни, CASE или служебные поля; это не причина режима. "
                             "Не придумывай числа, проценты, цели, вероятности, позиции или торговые приказы. "
                             "Для NEUTRAL_BALANCE скажи, что существенного изменения пока нет, без воды про шум."},
                            {"role": "user", "content": json.dumps({
                                "regime": analysis["regime"], "matrix": analysis["matrix"],
                                "reasons": analysis["classification_detail"]["reasons"]}, ensure_ascii=False)}]}
    request = urllib.request.Request(url.rstrip("/") + "/api/chat", data=json.dumps(payload).encode(),
                                     headers={"Content-Type": "application/json"})
    with urllib.request.urlopen(request, timeout=60) as response:
        result = json.load(response)
    prose = str((result.get("message") or {}).get("content") or "").strip()
    # Reject model text that could smuggle a new market value or a forecast.
    if not prose or len(prose) > 400 or re.search(
            r"[\d%$₽]|вероятност|шанс|гарант|процент|тысяч|миллион|миллиард|доллар|таргет|цель|стоп",
            prose, re.I):
        return "; ".join(analysis["classification_detail"]["reasons"][1:3]) or "Данных недостаточно."
    return prose


def format_message(a: dict, why: str) -> str:
    """Compact owner message: show evidence, omit empty placeholders."""
    def value(v, scale=1):
        return "UNKNOWN" if v is None else f"{v / scale:,.2f}"

    def delta(v, *, scale=1, suffix=""):
        if v is None:
            return "UNKNOWN"
        scaled = v / scale
        # Do not print a real non-zero move as 0.00 after rounding.
        if scaled != 0 and abs(scaled) < 0.01:
            return f"{'+' if scaled > 0 else '-'}<0.01{suffix}"
        return f"{scaled:+,.2f}{suffix}"

    d = a["classification_detail"]
    case = a["case_matches"][0] if a["case_matches"] else None
    lines = [
        "BTC",
        f"Цена: {value(a['price'])} ({delta(a['delta_price'])})",
        f"CVD: {value(a['cvd'], 1e9)}B ({delta(a['delta_cvd'], scale=1e9, suffix='B')})",
        f"OI: {value(a['oi'], 1e9)}B ({delta(a['delta_oi'], scale=1e9, suffix='B')})",
        "",
        f"STATE: {a['matrix']}",
        f"РЕЖИМ: {a['regime']}",
        f"ПОЧЕМУ: {why}",
    ]

    # Empty optional context is omitted instead of filling the phone with UNKNOWN.
    if a.get("levels"):
        lines += ["", f"УРОВНИ: {a['levels']}"]
        long = a.get("long_scenario") or {}
        bear = a.get("bear_scenario") or {}
        if all(long.get(k) not in (None, "UNKNOWN") for k in ("trigger", "confirmation", "invalidation")):
            lines.append(f"LONG: {long['trigger']} → {long['confirmation']} → {long['invalidation']}")
        if all(bear.get(k) not in (None, "UNKNOWN") for k in ("trigger", "confirmation", "invalidation")):
            lines.append(f"BEAR: {bear['trigger']} → {bear['confirmation']} → {bear['invalidation']}")

    if case:
        lines += ["", f"ПОХОЖИЙ CASE: {case['case_id']}: {case['lesson']}"]

    # Confidence is internal classification metadata, not a trading probability.
    # Keep Telegram owner output focused on observed evidence.
    lines += ["", "ДАННЫЕ: VERIFIED"]
    return "\n".join(lines)


def observation_quality(rec: dict) -> tuple[str, list[str]]:
    status = rec["quality"]["status"]
    if status != "VERIFIED":
        return status, []
    per_metric = rec["quality"].get("per_metric", {})
    metrics = rec["metrics"]
    missing = [name for name, key in (("price", "price"), ("cvd", "cvd_value"), ("oi", "oi_value"))
               if per_metric.get(name, {}).get("status") != "VERIFIED" or metrics.get(key) is None]
    return ("PARTIAL_VERIFIED" if missing else "VERIFIED"), missing


class MarketNotifier:
    def __init__(self, root: Path):
        self.state_path = root / "reports" / "notification-state.json"
        self.state_path.parent.mkdir(parents=True, exist_ok=True)
        self.state = json.loads(self.state_path.read_text(encoding="utf-8")) if self.state_path.exists() else {}

    async def process(self, ledger: Ledger, rec: dict, *, verbose: bool = False) -> dict:
        verbose = verbose or (ledger.root / "VERBOSE_NOTIFICATIONS").exists()
        quality_state, missing = observation_quality(rec)
        analysis = latest_analysis(ledger) if quality_state == "VERIFIED" else None
        fingerprint = None
        if analysis and analysis["timestamp"] == rec["captured_at_utc"]:
            detail = analysis["classification_detail"]
            anomaly = any(abs(analysis[k]) / max(abs(analysis[v]), 1) > 0.02
                          for k, v in (("delta_cvd", "cvd"), ("delta_oi", "oi")))
            if verbose or anomaly or detail["reclaimed_levels"] or detail["lost_levels"] or (
                    analysis["regime"] != self.state.get("last_regime")):
                fingerprint = json.dumps([analysis["regime"], detail["reclaimed_levels"],
                                          detail["lost_levels"], anomaly], sort_keys=True)
            if self.state.get("last_quality") not in (None, "VERIFIED"):
                fingerprint = "quality_recovered:" + fingerprint if fingerprint else "quality_recovered"
        elif quality_state != self.state.get("last_quality"):
            fingerprint = "quality:" + quality_state
        now = time.time()
        recent = self.state.get("recent_fingerprints", {})
        if not isinstance(recent, dict):
            recent = {}
        recent = {key: at for key, at in recent.items()
                  if isinstance(at, (int, float)) and now - at < 900}
        self.state["recent_fingerprints"] = recent
        if not fingerprint or (not verbose and (
                now - recent.get(fingerprint, 0) < 900 or
                (fingerprint == self.state.get("fingerprint")
                 and now - self.state.get("sent_at", 0) < 900))):
            self._save()
            return {"sent": False, "reason": "unchanged_or_cooldown"}
        if analysis:
            try:
                why = await asyncio.to_thread(explain_local, analysis)
            except Exception:
                why = "; ".join(analysis["classification_detail"]["reasons"][1:3]) or "UNKNOWN"
            message = format_message(analysis, why)
        else:
            if quality_state == "PARTIAL_VERIFIED":
                message = ("BTC DATA QUALITY: PARTIAL_VERIFIED; недостаёт " + ", ".join(missing)
                           + "; анализ ожидает полный свежий кадр.")
            else:
                message = f"BTC DATA QUALITY: {quality_state}; значения UNKNOWN до нового VERIFIED кадра."
        from bcc.telegram_companion.__main__ import default_config
        from bcc.telegram_companion.config import load
        from bcc.telegram_companion.adapters import Telegram
        config = default_config()
        if not config.exists():
            return {"sent": False, "reason": "companion_not_configured"}
        settings = load(config, env_file=config.parent / "companion.env")
        owner = next((p for p in settings.people if p.role == "owner"), None)
        if not settings.enabled or owner is None:
            return {"sent": False, "reason": "owner_delivery_disabled"}
        if not settings.bot_token:
            return {"sent": False, "reason": "companion_bot_token_missing"}
        tg = Telegram(settings)
        try:
            message_id = await tg.send(owner, message)
        finally:
            await tg.close()
        self.state.update(fingerprint=fingerprint, sent_at=now, last_quality=quality_state)
        recent[fingerprint] = now
        if analysis:
            self.state["last_regime"] = analysis["regime"]
        self._save()
        return {"sent": True, "message_id": message_id, "at": rec["captured_at_utc"]}

    def _save(self):
        tmp = self.state_path.with_suffix(".tmp")
        tmp.write_text(json.dumps(self.state, ensure_ascii=False), encoding="utf-8")
        os.replace(tmp, self.state_path)
