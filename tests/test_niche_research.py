"""niche_research: queue durability, caps, deadline, budget, safety rules and a full simulated day.

No network: fake model, fake search and a local HTTP server on 127.0.0.1 (the fetcher's private-host guard is
opened only for these tests via ``allow_private_hosts``). The clock is injected; sleeps advance the fake clock.
"""
from __future__ import annotations

import ast
import json
import re
import threading
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path

import pytest

from niche_research import MAX_RUNNING_TASKS, PAYMENT_DISCLAIMER
from niche_research.config import PAID_DAILY_HARD_CAP, load_config
from niche_research.models import (FakeModel, ModelRouter, NoModelAvailable, OllamaClient,
                                   OpenAICompatClient, PaidSpendRefused)
from niche_research.orchestrator import Orchestrator
from niche_research.pii import strip_pii
from niche_research.plan import day_slot
from niche_research.roles import AgentContext
from niche_research.store import Store
from niche_research.web import PoliteFetcher, ResearchLog, SearchHit, SearchResult

PKG = Path(__file__).resolve().parents[1] / "niche_research"
MONDAY_0900_UTC = 1793005200.0  # 2026-10-26 09:00 UTC, a Monday
DAY = 86400


class Clock:
    def __init__(self, t: float = MONDAY_0900_UTC):
        self.t = t
        self.sleeps: list[float] = []

    def __call__(self) -> float:
        return self.t

    def sleep(self, s: float) -> None:
        self.sleeps.append(s)
        self.t += s


# ---- local HTTP server ---------------------------------------------------------------------------------------
PAGES = {
    "/robots.txt": (200, "text/plain", "User-agent: *\nDisallow: /private\n"),
    "/uborka-1": (200, "text/html; charset=utf-8",
                  "<html><head><title>Клининг Альфа</title></head><body>"
                  "<p>Генеральная уборка квартиры от 4500 рублей за 50 кв.м</p>"
                  "<p>Клиенты часто спрашивают, сколько стоит уборка после ремонта</p>"
                  "<p>Пишите: ivan.petrov@example.org или +7 912 345-67-89</p></body></html>"),
    "/uborka-2": (200, "text/html; charset=utf-8",
                  "<html><head><title>Чистый дом</title></head><body>"
                  "<p>Поддерживающая уборка от 2900 рублей, выезд в день обращения</p>"
                  "<p>Отзывы: уборщица опоздала, менеджер долго отвечал на сообщения</p></body></html>"),
    "/uborka-3": (200, "text/html; charset=utf-8",
                  "<html><head><title>Блеск</title></head><body>"
                  "<p>Мойка окон 350 рублей за створку, уборка офисов по договору</p>"
                  "<p>Запись на уборку через форму на сайте, ответ в течение часа</p></body></html>"),
    "/private": (200, "text/html", "<html><body>secret</body></html>"),
    "/captcha": (200, "text/html", "<html><head><title>Just a moment...</title></head><body>check</body></html>"),
    "/login": (403, "text/html", "forbidden"),
}


class _Handler(BaseHTTPRequestHandler):
    def do_GET(self):  # noqa: N802
        self.server.paths.append(self.path)
        self.server.agents.append(self.headers.get("User-Agent", ""))
        status, ctype, body = PAGES.get(self.path, (404, "text/plain", "nf"))
        data = body.encode("utf-8")
        self.send_response(status)
        self.send_header("Content-Type", ctype)
        self.send_header("Content-Length", str(len(data)))
        self.end_headers()
        self.wfile.write(data)

    def log_message(self, *a):
        pass


@pytest.fixture
def site():
    srv = ThreadingHTTPServer(("127.0.0.1", 0), _Handler)
    srv.paths, srv.agents = [], []
    th = threading.Thread(target=srv.serve_forever, daemon=True)
    th.start()
    srv.base = f"http://127.0.0.1:{srv.server_address[1]}"
    yield srv
    srv.shutdown()
    srv.server_close()


class FakeSearch:
    def __init__(self, base: str, fail: bool = False):
        self.base = base
        self.fail = fail
        self.queries: list[str] = []

    def search(self, query, task_id=None):
        self.queries.append(query)
        if self.fail:
            return SearchResult(query, [], error="simulated search outage")
        return SearchResult(query, [SearchHit(f"page {i}", f"{self.base}/uborka-{i}") for i in (1, 2, 3)])


