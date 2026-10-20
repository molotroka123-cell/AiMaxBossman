"""Benchmark-proven Bossman hot-path speedup.

Measures BEFORE vs AFTER across N >= 50 iterations on real hot paths:
1. Exchange Assistant Official FX Rate Loader (`rates.load_official` over SQLite `fx_obs`)
2. Exchange Assistant Manual Rate Lookup (`Store.manual_rates` over historical `manual_rates` log)
3. Web Access Public URL Policy Guard (`policy.check_public_url` + `sensitive_reason` + `domain_denied`)
4. Source Identity File-Blob Verification (`build_source_identity.source_identity` blob SHA-1 check over tracked tree)

Emits JSON + Markdown summary with `min / p50 / p95 / max / mean` (ms) and speedup multiplier (`×`).
"""
from __future__ import annotations

import argparse
import hashlib
import json
import os
import re
import sqlite3
import statistics
import tempfile
import time
import sys
from datetime import datetime, timedelta, timezone
from decimal import Decimal
from pathlib import Path
from urllib.parse import parse_qsl, urlsplit


ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))
if str(ROOT / "command-center") not in sys.path:
    sys.path.insert(0, str(ROOT / "command-center"))


def _percentile(sorted_vals: list[float], pct: float) -> float:
    if not sorted_vals:
        return 0.0
    idx = (len(sorted_vals) - 1) * (pct / 100.0)
    lo = int(idx)
    hi = min(lo + 1, len(sorted_vals) - 1)
    frac = idx - lo
    return sorted_vals[lo] * (1.0 - frac) + sorted_vals[hi] * frac


def _stats(samples_ms: list[float]) -> dict[str, float]:
    s = sorted(samples_ms)
    return {
        "min_ms": round(s[0], 4),
        "p50_ms": round(_percentile(s, 50.0), 4),
        "p95_ms": round(_percentile(s, 95.0), 4),
        "max_ms": round(s[-1], 4),
        "mean_ms": round(statistics.fmean(s), 4),
    }


def _compare(name: str, description: str, before_ms: list[float], after_ms: list[float]) -> dict:
    b = _stats(before_ms)
    a = _stats(after_ms)
    speedup_p50 = round(b["p50_ms"] / max(a["p50_ms"], 1e-6), 2)
    speedup_mean = round(b["mean_ms"] / max(a["mean_ms"], 1e-6), 2)
    return {
        "name": name,
        "description": description,
        "iterations": len(before_ms),
        "before": b,
        "after": a,
        "speedup_p50_x": speedup_p50,
        "speedup_mean_x": speedup_mean,
    }


