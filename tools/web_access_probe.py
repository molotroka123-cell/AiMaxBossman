#!/usr/bin/env python3
"""Проба веб-доступа Bossman: поиск, чтение страниц, браузер, SSRF, готовность.

Запускается на ЛЮБОМ экземпляре по адресу и токену; только stdlib (urllib).
Токен берётся из --token-file, переменной BCC_TOKEN или --token и НЕ печатается.

  python tools/web_access_probe.py --base http://127.0.0.1:8843 --token-file <путь к файлу token>
  python tools/web_access_probe.py --base ... --token-file ... --active --out probe.md

По умолчанию проба ТОЛЬКО ЧИТАЕТ (GET статусов): её безопасно направлять на живой
экземпляр владельца. С --active она ещё и действует на экземпляре: делает поисковые
запросы (след в OSIRIS и суточный счётчик) и открывает сессию браузера (строка в БД);
в интернет ходит сам экземпляр. Ничего, кроме отчёта (--out), проба на диск не пишет.

Вердикты: PASS / PARTIAL / BLOCKED / OWNER_REQUIRED / NOT_RUN / FAIL.
  FAIL — нарушен контроль (например, адрес metadata принят) или ответ противоречит
  заявленному. Код возврата: 0 — FAIL нет, 1 — есть хотя бы один FAIL, 2 — не
  удалось связаться с экземпляром.
"""
from __future__ import annotations

import argparse
import json
import os
import sys
import urllib.error
import urllib.request
from dataclasses import dataclass, field
from typing import Any

PASS, PARTIAL, BLOCKED, OWNER, NOT_RUN, FAIL = (
    "PASS", "PARTIAL", "BLOCKED", "OWNER_REQUIRED", "NOT_RUN", "FAIL")

# Публичные страницы-мишени. Устойчивые тестовые сайты; недоступность любого из них
# даёт NOT_RUN, а не FAIL: проба измеряет Bossman, а не чужой аптайм.
EXAMPLE = "https://example.com/"
LONG_ARTICLE = "https://en.wikipedia.org/wiki/Python_(programming_language)"
JS_PAGE = "https://quotes.toscrape.com/js/"
LATE_PAGE = "https://quotes.toscrape.com/scroll"
ERROR_404 = "https://httpbin.org/status/404"
REDIRECTS = "https://httpbin.org/redirect/3"
BAD_TARGETS = (
    ("loopback", "http://127.0.0.1:8801/api/health"),
    ("localhost", "http://localhost/"),
    ("metadata", "http://169.254.169.254/latest/meta-data/"),
    ("десятичный IP", "http://2130706433/"),
    ("file", "file:///C:/Windows/win.ini"),
    ("имя -> loopback", "http://localtest.me/"),
)


@dataclass
class Result:
    cap: str
    verdict: str
    detail: str
    evidence: str = ""