def _sources(prompt: str) -> list[tuple[int, str, str]]:
    """(id, url, first excerpt line) from a researcher/competitor prompt."""
    out = []
    for m in re.finditer(r"^\[(\d+)\] (\S+) .*?\n(.+)$", prompt, re.M):
        out.append((int(m.group(1)), m.group(2), m.group(3).strip()))
    return out


def fake_responder(system: str, prompt: str) -> str:
    role = re.search(r"РОЛЬ: (\w+)", prompt).group(1)
    if role == "researcher":
        src = _sources(prompt)
        obs = [{"source_id": i, "quote": line[:80], "fact": f"факт из источника {i}"} for i, _, line in src]
        obs.append({"source_id": 1, "quote": "этой фразы нет на странице совсем", "fact": "выдумка"})
        return json.dumps({"observations": obs, "conclusions": ["владельцы тратят время на ответы о цене"],
                           "assumptions": ["часть заявок теряется вечером"], "unknowns": ["объём рынка"]},
                          ensure_ascii=False)
    if role == "competitor":
        src = _sources(prompt)
        offers = [{"source_id": i, "name": f"фирма {i}", "offer": "уборка", "price_text": "от 2900 руб",
                   "quote": line[:60]} for i, _, line in src]
        return "```json\n" + json.dumps({"offers": offers, "notes": "-"}, ensure_ascii=False) + "\n```"
    if role == "packager":
        return json.dumps({"offer": {"for_whom": "клининговые компании 1–5 человек",
                                     "routine_task": "ответы на вопросы о цене", "deliverable": "шаблоны ответов",
                                     "timeframe": "3 дня", "price_hypothesis": "неизвестно"},
                           "scores": {"problem_visibility": 4, "buyer_accessibility": 3, "competition": 2,
                                      "output_quality": 4, "expected_margin": 3},
                           "score_reasons": {"problem_visibility": "в отзывах жалобы на долгие ответы"},
                           "production_cost": "около 4 часов"}, ensure_ascii=False)
    if role == "critic":
        return json.dumps({"unsupported_claims": ["цена не подтверждена"], "weak_points": ["мало источников"],
                           "reasons_wont_pay": ["делают сами", "нет бюджета", "не доверяют шаблонам"],
                           "adjusted_scores": {"problem_visibility": 3, "buyer_accessibility": 3, "competition": 2,
                                               "output_quality": 4, "expected_margin": 2},
                           "verdict": "слабая гипотеза"}, ensure_ascii=False)
    if role == "weekly":
        return json.dumps({"ranking": ["уборка"], "why": "-"}, ensure_ascii=False)
    # draft
    return "# FAQ клининга\n\nСколько стоит генеральная уборка? От 4500 рублей. Срок: неизвестно."


def make_cfg(tmp_path: Path, **extra) -> object:
    cfg = {"tz_offset_hours": 0, "allow_private_hosts": True, "max_tasks_per_tick": 10, **extra}
    (tmp_path / "config.json").write_text(json.dumps(cfg), encoding="utf-8")
    return load_config(tmp_path)


def make_orch(tmp_path: Path, site=None, clock: Clock | None = None, clients=None, search=None, **extra):
    clock = clock or Clock()
    cfg = make_cfg(tmp_path, **extra)
    store = Store(cfg.db_path)
    router = ModelRouter(store, cfg, clients if clients is not None else [FakeModel(fake_responder)], clock)
    orch = Orchestrator(cfg, store=store, router=router, clock=clock, sleeper=clock.sleep,
                        search=search if search is not None else (FakeSearch(site.base) if site else FakeSearch("")))
    return orch, clock