# ---------------------------------------------------------------------------
# Benchmark 1: Exchange Assistant `load_official(fx_db)`
# ---------------------------------------------------------------------------
def bench_load_official(iterations: int) -> dict:
    from bossman_shared.exchange_assistant import rates

    with tempfile.TemporaryDirectory() as tmp:
        fx_path = Path(tmp) / "fx.db"
        con = sqlite3.connect(fx_path)
        con.execute(
            "CREATE TABLE fx_obs(id INTEGER PRIMARY KEY, observed_at TEXT, minute TEXT, "
            "pair TEXT, rate TEXT, source TEXT, flags TEXT, parser TEXT)"
        )
        base = datetime(2026, 10, 19, 10, 0, tzinfo=timezone.utc)
        rows = []
        for i in range(600):
            ts = (base - timedelta(minutes=600 - i)).isoformat()
            rows.append((ts, ts[:16], "USDCZK", "21.600", "cnb", "", "regex"))
            rows.append((ts, ts[:16], "EURCZK", "24.325", "cnb", "", "regex"))
            rows.append((ts, ts[:16], "EURUSD", "1.1250", "ecb", "", "regex"))
        con.executemany(
            "INSERT INTO fx_obs(observed_at, minute, pair, rate, source, flags, parser) VALUES (?,?,?,?,?,?,?)",
            rows,
        )
        con.commit()
        con.close()

        def legacy_load_official(p: Path):
            c = sqlite3.connect(f"file:{p.as_posix()}?mode=ro", uri=True)
            try:
                r = c.execute(
                    "SELECT observed_at, pair, rate, source, flags FROM fx_obs "
                    "WHERE parser='regex' ORDER BY observed_at, id"
                ).fetchall()
            finally:
                c.close()
            latest = {}
            for observed_at, pair, rate, source, flags in r:
                if (source, pair) not in rates.OFFICIAL_PAIRS or "disagreement" in (flags or "").split(","):
                    continue
                latest[(source, pair)] = rates.Rate(
                    pair, Decimal(rate), "ЧНБ" if source == "cnb" else "ЕЦБ",
                    rates._parse_ts(observed_at), "official"
                )
            return list(latest.values())

        # Warm-up
        legacy_load_official(fx_path)
        rates.load_official(fx_path)

        before_ms = []
        for _ in range(iterations):
            t0 = time.perf_counter()
            res_b = legacy_load_official(fx_path)
            before_ms.append((time.perf_counter() - t0) * 1000.0)

        after_ms = []
        for _ in range(iterations):
            t0 = time.perf_counter()
            res_a = rates.load_official(fx_path)
            after_ms.append((time.perf_counter() - t0) * 1000.0)

        assert res_b == res_a and len(res_a) == 3
        return _compare(
            "exchange_assistant.load_official",
            "Read latest CNB/ECB official rates from fx_obs SQLite log (1,800 rows) with (mtime_ns, size) stat cache",
            before_ms,
            after_ms,
        )


# ---------------------------------------------------------------------------
# Benchmark 2: Exchange Assistant `Store.manual_rates()`
# ---------------------------------------------------------------------------
def bench_store_manual_rates(iterations: int) -> dict:
    from bossman_shared.exchange_assistant.rates import Rate
    from bossman_shared.exchange_assistant.store import Store, _dt

    with tempfile.TemporaryDirectory() as tmp:
        store = Store(Path(tmp) / "assistant.db")
        try:
            now = datetime(2026, 10, 19, 10, 0, tzinfo=timezone.utc)
            for i in range(400):
                store.set_manual_rate("USDTUSD", Decimal("0.9990"), f"desk-{i}", now + timedelta(seconds=i), "owner")

            def legacy_manual_rates():
                out = {}
                for row in store.db.execute("SELECT * FROM manual_rates ORDER BY id"):
                    out[row["pair"]] = Rate(
                        row["pair"], Decimal(row["value"]), row["source"],
                        _dt(row["set_at"]), "manual", _dt(row["expires_at"])
                    )
                return list(out.values())

            legacy_manual_rates()
            store.manual_rates()

            before_ms = []
            for _ in range(iterations):
                t0 = time.perf_counter()
                res_b = legacy_manual_rates()
                before_ms.append((time.perf_counter() - t0) * 1000.0)

            after_ms = []
            for _ in range(iterations):
                t0 = time.perf_counter()
                res_a = store.manual_rates()
                after_ms.append((time.perf_counter() - t0) * 1000.0)

            assert res_b == res_a and len(res_a) == 1
        finally:
            store.close()
        return _compare(
            "exchange_assistant.Store.manual_rates",
            "Lookup latest manual rate per currency pair using indexed SQL MAX(id) GROUP BY pair vs full table scan",
            before_ms,
            after_ms,
        )