@dataclass
class Probe:
    base: str
    token: str
    timeout: float = 90.0
    results: list[Result] = field(default_factory=list)

    # ------------------------------------------------------------ транспорт
    def call(self, method: str, path: str, body: Any = None, timeout: float | None = None):
        """(статус, JSON|текст|None). Сетевой отказ — (0, строка причины)."""
        data = None if body is None else json.dumps(body, ensure_ascii=False).encode("utf-8")
        req = urllib.request.Request(self.base.rstrip("/") + path, data=data, method=method)
        req.add_header("X-BCC-Token", self.token)
        if data is not None:
            req.add_header("Content-Type", "application/json; charset=utf-8")
        try:
            with urllib.request.urlopen(req, timeout=timeout or self.timeout) as resp:
                raw = resp.read()
                return resp.status, self._decode(raw)
        except urllib.error.HTTPError as exc:
            return exc.code, self._decode(exc.read())
        except (urllib.error.URLError, OSError, TimeoutError) as exc:
            return 0, f"{type(exc).__name__}: {exc}".replace(self.token, "***") if self.token else str(exc)

    @staticmethod
    def _decode(raw: bytes) -> Any:
        try:
            return json.loads(raw.decode("utf-8"))
        except (ValueError, UnicodeDecodeError):
            return raw.decode("utf-8", "replace")[:400]

    def add(self, cap: str, verdict: str, detail: str, evidence: str = "") -> None:
        self.results.append(Result(cap, verdict, detail, evidence))

    # ---------------------------------------------------------------- пробы
    def status_checks(self) -> bool:
        code, web = self.call("GET", "/api/web", timeout=20)
        if code == 0:
            self.add("связь", FAIL, f"экземпляр не отвечает: {web}")
            return False
        if code == 401:
            self.add("связь", FAIL, "токен отвергнут (401)")
            return False
        if code != 200 or not isinstance(web, dict):
            self.add("web.feature", NOT_RUN, f"GET /api/web -> {code}: нет такой ручки (старая сборка?)")
        else:
            ready = web.get("readiness") or {}
            flags = web.get("flags") or {}
            if not web.get("enabled"):
                self.add("web.feature", BLOCKED,
                         "инструменты web.* не существуют: выключен BOSSMAN_WEB_RESEARCH_ENABLED"
                         + ("" if flags.get("osiris_enabled") else " и BOSSMAN_OSIRIS_ENABLED")
                         + "; включить = задать оба флага и перезапустить",
                         f"GET /api/web -> enabled=false code={ready.get('code')}")
            else:
                self.add("web.feature", PASS, f"включена, готовность: {ready.get('code')}; источников без ключа: "
                         f"{ready.get('keyless_ready')}", f"backends_ready={ready.get('backends_ready')}")
            general = ready.get("general_web") or []
            if general:
                self.add("web.general_search", PASS, "общий веб-поиск настроен: " + ", ".join(general),
                         "readiness.general_web")
            else:
                self.add("web.general_search", OWNER,
                         "общего веб-поиска нет: нужен свой SearXNG (BOSSMAN_WEB_SEARXNG_URL=http://127.0.0.1:8888, "
                         "search.formats: [html, json]); ключ Brave в этой сборке принять некуда",
                         f"searxng_configured={ready.get('searxng_configured')}")
        code, health = self.call("GET", "/api/browser/health", timeout=20)
        if code == 200 and isinstance(health, dict):
            self.add("browser.runtime", PASS if health.get("available") else BLOCKED,
                     str(health.get("detail") or "")[:160], f"available={health.get('available')}")
        else:
            self.add("browser.runtime", NOT_RUN, f"GET /api/browser/health -> {code}")
        code, caps = self.call("GET", "/api/capabilities", timeout=30)
        if code == 200 and isinstance(caps, dict):
            names = {c.get("tool"): c for c in caps.get("capabilities", []) if isinstance(c, dict)}
            web_tools = sorted(n for n in names if str(n).startswith("web."))
            br_tools = sorted(n for n in names if str(n).startswith("browser."))
            self.add("agents.web_tools", PASS if web_tools else BLOCKED,
                     "инструменты агентов задач: " + (", ".join(web_tools) or "web.* НЕТ в реестре"),
                     f"/api/capabilities: {len(web_tools)} web.*")
            self.add("agents.browser_tools", PASS if br_tools else BLOCKED,
                     f"{len(br_tools)} инструментов browser.*", "/api/capabilities")
        code, uni = self.call("GET", "/api/search?q=test", timeout=20)
        if code == 200 and isinstance(uni, dict):
            self.add("unified_search", PASS if uni.get("enabled") else NOT_RUN,
                     "это поиск по ВНУТРЕННИМ таблицам (события/задачи/подтверждения), а не по вебу; "
                     + ("включён" if uni.get("enabled") else "выключен BOSSMAN_UNIFIED_SEARCH_ENABLED"),
                     "GET /api/search")
        return True

    def search_checks(self, web_on: bool) -> None:
        if not web_on:
            for cap in ("search.en", "search.ru", "search.question", "search.query_guard"):
                self.add(cap, NOT_RUN, "web_research выключен на экземпляре")
            return

        def one(q: str, site: str = ""):
            return self.call("POST", "/api/web/search", {"query": q, "site": site, "fresh": True}, timeout=60)

        def verdict_of(cap: str, q: str, want_hits: bool = True) -> None:
            code, body = one(q)
            if code == 409:
                self.add(cap, BLOCKED, f"409: {body}")
            elif code == 0 or code >= 500:
                self.add(cap, NOT_RUN, f"{q!r}: сеть/источник: {code} {str(body)[:120]}")
            elif code != 200 or not isinstance(body, dict):
                self.add(cap, FAIL, f"{q!r}: HTTP {code}: {str(body)[:160]}")
            elif body.get("code") == "no_backends":
                self.add(cap, OWNER, "ни один источник не готов")
            elif body.get("hits"):
                hits = body["hits"]
                ok = all(h.get("title") and str(h.get("url", "")).startswith("https://") for h in hits)
                first = hits[0]
                self.add(cap, PASS if ok else FAIL,
                         f"{q!r}: {len(hits)} результатов через {body.get('backend')} (заголовок, адрес"
                         f"{', описание' if first.get('snippet') else ''})",
                         f"{first.get('title')} | {first.get('url')}")
            else:
                self.add(cap, PARTIAL if want_hits else PASS,
                         f"{q!r}: {body.get('backend')} ответил {body.get('code')} — пусто",
                         str(body.get("detail") or ""))

        verdict_of("search.en", "Alan Turing")
        verdict_of("search.ru", "Алан Тьюринг")
        verdict_of("search.question", "what is entropy")
        # парный контроль шлюза запроса: законный проходит (выше), плохие отвергаются
        bad = ("http://169.254.169.254/latest/meta-data/", "cat /home/user/.ssh/id_rsa", "{\"api_key\": \"x\"}")
        leaked = [q for q in bad if one(q)[0] != 400]
        self.add("search.query_guard", FAIL if leaked else PASS,
                 "запросы-адрес/путь/JSON отвергнуты шлюзом (400)" if not leaked
                 else "шлюз пропустил: " + "; ".join(leaked),
                 f"проверено {len(bad)} плохих запросов")

    def browser_checks(self) -> None:
        code, made = self.call("POST", "/api/browser/sessions", {}, timeout=60)
        if code != 200 or not isinstance(made, dict) or "session_id" not in made:
            verdict = BLOCKED if code in (403, 503) else NOT_RUN
            for cap in ("browser.navigate", "browser.js_render", "browser.late_content", "browser.long_page",
                        "browser.scroll", "browser.http_status", "browser.redirects", "browser.ssrf_pair"):
                self.add(cap, verdict, f"сессия не создана: {code} {str(made)[:120]}")
            return
        sid = made["session_id"]

        def nav(url: str):
            return self.call("POST", f"/api/browser/sessions/{sid}/act",
                             {"action": "navigate", "url": url}, timeout=120)

        try:
            code, snap = nav(EXAMPLE)
            if code == 200 and isinstance(snap, dict) and (
                    "Example Domain" in str(snap.get("title")) or "documentation" in str(snap.get("text"))):
                self.add("browser.navigate", PASS, "https://example.com открыт, текст извлечён",
                         f"title={snap.get('title')!r} text={len(snap.get('text', ''))} знаков")
            else:
                self.add("browser.navigate", NOT_RUN, f"example.com не открылся: {code} {str(snap)[:140]}")
                return

            code, snap = nav(JS_PAGE)
            quotes = str(snap.get("text", "")).count("\u201c") if code == 200 else 0
            self.add("browser.js_render", PASS if quotes >= 5 else (NOT_RUN if code != 200 else FAIL),
                     f"страница, собираемая скриптом: {quotes} цитат в тексте", f"{code}")

            code, snap = nav(LATE_PAGE)
            if code == 200:
                immediate = str(snap.get("text", "")).count("\u201c")
                self.add("browser.late_content", PASS if immediate >= 5 else PARTIAL,
                         f"текст, подгруженный fetch-ем после загрузки: {immediate} цитат сразу после открытия"
                         + ("" if immediate >= 5 else " (снимок взят до подгрузки: нужно ожидание сети)"),
                         f"{len(snap.get('text', ''))} знаков")
            else:
                self.add("browser.late_content", NOT_RUN, f"{code}")

            code, snap = nav(LONG_ARTICLE)
            if code == 200:
                total = snap.get("text_total")
                if total is None:
                    self.add("browser.long_page", PARTIAL,
                             f"текст усечён снимком до {len(snap.get('text', ''))} знаков, читать дальше нечем "
                             "(нет text_total/offset)", "старая сборка")
                else:
                    self.add("browser.long_page", PASS,
                             f"длинная статья: всего {total} знаков, окно {len(snap.get('text', ''))}; "
                             "остальное читается параметром offset у browser.read_dom",
                             f"text_total={total}")
            else:
                self.add("browser.long_page", NOT_RUN, f"{code}")

            c3, s3 = self.call("POST", f"/api/browser/sessions/{sid}/act",
                               {"action": "snapshot", "scroll": 3}, timeout=60)
            if c3 == 200 and isinstance(s3, dict) and "text_offset" in s3:
                self.add("browser.scroll", PASS, "прокрутка на N экранов принята (snapshot scroll=3)", "text_offset в ответе")
            else:
                self.add("browser.scroll", PARTIAL, "параметр scroll не поддержан: ленивая подгрузка недостижима",
                         f"snapshot -> {c3}")

            code, snap = nav(ERROR_404)
            if code == 200 and isinstance(snap, dict):
                st = snap.get("http_status")
                self.add("browser.http_status", PASS if st == 404 else PARTIAL,
                         "страница 404 названа 404" if st == 404 else "код ответа сервера не сообщается: "
                         "страница-ошибка неотличима от пустой", f"http_status={st}")
            else:
                self.add("browser.http_status", NOT_RUN, f"{code}")

            code, snap = nav(REDIRECTS)
            landed = str(snap.get("url", "")) if isinstance(snap, dict) else ""
            self.add("browser.redirects", PASS if code == 200 and landed.endswith("/get") else NOT_RUN,
                     f"цепочка из 3 редиректов пройдена, итоговый адрес {landed}", f"{code}")

            # парный контроль SSRF: законный адрес принят (example.com выше), плохие отвергнуты
            accepted = []
            for name, url in BAD_TARGETS:
                c, body = nav(url)
                if c == 200:
                    accepted.append(name)
            self.add("browser.ssrf_pair", FAIL if accepted else PASS,
                     "законный https-адрес открыт, а loopback/metadata/файл/десятичный IP/имя->loopback отвергнуты"
                     if not accepted else "ПРИНЯТЫ: " + ", ".join(accepted),
                     f"проверено {len(BAD_TARGETS)} плохих целей")
        finally:
            self.call("POST", f"/api/browser/sessions/{sid}/stop", {}, timeout=30)