# ---- queue -----------------------------------------------------------------------------------------------------
def test_queue_survives_restart_and_recovers_stale_running(tmp_path):
    db = tmp_path / "q.sqlite3"
    s1 = Store(db)
    key = s1.issue_orchestrator_key()
    a = s1.enqueue(key, task_key="d01:researcher", day=1, week=1, niche="уборка", role="researcher", now=0)
    b = s1.enqueue(key, task_key="d01:competitor", day=1, week=1, niche="уборка", role="competitor", depends_on=a,
                   now=0)
    assert s1.enqueue(key, task_key="d01:researcher", day=1, week=1, niche="x", role="researcher", now=0) == a
    t = s1.claim_next("w1", now=100, lease_seconds=60)
    assert t["id"] == a and t["attempts"] == 1
    assert s1.claim_next("w1", now=101, lease_seconds=60) is None  # b waits on a
    s1.close()  # "PC restart" while a is running

    s2 = Store(db)
    assert s2.counts() == {"pending": 1, "running": 1, "done": 0, "failed": 0}
    assert s2.recover_stale(now=120, max_attempts=3) == 0  # lease still valid
    assert s2.recover_stale(now=200, max_attempts=3) == 1
    assert s2.get_task(a)["status"] == "pending" and s2.get_task(a)["attempts"] == 1
    for now in (300, 500):  # attempts 2 and 3, each lease expires
        assert s2.claim_next("w2", now=now, lease_seconds=10)["id"] == a
        s2.recover_stale(now=now + 50, max_attempts=3)
    assert s2.get_task(a)["status"] == "failed"
    assert s2.get_task(b)["status"] == "failed" and "dependency" in s2.get_task(b)["error"]


def test_fail_retries_then_fails_and_release_returns_attempt(tmp_path):
    s = Store(tmp_path / "q.sqlite3")
    key = s.issue_orchestrator_key()
    a = s.enqueue(key, task_key="k", day=1, week=1, niche="n", role="packager", now=0)
    s.claim_next("w", 1, 60)
    s.release_task(a)
    assert s.get_task(a)["attempts"] == 0 and s.get_task(a)["status"] == "pending"
    for i in range(3):
        s.claim_next("w", 10 + i, 60)
        status = s.fail_task(a, "boom", 10 + i, max_attempts=3)
    assert status == "failed"


def test_cap_of_four_running_tasks_is_enforced_by_the_db(tmp_path):
    db = tmp_path / "q.sqlite3"
    s = Store(db)
    key = s.issue_orchestrator_key()
    for i in range(12):
        s.enqueue(key, task_key=f"t{i}", day=1, week=1, niche="n", role="researcher", now=0)
    got = [s.claim_next("w", 1, 600, cap=99) for _ in range(6)]
    assert sum(1 for g in got if g) == MAX_RUNNING_TASKS == 4
    assert s.counts()["running"] == 4

    # concurrent claimers in separate connections never exceed the cap either
    for t in s.tasks(status="running"):
        s.finish_task(t["id"], 2)
    claimed: list = []
    barrier = threading.Barrier(10)

    def worker(n):
        st = Store(db)
        barrier.wait()
        claimed.append(st.claim_next(f"w{n}", 3, 600))
        st.close()

    threads = [threading.Thread(target=worker, args=(n,)) for n in range(10)]
    [t.start() for t in threads]
    [t.join() for t in threads]
    assert sum(1 for c in claimed if c) == 4
    assert Store(db).counts()["running"] == 4


# ---- agents cannot enqueue -------------------------------------------------------------------------------------
def test_agents_have_no_enqueue_access(tmp_path):
    s = Store(tmp_path / "q.sqlite3")
    with pytest.raises(PermissionError):
        s.enqueue(None, task_key="x", day=1, week=1, niche="n", role="researcher")
    s.issue_orchestrator_key()
    with pytest.raises(PermissionError):
        s.issue_orchestrator_key()  # the orchestrator already holds the only key
    other = Store(tmp_path / "other.sqlite3").issue_orchestrator_key()
    with pytest.raises(PermissionError):
        s.enqueue(other, task_key="x", day=1, week=1, niche="n", role="researcher")
    # agent-side modules never import the queue or the orchestrator
    for mod in ("roles.py", "models.py", "web.py", "pii.py", "plan.py"):
        tree = ast.parse((PKG / mod).read_text(encoding="utf-8"))
        names = [n.module or "" for n in ast.walk(tree) if isinstance(n, ast.ImportFrom)]
        names += [a.name for n in ast.walk(tree) if isinstance(n, ast.Import) for a in n.names]
        assert not any(x.endswith(("store", "orchestrator")) for x in names), (mod, names)
    fields = set(AgentContext.__dataclass_fields__)
    assert not {f for f in fields if "store" in f or "enqueue" in f or "queue" in f or "key" in f}
    ctx = AgentContext(task={"id": 1, "niche": "n", "day": 1}, _ask=lambda *a, **k: None)
    assert not hasattr(ctx, "enqueue")