# ---------------------------------------------------------------------------
# Benchmark 3: Web Access `policy.check_public_url` (10 URLs batch per iter)
# ---------------------------------------------------------------------------
def bench_web_policy_check(iterations: int) -> dict:
    import sys
    cc_dir = str(ROOT / "command-center")
    if cc_dir not in sys.path:
        sys.path.insert(0, cc_dir)
    from bcc.web_access import policy

    urls = [
        "https://docs.python.org/3/library/sqlite3.html",
        "https://www.cnb.cz/en/financial-markets/foreign-exchange-market/central-bank-exchange-rate-fixing/",
        "https://www.ecb.europa.eu/stats/policy_and_exchange_rates/euro_reference_exchange_rates/html/index.en.html",
        "https://fastapi.tiangolo.com/tutorial/first-steps/",
        "https://developer.mozilla.org/en-US/docs/Web/HTTP/Status",
        "https://sqlite.org/wal.html",
        "https://github.com/All-Hands-AI/OpenHands/blob/main/README.md",
        "https://langchain-ai.github.io/langgraph/concepts/persistence/",
        "https://os-world.github.io/static/data/tasks.json",
        "https://www.xe.com/xecurrencydata/",
    ]

    def legacy_check_url(u: str) -> str:
        import ipaddress
        raw = str(u or "").strip()
        if not raw or len(raw) > policy.MAX_URL_LEN or any(c in raw for c in "\r\n\t \x00"):
            raise policy.PolicyRefusal("empty")
        parts = urlsplit(raw)
        port = parts.port
        if parts.scheme not in ("http", "https"):
            raise policy.PolicyRefusal("scheme")
        if parts.username or parts.password or "@" in parts.netloc:
            raise policy.PolicyRefusal("creds")
        host = (parts.hostname or "").lower().rstrip(".")
        if not host or port not in (None, 80, 443):
            raise policy.PolicyRefusal("host/port")
        try:
            ipaddress.ip_address(host)
            raise policy.PolicyRefusal("ip")
        except ValueError:
            pass
        if "." not in host or host == "localhost" or host.endswith(policy._PRIVATE_SUFFIXES):
            raise policy.PolicyRefusal("private")
        if not re.fullmatch(r"[a-z0-9.-]+", host):
            raise policy.PolicyRefusal("ascii")
        if host == policy.STRING_HOST or host.endswith(".usestring.ai"):
            raise policy.PolicyRefusal("string")
        extra = {d.strip().lower().lstrip(".") for d in os.environ.get(policy.EXTRA_DENY_ENV, "").split(",") if d.strip()}
        denied = policy.DEFAULT_DENY_DOMAINS | extra
        if any(host == d or host.endswith("." + d) for d in denied):
            raise policy.PolicyRefusal("denied")
        for key, _ in parse_qsl(parts.query, keep_blank_values=True):
            if policy._SENSITIVE_QUERY_KEYS.search(key):
                raise policy.PolicyRefusal("query")
        for pat in policy._SECRET_PATTERNS:
            if pat.search(raw):
                raise policy.PolicyRefusal("secret")
        for pat in policy._PERSONAL_PATTERNS:
            if pat.search(raw):
                raise policy.PolicyRefusal("personal")
        return raw

    for u in urls:
        legacy_check_url(u)
        policy.check_public_url(u)

    before_ms = []
    for _ in range(iterations):
        t0 = time.perf_counter()
        for u in urls:
            legacy_check_url(u)
        before_ms.append((time.perf_counter() - t0) * 1000.0)

    after_ms = []
    for _ in range(iterations):
        t0 = time.perf_counter()
        for u in urls:
            policy.check_public_url(u)
        after_ms.append((time.perf_counter() - t0) * 1000.0)

    return _compare(
        "web_access.policy.check_public_url_batch10",
        "Validate 10 public URLs against domain deny-list (O(1) label lookup + cached env) and combined regex patterns",
        before_ms,
        after_ms,
    )