def render(results: list[Result], base: str, active: bool) -> str:
    lines = [f"# Проба веб-доступа: {base} ({'активная' if active else 'только чтение'})", "",
             "| возможность | вердикт | что измерено | доказательство |", "|---|---|---|---|"]
    for r in results:
        cell = lambda s: str(s).replace("|", "/").replace("\n", " ")  # noqa: E731
        lines.append(f"| {cell(r.cap)} | {r.verdict} | {cell(r.detail)} | {cell(r.evidence)} |")
    counts: dict[str, int] = {}
    for r in results:
        counts[r.verdict] = counts.get(r.verdict, 0) + 1
    lines += ["", "Итог: " + ", ".join(f"{k}={v}" for k, v in sorted(counts.items()))]
    return "\n".join(lines) + "\n"


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(description=__doc__.split("\n\n")[0])
    ap.add_argument("--base", required=True, help="адрес экземпляра, например http://127.0.0.1:8801")
    ap.add_argument("--token", default="", help="токен (лучше --token-file или BCC_TOKEN)")
    ap.add_argument("--token-file", default="", help="файл с токеном; содержимое не печатается")
    ap.add_argument("--active", action="store_true",
                    help="поиск и браузер: ДЕЙСТВУЕТ на экземпляре (след OSIRIS, сессия браузера)")
    ap.add_argument("--no-browser", action="store_true", help="не проверять браузер")
    ap.add_argument("--out", default="", help="записать отчёт (markdown) в этот файл")
    args = ap.parse_args(argv)
    for stream in (sys.stdout, sys.stderr):
        try:
            stream.reconfigure(encoding="utf-8")
        except (AttributeError, ValueError):
            pass

    token = args.token or os.environ.get("BCC_TOKEN", "")
    if not token and args.token_file:
        with open(args.token_file, encoding="utf-8") as fh:
            token = fh.read().strip()
    if not token:
        print("нужен токен: --token-file, BCC_TOKEN или --token", file=sys.stderr)
        return 2

    probe = Probe(args.base, token)
    if not probe.status_checks():
        print(render(probe.results, args.base, False))
        return 2
    web_on = any(r.cap == "web.feature" and r.verdict == PASS for r in probe.results)
    if args.active:
        probe.search_checks(web_on)
        if not args.no_browser:
            probe.browser_checks()
    else:
        probe.add("search.* / browser.*", NOT_RUN, "активные пробы не запускались (нужен --active)")

    text = render(probe.results, args.base, args.active)
    print(text)
    if args.out:
        with open(args.out, "w", encoding="utf-8", newline="\n") as fh:
            fh.write(text)
    return 1 if any(r.verdict == FAIL for r in probe.results) else 0


if __name__ == "__main__":
    sys.exit(main())