# ---- deadline --------------------------------------------------------------------------------------------------
def test_deadline_stops_exactly_after_28_days(tmp_path, site):
    orch, clock = make_orch(tmp_path, site)
    assert orch.tick() == {"state": "not_started"}
    assert orch.start()["ok"]
    assert orch.start()["ok"] is False  # refuses a second start
    clock.t = MONDAY_0900_UTC + 28 * DAY - 3600  # sleeps inside the tick advance the fake clock
    r = orch.tick()
    assert r["state"] == "ran" and r["day"] == 28
    clock.t = MONDAY_0900_UTC + 28 * DAY
    r = orch.tick()
    assert r["state"] == "stopped" and r["stopped"]["reason"] == "deadline"
    final = (orch.config.run_dir / "reports" / "final.md").read_text(encoding="utf-8")
    assert PAYMENT_DISCLAIMER in final and "Ни одна ниша не является доказанной" in final
    before = orch.store.counts()
    clock.t += 3 * DAY
    assert orch.tick()["state"] == "stopped"
    assert orch.store.counts() == before  # no new work after the deadline


def test_tick_is_bounded(tmp_path, site):
    orch, clock = make_orch(tmp_path, site, max_tasks_per_tick=2)
    orch.start()
    clock.t += 3 * DAY + 60  # day 4: catch-up plans days 1..4
    r = orch.tick()
    assert len(r["processed"]) == 2
    assert orch.store.counts()["pending"] > 0


# ---- budget ----------------------------------------------------------------------------------------------------
def test_paid_spend_is_zero_by_default_and_paid_call_refused(tmp_path):
    cfg = make_cfg(tmp_path)
    assert cfg.paid_daily_limit == 0
    store = Store(cfg.db_path)
    paid = FakeModel(fake_responder, provider="7api", model="glm-5.3-flash", paid=True, usd_per_mtok=0.028)
    router = ModelRouter(store, cfg, [paid], Clock())
    with pytest.raises(PaidSpendRefused):
        router.complete("s", "РОЛЬ: weekly x")
    assert paid.calls == 0 and store.paid_spend() == 0.0
    free = FakeModel(fake_responder, provider="openrouter", model="nemotron:free")
    router = ModelRouter(store, cfg, [free, paid], Clock())
    reply = router.complete("s", "РОЛЬ: weekly x")
    assert reply.provider == "openrouter" and reply.cost_usd == 0 and paid.calls == 0
    assert store.total_spend() == 0.0


def test_paid_only_with_explicit_owner_limit_and_clamped(tmp_path):
    cfg = make_cfg(tmp_path, paid_usd_limit=50)
    assert cfg.paid_daily_limit == PAID_DAILY_HARD_CAP == 2.0
    store = Store(cfg.db_path)
    paid = FakeModel(fake_responder, provider="7api", model="glm-5.3-flash", paid=True, usd_per_mtok=0.028)
    clock = Clock()
    router = ModelRouter(store, cfg, [paid], clock)
    r = router.complete("s", "РОЛЬ: weekly x")
    assert r.paid and r.cost_usd > 0
    assert store.paid_spend() == pytest.approx(r.cost_usd)
    store.log_spend(provider="7api", model="m", tokens_in=0, tokens_out=0, cost_usd=2.0, paid=True, ok=True,
                    now=clock())
    with pytest.raises(PaidSpendRefused):
        router.complete("s", "РОЛЬ: weekly x")


def test_no_model_releases_task_without_burning_attempts(tmp_path, site):
    orch, clock = make_orch(tmp_path, site, clients=[])
    orch.start()
    r = orch.tick()
    assert r["processed"] == [] and "model" in r["stop_reason"]
    assert all(t["attempts"] == 0 for t in orch.store.tasks())


def test_router_falls_back_and_logs_failures(tmp_path):
    cfg = make_cfg(tmp_path)
    store = Store(cfg.db_path)
    bad = FakeModel(fake_responder, provider="gemini", model="g", fail_times=5)
    good = FakeModel(fake_responder, provider="openrouter", model="o")
    r = ModelRouter(store, cfg, [bad, good], Clock()).complete("s", "РОЛЬ: weekly")
    assert r.provider == "openrouter"
    rows = store.spend_rows()
    assert [(x["provider"], x["ok"]) for x in rows] == [("gemini", 0), ("openrouter", 1)]