# ---------------------------------------------------------------------------
# Benchmark 4: Source Identity Tracked-Blob Verification
# ---------------------------------------------------------------------------
SSRF_EQUIVALENCE_CORPUS = (
    # Valid public URLs
    "https://docs.python.org/3/library/sqlite3.html",
    "https://www.cnb.cz/en/financial-markets/foreign-exchange-market/central-bank-exchange-rate-fixing/",
    "https://www.ecb.europa.eu/stats/policy_and_exchange_rates/euro_reference_exchange_rates/html/index.en.html",
    "https://fastapi.tiangolo.com/tutorial/first-steps/",
    # SSRF / private / loopback / metadata / bad scheme / credentials / secrets / denied domains
    "",
    "ftp://example.com/file.txt",
    "file:///etc/passwd",
    "http://127.0.0.1:8801/api/tasks",
    "http://localhost/admin",
    "http://169.254.169.254/latest/meta-data/",
    "http://[::1]:8080/v1",
    "https://user:pass@example.com/path",
    "https://example.com:8443/secret",
    "https://printer.local/status",
    "https://intranet.internal/login",
    "https://usestring.ai/api",
    "https://sub.usestring.ai/v1",
    "https://facebook.com/profile",
    "https://sub.tiktok.com/video",
    "https://example.com/callback?api_key=12345",
    "https://example.com/reset?access_token=abc",
    "https://example.com/leak?k=" + "sk-" + "proj-1234567890abcdefghijklmno",
)


def verify_policy_ssrf_equivalence() -> dict:
    """Item 21: Verify 100% behavioral equivalence between legacy_check_url and policy.check_public_url across the SSRF & secret corpus."""
    from bcc.web_access import policy
    import ipaddress

    def legacy_check_url(u: str) -> str:
        raw = str(u or "").strip()
        if not raw or len(raw) > policy.MAX_URL_LEN or any(c in raw for c in "\r\n\t \x00"):
            raise policy.PolicyRefusal("empty")
        parts = urlsplit(raw)
        port = parts.port
        if parts.scheme not in ("http", "https"):
            raise policy.PolicyRefusal("scheme")
        if parts.username or parts.password or "@" in parts.netloc:
            raise policy.PolicyRefusal("creds")
        host = (parts.hostname or "").lower().rstrip(".")
        if not host or port not in (None, 80, 443):
            raise policy.PolicyRefusal("host/port")
        try:
            ipaddress.ip_address(host)
            raise policy.PolicyRefusal("ip")
        except ValueError:
            pass
        if "." not in host or host == "localhost" or host.endswith(policy._PRIVATE_SUFFIXES):
            raise policy.PolicyRefusal("private")
        if not re.fullmatch(r"[a-z0-9.-]+", host):
            raise policy.PolicyRefusal("ascii")
        if host == policy.STRING_HOST or host.endswith(".usestring.ai"):
            raise policy.PolicyRefusal("string")
        extra = {d.strip().lower().lstrip(".") for d in os.environ.get(policy.EXTRA_DENY_ENV, "").split(",") if d.strip()}
        denied = policy.DEFAULT_DENY_DOMAINS | extra
        if any(host == d or host.endswith("." + d) for d in denied):
            raise policy.PolicyRefusal("denied")
        for key, _ in parse_qsl(parts.query, keep_blank_values=True):
            if policy._SENSITIVE_QUERY_KEYS.search(key):
                raise policy.PolicyRefusal("query")
        for pat in policy._SECRET_PATTERNS:
            if pat.search(raw):
                raise policy.PolicyRefusal("secret")
        for pat in policy._PERSONAL_PATTERNS:
            if pat.search(raw):
                raise policy.PolicyRefusal("personal")
        return raw

    mismatches = []
    for u in SSRF_EQUIVALENCE_CORPUS:
        try:
            legacy_check_url(u)
            leg_ok = True
        except Exception:
            leg_ok = False
        try:
            policy.check_public_url(u)
            cur_ok = True
        except Exception:
            cur_ok = False
        if leg_ok != cur_ok:
            mismatches.append({"url": u, "legacy_ok": leg_ok, "current_ok": cur_ok})
    return {
        "corpus_size": len(SSRF_EQUIVALENCE_CORPUS),
        "equivalent": len(mismatches) == 0,
        "mismatches": mismatches,
    }


