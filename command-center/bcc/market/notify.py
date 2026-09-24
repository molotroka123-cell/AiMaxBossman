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
                             "Объясни на русском одной короткой фразой только заданный режим и причины. "
                             "Не называй чисел, процентов, целей, вероятностей и торговых приказов. "
                             "Если данных недостаточно, скажи об этом."},
                            {"role": "user", "content": json.dumps({
                                "regime": analysis["regime"], "matrix": analysis["matrix"],
                                "reasons": analysis["classification_detail"]["reasons"],
                                "missing_data": analysis["missing_data"]}, ensure_ascii=False)}]}
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
    def value(v, scale=1):
        return "UNKNOWN" if v is None else f"{v / scale:,.2f}"
    d = a["classification_detail"]
    case = a["case_matches"][0] if a["case_matches"] else None
    return (f"BTC\nЦена: {value(a['price'])} (Δ {value(a['delta_price'])})\n"
            f"CVD: {value(a['cvd'], 1e9)}B (Δ {value(a['delta_cvd'], 1e9)}B)\n"
            f"OI: {value(a['oi'], 1e9)}B (Δ {value(a['delta_oi'], 1e9)}B)\n\n"
            f"STATE: {a['matrix']}\nРЕЖИМ: {a['regime']}\nПОЧЕМУ: {why}\n\n"
            f"УРОВНИ: {a['levels'] or 'UNKNOWN'}\n\n"
            f"LONG: {a['long_scenario']['trigger']} → {a['long_scenario']['confirmation']} → {a['long_scenario']['invalidation']}\n"
            f"BEAR: {a['bear_scenario']['trigger']} → {a['bear_scenario']['confirmation']} → {a['bear_scenario']['invalidation']}\n\n"
            f"ПОХОЖИЙ CASE: {case['case_id'] + ': ' + case['lesson'] if case else 'UNKNOWN'}\n"
            f"DATA QUALITY: missing={', '.join(a['missing_data']) or 'none'}; "
            f"classification confidence={a['classification_confidence']:.2f} (не вероятность прибыли)")


class MarketNotifier:
    def __init__(self, root: Path):
        self.state_path = root / "reports" / "notification-state.json"
        self.state_path.parent.mkdir(parents=True, exist_ok=True)
        self.state = json.loads(self.state_path.read_text(encoding="utf-8")) if self.state_path.exists() else {}

    async def process(self, ledger: Ledger, rec: dict, *, verbose: bool = False) -> dict:
        verbose = verbose or (ledger.root / "VERBOSE_NOTIFICATIONS").exists()
        status = rec["quality"]["status"]
        analysis = latest_analysis(ledger) if status == "VERIFIED" else None
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
        elif status != self.state.get("last_quality"):
            fingerprint = "quality:" + status
        now = time.time()
        if not fingerprint or (fingerprint == self.state.get("fingerprint")
                               and now - self.state.get("sent_at", 0) < 900):
            self._save()
            return {"sent": False, "reason": "unchanged_or_cooldown"}
        if analysis:
            try:
                why = await asyncio.to_thread(explain_local, analysis)
            except Exception:
                why = "; ".join(analysis["classification_detail"]["reasons"][1:3]) or "UNKNOWN"
            message = format_message(analysis, why)
        else:
            message = f"BTC DATA QUALITY: {status}; значения UNKNOWN до нового VERIFIED кадра."
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
        self.state.update(fingerprint=fingerprint, sent_at=now, last_quality=status)
        if analysis:
            self.state["last_regime"] = analysis["regime"]
        self._save()
        return {"sent": True, "message_id": message_id, "at": rec["captured_at_utc"]}

    def _save(self):
        tmp = self.state_path.with_suffix(".tmp")
        tmp.write_text(json.dumps(self.state, ensure_ascii=False), encoding="utf-8")
        os.replace(tmp, self.state_path)