def test_ollama_without_loaded_model_is_skipped(tmp_path):
    class H(BaseHTTPRequestHandler):
        def do_GET(self):  # noqa: N802
            body = json.dumps({"models": [{"name": "qwen:big"}]} if self.path == "/api/tags" else {"models": []})
            self.send_response(200)
            self.end_headers()
            self.wfile.write(body.encode())

        def do_POST(self):  # noqa: N802
            raise AssertionError("must not load a model")

        def log_message(self, *a):
            pass

    srv = ThreadingHTTPServer(("127.0.0.1", 0), H)
    threading.Thread(target=srv.serve_forever, daemon=True).start()
    try:
        oll = OllamaClient(f"http://127.0.0.1:{srv.server_address[1]}")
        assert oll.available() is False
        cfg = make_cfg(tmp_path)
        store = Store(cfg.db_path)
        r = ModelRouter(store, cfg, [oll, FakeModel(fake_responder)], Clock()).complete("s", "РОЛЬ: weekly")
        assert r.provider == "fake"
        down = OllamaClient("http://127.0.0.1:9")
        assert down.available() is False
    finally:
        srv.shutdown()
        srv.server_close()


def test_api_key_never_reaches_logs(tmp_path):
    class H(BaseHTTPRequestHandler):
        def do_POST(self):  # noqa: N802
            self.send_response(401)
            self.end_headers()
            self.wfile.write(self.headers.get("Authorization", "").encode())

        def log_message(self, *a):
            pass

    srv = ThreadingHTTPServer(("127.0.0.1", 0), H)
    threading.Thread(target=srv.serve_forever, daemon=True).start()
    secret = "sk-test-SECRET-123456"
    try:
        cfg = make_cfg(tmp_path)
        store = Store(cfg.db_path)
        c = OpenAICompatClient("openrouter", f"http://127.0.0.1:{srv.server_address[1]}", "m:free", secret)
        with pytest.raises(NoModelAvailable) as ei:
            ModelRouter(store, cfg, [c], Clock()).complete("s", "p")
        assert secret not in str(ei.value)
        dump = json.dumps([store.spend_rows(), store.errors(), store.run_log()])
        assert secret not in dump and "401" in dump
    finally:
        srv.shutdown()
        srv.server_close()


# ---- web rules -------------------------------------------------------------------------------------------------
def _fetcher(tmp_path, clock, **kw):
    store = Store(tmp_path / "w.sqlite3")
    log = ResearchLog(store, tmp_path / "run", clock)
    return PoliteFetcher(store, log, clock=clock, sleeper=clock.sleep, allow_private=True, **kw), store


def test_robots_disallow_is_respected(tmp_path, site):
    clock = Clock()
    f, store = _fetcher(tmp_path, clock)
    r = f.fetch(site.base + "/private", task_id=7)
    assert not r.ok and "robots" in r.error
    assert "/private" not in site.paths and "/robots.txt" in site.paths
    assert all(a.startswith("BossmanNicheResearch/1.0") for a in site.agents)
    rec = store.learning([7])
    assert rec and rec[0]["kind"] == "unknown" and rec[0]["url"].endswith("/private")


def test_walls_are_not_bypassed_and_private_hosts_refused(tmp_path, site):
    clock = Clock()
    f, _ = _fetcher(tmp_path, clock)
    assert "not bypassed" in f.fetch(site.base + "/captcha").error
    assert "not bypassed" in f.fetch(site.base + "/login").error
    assert site.paths.count("/login") == 1  # no retry
    strict = PoliteFetcher(Store(tmp_path / "s.sqlite3"), None, clock=clock, sleeper=clock.sleep)
    assert strict.fetch(site.base + "/uborka-1").error == "non-public host refused"
    assert strict.fetch("file:///c:/windows/win.ini").ok is False