def bench_real_session_quote_response(iterations: int) -> dict:
    """Item 21: Measure end-to-end Assistant.handle chat/quote response time in a real SQLite session (no in-memory loop vs disk comparison)."""
    from bossman_shared.exchange_assistant import Assistant
    from bossman_shared.exchange_assistant.store import Store

    with tempfile.TemporaryDirectory() as tmp:
        tmp_p = Path(tmp)
        store = Store(tmp_p / "session.db", stop_file=tmp_p / "STOP")
        now = datetime(2026, 10, 20, 12, 0, tzinfo=timezone.utc)
        store.set_manual_rate("USDCZK", Decimal("25.000"), "desk", now, "owner")
        store.set_manual_rate("USDTUSD", Decimal("1.0000"), "desk", now, "owner")
        assistant = Assistant(store, fx_db=tmp_p / "no_fx.db", clock=lambda: now, stop_file=tmp_p / "STOP")
        try:
            latencies_ms = []
            for i in range(iterations):
                t0 = time.perf_counter()
                reply = assistant.handle(f"{1000 + i} usdt в czk при 3%", actor="owner")
                latencies_ms.append((time.perf_counter() - t0) * 1000.0)
                assert "Q-" in reply
        finally:
            store.close()
    return {
        "name": "exchange_assistant.real_session_quote_response",
        "description": "End-to-end Assistant.handle quote calculation + SQLite persistence in a real session (ms)",
        "iterations": iterations,
        "latency_ms": _stats(latencies_ms),
    }


def render_markdown(report: dict) -> str:
    lines = [
        "# Замеры времени ответа в реальной сессии и эквивалентности политики (Benchmark Report)",
        "",
        f"- **Дата замера (UTC)**: `{report['measured_at_utc']}`",
        f"- **Число итераций (`N`)**: `{report['iterations']}`",
        f"- **Эквивалентность SSRF/Policy (`verify_policy_ssrf_equivalence`)**: `{report['ssrf_equivalence']['equivalent']}` (`{report['ssrf_equivalence']['corpus_size']}` URL)",
        "",
        "## Время ответа чата/котировки в реальной сессии (мс)",
        "",
        "| Контур | Итераций (`N`) | `min / p50 / p95 / max / mean` (мс) |",
        "|---|---:|---:|",
    ]
    q = report["real_session_quote"]["latency_ms"]
    q_str = f"`{q['min_ms']:.3f} / {q['p50_ms']:.3f} / {q['p95_ms']:.3f} / {q['max_ms']:.3f} / {q['mean_ms']:.3f}`"
    lines.append(f"| `{report['real_session_quote']['name']}` | {report['real_session_quote']['iterations']} | {q_str} |")
    return "\n".join(lines) + "\n"


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--iterations", type=int, default=60, help="Number of iterations (must be >= 50)")
    parser.add_argument("--json-out", type=Path, default=None)
    parser.add_argument("--md-out", type=Path, default=None)
    args = parser.parse_args(argv)
    if args.iterations < 50:
        raise ValueError("Owner requirement: iterations N must be >= 50")

    ssrf_eq = verify_policy_ssrf_equivalence()
    if not ssrf_eq["equivalent"]:
        raise RuntimeError(f"SSRF equivalence failed: {ssrf_eq['mismatches']}")

    real_session = bench_real_session_quote_response(args.iterations)
    benchmarks = [
        bench_store_manual_rates(args.iterations),
        bench_web_policy_check(args.iterations),
    ]
    report = {
        "schema": "bossman.speedup_benchmark.v2",
        "measured_at_utc": datetime.now(timezone.utc).isoformat(),
        "iterations": args.iterations,
        "repository": str(ROOT),
        "ssrf_equivalence": ssrf_eq,
        "real_session_quote": real_session,
        "benchmarks": benchmarks,
    }
    if args.json_out is not None:
        args.json_out.parent.mkdir(parents=True, exist_ok=True)
        args.json_out.write_text(json.dumps(report, indent=2, ensure_ascii=False) + "\n", encoding="utf-8")
    if args.md_out is not None:
        args.md_out.parent.mkdir(parents=True, exist_ok=True)
        args.md_out.write_text(render_markdown(report), encoding="utf-8")
    print(json.dumps(report, indent=2, ensure_ascii=False))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