def test_rate_limit_at_least_5s_per_host(tmp_path):
    clock = Clock()
    stamps: dict[str, list[float]] = {}

    def transport(url, headers, timeout):
        host = url.split("/")[2]
        stamps.setdefault(host, []).append(clock())
        body = b"User-agent: *\nAllow: /\n" if url.endswith("robots.txt") else b"<p>hello</p>"
        return 200, {"Content-Type": "text/plain" if url.endswith("robots.txt") else "text/html"}, body

    f, store = _fetcher(tmp_path, clock, transport=transport, min_interval=1)  # 1 s is raised to the 5 s floor
    for p in ("/a", "/b", "/c"):
        assert f.fetch("http://shop.example" + p).ok
    f.fetch("http://other.example/x")
    s = stamps["shop.example"]
    assert len(s) == 4  # robots + 3 pages
    assert all(b - a >= 5.0 for a, b in zip(s, s[1:]))
    assert stamps["other.example"][0] - s[-1] < 5.0  # different host is not delayed by shop.example
    # the per-host time survives a new fetcher (next tick)
    f2 = PoliteFetcher(store, None, clock=clock, sleeper=clock.sleep, transport=transport, allow_private=True)
    f2._robots = dict(f._robots)
    f2.fetch("http://shop.example/d")
    assert stamps["shop.example"][-1] - stamps["shop.example"][-2] >= 5.0


def test_pii_is_stripped(tmp_path, site):
    text = "Звоните +7 (912) 345-67-89, пишите ivan@mail.ru, tg @ivan_petrov. Цена 15 000 ₽, 24.10.2026, 4500 рублей"
    out = strip_pii(text)
    assert "ivan@mail.ru" not in out and "345-67-89" not in out and "@ivan_petrov" not in out
    assert "15 000 ₽" in out and "24.10.2026" in out and "4500 рублей" in out
    clock = Clock()
    f, store = _fetcher(tmp_path, clock)
    r = f.fetch(site.base + "/uborka-1", task_id=3)
    assert r.ok
    ctx = AgentContext(task={"id": 3, "niche": "уборка", "day": 1}, _ask=lambda *a, **k: None)
    from niche_research.roles import _excerpt
    ex = _excerpt(r.text, "уборка")
    assert "ivan.petrov@example.org" not in ex and "912 345" not in ex and ctx.task["id"] == 3


# ---- full simulated day ----------------------------------------------------------------------------------------
def test_full_simulated_day_produces_report_sample_proposal(tmp_path, site):
    orch, clock = make_orch(tmp_path, site)
    assert day_slot(MONDAY_0900_UTC, 1, 0).niche == "уборка"
    orch.start()
    clock.t += 3600
    r = orch.tick()
    assert r["state"] == "ran"
    assert [p["outcome"] for p in r["processed"]] == ["done"] * 5, r
    run = orch.config.run_dir
    day = (run / "reports" / "day-01.md").read_text(encoding="utf-8")
    for needle in (PAYMENT_DISCLAIMER, "## Что проверено", "## Что найдено", "## Что неизвестно",
                   "## Расход моделей", "## Следующий шаг", "fake:fake-1", "Генеральная уборка квартиры"):
        assert needle in day, needle
    assert "ivan.petrov@example.org" not in day and "345-67-89" not in day
    assert "этой фразы нет на странице" not in day  # fabricated quote not presented as a fact
    sample = (run / "samples" / "day-01-sample.md").read_text(encoding="utf-8")
    assert sample.startswith("# FAQ") and PAYMENT_DISCLAIMER in sample
    prop = (run / "proposals" / "day-01-proposals.md").read_text(encoding="utf-8")
    assert "Причина:" in prop and "Ожидаемый эффект:" in prop and "Как проверить:" in prop
    assert "не применяется автоматически" in prop.lower()
    assert (run / "reports" / "week-1.md").exists()
    learning = (run / "research" / "learning.jsonl").read_text(encoding="utf-8").splitlines()
    kinds = {json.loads(x)["kind"] for x in learning}
    assert {"observation", "conclusion", "assumption"} <= kinds
    assert all(json.loads(x)["date"] and json.loads(x)["task_id"] for x in learning)
    for res in orch.store.results(day=1):
        assert res["models"] == ["fake:fake-1"] and res["tools"]
    rs = orch.store.results(day=1, role="researcher")[0]["content"]
    assert len(rs["observations"]) == 3 and len(rs["unverified"]) == 1
    assert len(orch.store.results(day=1, role="competitor")[0]["content"]["offers"]) == 3
    assert orch.store.paid_spend() == 0.0 and orch.store.total_spend() == 0.0
    st = orch.status()
    assert st["queue"]["done"] == 5 and st["day"] == 1 and st["paid_spend_usd"] == 0.0


def test_search_outage_is_recorded_as_unknown(tmp_path, site):
    orch, clock = make_orch(tmp_path, site, search=FakeSearch("", fail=True))
    orch.start()
    orch.tick()
    rs = orch.store.results(day=1, role="researcher")[0]["content"]
    assert rs["observations"] == [] and any("неизвестно" in u for u in rs["unknowns"])
    day = (orch.config.run_dir / "reports" / "day-01.md").read_text(encoding="utf-8")
    assert "simulated search outage" in day and PAYMENT_DISCLAIMER in day


def test_weekend_comparison_selects_at_most_two(tmp_path, site):
    orch, clock = make_orch(tmp_path, site, max_tasks_per_tick=40)
    orch.start()
    clock.t += 5 * DAY + 60  # Saturday = day 6: days 1..5 niches + day 6 weekly
    orch.tick()
    orch.tick()
    weekly = orch.store.results(day=6, role="weekly")
    assert weekly and len(weekly[0]["content"]["selected"]) <= 2
    week = (orch.config.run_dir / "reports" / "week-1.md").read_text(encoding="utf-8")
    assert "Выбранные гипотезы" in week and "Сравнение моделей" in week and PAYMENT_DISCLAIMER in week


def test_plan_maps_weekdays_and_weekends():
    thursday = MONDAY_0900_UTC + 3 * DAY
    slots = [day_slot(thursday, d, 0) for d in range(1, 8)]
    assert [s.kind for s in slots] == ["niche", "niche", "weekly", "weekly", "niche", "niche", "niche"]
    assert [s.niche for s in slots if s.niche] == ["уборка", "малярные работы", "переезды", "мелкий ремонт",
                                                    "монтажные услуги"]
    assert day_slot(thursday, 28, 0).week == 4


# ---- nothing is ever sent ---------------------------------------------------------------------------------------
def test_package_has_no_send_or_publish_paths():
    banned_modules = {"smtplib", "telegram", "aiogram", "telebot", "tweepy", "discord", "slack_sdk", "email",
                      "imaplib", "poplib", "praw", "facebook", "vk_api", "instagrapi"}
    banned_text = ("sendMessage", "api.telegram.org", "smtp.", "sendmail", "/statuses/update", "graph.facebook.com")
    for py in PKG.glob("*.py"):
        src = py.read_text(encoding="utf-8")
        tree = ast.parse(src)
        mods = {(n.module or "").split(".")[0] for n in ast.walk(tree) if isinstance(n, ast.ImportFrom)}
        mods |= {a.name.split(".")[0] for n in ast.walk(tree) if isinstance(n, ast.Import) for a in n.names}
        assert not (mods & banned_modules), (py.name, mods & banned_modules)
        for t in banned_text:
            assert t not in src, (py.name, t)
        funcs = [n.name.lower() for n in ast.walk(tree) if isinstance(n, (ast.FunctionDef, ast.AsyncFunctionDef))]
        assert not [f for f in funcs if f.startswith(("send", "post_", "publish", "tweet", "mail"))], (py.name, funcs)


def test_cli_start_status_stop_without_network(tmp_path):
    import os
    import subprocess
    import sys

    env = {**os.environ, "NICHE_RESEARCH_DIR": str(tmp_path), "NICHE_RESEARCH_KEYS_FILE": str(tmp_path / "none.env")}
    root = PKG.parent

    def cli(*args):
        p = subprocess.run([sys.executable, "-m", "niche_research", *args], cwd=root, env=env, capture_output=True,
                           text=True, encoding="utf-8", timeout=60)
        return p.returncode, json.loads(p.stdout)

    assert cli("status")[1]["started"] is False
    code, out = cli("start")
    assert code == 0 and out["ok"] and out["deadline_ts"] - out["start_ts"] == 28 * DAY
    code, out = cli("start")
    assert code == 1 and out["error"] == "already started"
    code, out = cli("stop")
    assert code == 0 and out["stopped"]["reason"] == "manual"
    assert PAYMENT_DISCLAIMER in (tmp_path / "run" / "reports" / "final.md").read_text(encoding="utf-8")
    assert cli("tick")[1]["state"] == "stopped"
    assert cli("status")[1]["paid_spend_usd"] == 0.0
