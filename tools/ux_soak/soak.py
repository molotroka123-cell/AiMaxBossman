"""Long-session UX soak: UI + API + CLI over one backend, with restarts and invariants.

Usage (PowerShell, from the repo root):

    python tools/ux_soak/soak.py --port 8870 --data-dir C:/.../rc19-data/f-ux/soak1 \
        --out C:/.../evidence/rc19/f/soak1 --minutes 60 --max-interactions 600

Everything runs against a fresh data dir; the owner data root is refused. Model calls go
to a local stub (tools/ux_soak/fake_model.py) unless ``--live-model`` is given, in which
case a handful of calls go to the local Ollama model (skipped while the PAUSE file exists).
"""
from __future__ import annotations

import argparse
import json
import os
import random
import re
import subprocess
import sys
import time
import traceback
from pathlib import Path

import httpx
import psutil

sys.path.insert(0, str(Path(__file__).resolve().parent))
import fake_model  # noqa: E402
import stats  # noqa: E402
from backend import Backend, DesktopBackend  # noqa: E402

PAUSE = Path(r"C:\Users\asd\Bossman\rc19-owner-test.PAUSE")
LIVE_MODEL = "bossman-fast-qwen36-35b-a3b-q5:latest"
BAD_TEXT = re.compile(r"\bundefined\b|\bNaN\b|\[object Object\]|Traceback \(most recent|Internal Server Error")
# Pages whose render starts nothing by itself, safe to open repeatedly. Every nav item is
# visited; "heavy" pages (studios) are visited less often.
HEAVY = {"video-studio", "music-studio", "web_designer", "images", "browser", "terminal", "coding"}


class Soak:
    def __init__(self, args):
        self.args = args
        self.out = Path(args.out)
        self.out.mkdir(parents=True, exist_ok=True)
        (self.out / "shots").mkdir(exist_ok=True)
        self.rng = random.Random(args.seed)
        self.desktop = args.launch == "desktop"
        if self.desktop:
            # launcher logs carry the access-token banner: keep them in the private data dir
            self.backend = DesktopBackend(args.port, Path(args.data_dir), Path(args.data_dir) / "soak-logs",
                                          profile=Path(args.profile) / "desktop-window", cdp_port=args.cdp_port,
                                          browser=args.browser)
        else:
            self.backend = Backend(args.port, Path(args.data_dir), self.out / "logs")
        if args.pylib:  # e.g. prompt_toolkit for the CLI, as in the owner bundle
            base_env = self.backend.env

            def env_with_pylib(_base=base_env):
                env = _base()
                env["PYTHONPATH"] = env["PYTHONPATH"] + os.pathsep + args.pylib
                return env
            self.backend.env = env_with_pylib
        self.windows_seen: list[int] = []
        self.events = open(self.out / "events.jsonl", "a", encoding="utf-8")
        self.findings: list[dict] = []
        self.api_lat: list[float] = []
        self.render_lat: dict[str, list[float]] = {}
        self.restart_ready: list[float] = []
        self.reconnect_lat: list[float] = []
        self.offline_detect_lat: list[float] = []
        self.rss: list[tuple[float, float]] = []
        self.heap: list[tuple[float, float]] = []
        self.nodes: list[tuple[float, float]] = []
        self.browser_rss: list[tuple[float, float]] = []
        self.console: list[dict] = []
        self.netfail: list[dict] = []
        self.interactions = 0
        self.counts: dict[str, int] = {}
        self.restarts = 0
        self.phase = "up"             # up | down (backend deliberately killed)
        self.t0 = time.monotonic()
        self.agents: dict[str, int] = {}
        self.slow_tasks: set[int] = set()
        self.cli_sessions: list[str] = []
        self.cli_last_word = ""
        self.theme_expected: str | None = None
        self.live_calls = 0

    # ------------------------------------------------------------------ record
    def now(self) -> float:
        return round(time.monotonic() - self.t0, 2)

    def log(self, _kind: str, **kw) -> None:
        rec = {"t": self.now(), "kind": _kind, **kw}
        self.events.write(json.dumps(rec, ensure_ascii=False, default=str) + "\n")
        self.events.flush()

    def finding(self, sev: str, area: str, what: str, evidence: str = "", shot: bool = True) -> None:
        name = ""
        if shot and getattr(self, "page", None) is not None:
            name = f"shots/f{len(self.findings):03d}_{area}.png"
            try:
                self.page.screenshot(path=str(self.out / name))
            except Exception:  # noqa: BLE001
                name = ""
        rec = {"t": self.now(), "severity": sev, "area": area, "what": what,
               "evidence": str(evidence)[:800], "shot": name, "interaction": self.interactions}
        self.findings.append(rec)
        self.log("finding", **rec)
        print(f"[{sev}] {area}: {what} :: {str(evidence)[:200]}", flush=True)

    # --------------------------------------------------------------------- api
    def client(self) -> httpx.Client:
        return httpx.Client(base_url=self.backend.base, timeout=30,
                            headers={"X-BCC-Token": self.backend.token})

    def api(self, method: str, path: str, **kw):
        t = time.perf_counter()
        with self.client() as c:
            r = c.request(method, path, **kw)
        self.api_lat.append((time.perf_counter() - t) * 1000)
        if r.status_code >= 500:
            self.finding("high", "api", f"{method} {path} -> {r.status_code}", r.text, shot=False)
        return r

    def tasks(self) -> dict[int, dict]:
        out: dict[int, dict] = {}
        before = None
        while True:
            params = {"limit": 500, **({"before_id": before} if before else {})}
            items = self.api("GET", "/api/tasks", params=params).json()
            items = items.get("tasks", items) if isinstance(items, dict) else items
            for t in items:
                out[int(t["id"])] = t
            if len(items) < 500:
                return out
            before = min(int(t["id"]) for t in items)

    def statuses(self) -> dict[int, str]:
        return {k: v["status"] for k, v in self.tasks().items()}

    # ------------------------------------------------------------------- setup
    def setup_backend_data(self) -> None:
        stub = f"http://127.0.0.1:{self.args.stub_port}/v1"
        prov = self.api("POST", "/api/providers", json={"name": "soak-stub", "kind": "openai_compat",
                                                        "base_url": stub}).json()
        dead = self.api("POST", "/api/providers", json={"name": "soak-dead", "kind": "openai_compat",
                                                        "base_url": f"http://127.0.0.1:{self.args.dead_port}/v1"}).json()
        for name, pid, model in (("soak-fast", prov["id"], "soak-fast"), ("soak-slow", prov["id"], "soak-slow"),
                                 ("soak-500", prov["id"], "soak-500"), ("soak-dead", dead["id"], "dead-model")):
            m = self.api("POST", "/api/models", json={"provider_id": pid, "name": model, "kind": "local"}).json()
            a = self.api("POST", "/api/agents", json={"name": name, "model_id": m["id"],
                                                      "system_prompt": "soak test agent", "max_steps": 2}).json()
            self.agents[name] = int(a["id"])
        self.log("setup", agents=self.agents)

    # ---------------------------------------------------------------- browser
    def attach(self, page) -> None:
        self.page = page

        def on_console(m):
            if m.type == "error":
                phase = "down" if re.search(r"ERR_CONNECTION_(REFUSED|RESET)|ERR_EMPTY_RESPONSE", m.text) else self.phase
                rec = {"t": self.now(), "phase": phase, "text": m.text[:400], "url": page.url}
                self.console.append(rec)
                self.log("console", **rec)

        def on_pageerror(e):
            rec = {"t": self.now(), "phase": self.phase, "text": ("pageerror: " + str(e))[:400], "url": page.url}
            self.console.append(rec)
            self.log("console", **rec)

        def on_failed(req):
            # connection refused/reset can only mean "backend down" (events are delivered
            # late by the sync API, so the phase flag alone would misfile them)
            phase = "down" if re.search(r"ERR_CONNECTION_(REFUSED|RESET)|ERR_EMPTY_RESPONSE", str(req.failure)) else self.phase
            rec = {"t": self.now(), "phase": phase, "url": req.url, "failure": str(req.failure)}
            self.netfail.append(rec)
            self.log("netfail", **rec)

        def on_response(r):
            if r.status >= 500 or r.status in (401, 403, 404, 409, 422):
                rec = {"t": self.now(), "phase": self.phase, "url": r.url, "status": r.status,
                       "method": r.request.method}
                self.netfail.append(rec)
                self.log("http", **rec)

        page.on("console", on_console)
        page.on("pageerror", on_pageerror)
        page.on("requestfailed", on_failed)
        page.on("response", on_response)
        page.on("dialog", lambda d: d.dismiss())
        self.cdp = page.context.new_cdp_session(page)
        self.cdp.send("Performance.enable")

    def login(self) -> None:
        p = self.page
        if not self.desktop:        # the desktop window is already on the address the launcher gave it
            p.goto(self.backend.base + "/", wait_until="domcontentloaded")
        p.wait_for_function("() => !document.getElementById('login').hidden || !document.getElementById('shell').hidden",
                            timeout=30000)
        if p.locator("#login").is_visible():
            p.fill("#login-token", self.backend.token)
            p.click("#login-submit")
        p.wait_for_selector("#shell:not([hidden])", timeout=30000)
        p.wait_for_selector("#view[data-rendered]", timeout=30000)

    def current(self) -> str:
        return self.page.evaluate("() => document.getElementById('view').dataset.rendered || ''")

    def nav_ids(self) -> list[str]:
        return self.page.evaluate("() => [...new Set([...document.querySelectorAll('#nav button.nav-item[data-page]')].map(e => e.dataset.page))]")

    def wait_rendered(self, pid: str, how: str, t: float, timeout: float = 25.0) -> bool:
        try:
            self.page.wait_for_selector(f"#view[data-rendered='{pid}']", timeout=timeout * 1000)
        except Exception:  # noqa: BLE001
            self.finding("high", "render", f"page {pid} not rendered within {timeout}s via {how}",
                         self.page.evaluate("() => document.getElementById('view').innerText.slice(0,300)"))
            return False
        dt = (time.perf_counter() - t) * 1000
        self.render_lat.setdefault(pid, []).append(dt)
        self.check_screen(pid)
        return True

    def check_screen(self, pid: str) -> None:
        """Blank / stuck spinner / junk text on the rendered page."""
        p = self.page
        info = p.evaluate("""() => { const v = document.getElementById('view');
            const vis = (e) => !!(e.offsetWidth || e.offsetHeight || e.getClientRects().length);
            return { text: v.innerText.slice(0, 4000), len: v.innerText.trim().length,
                     skel: [...v.querySelectorAll('.skeleton')].filter(vis).length,
                     busy: [...v.querySelectorAll('[aria-busy="true"], .is-loading')].filter(vis).length } }""")
        if info["len"] < 15:
            self.finding("high", "blank", f"page {pid} rendered (almost) empty", info["text"])
        m = BAD_TEXT.search(info["text"])
        if m:
            self.finding("medium", "junk-text", f"page {pid} shows '{m.group(0)}'",
                         info["text"][max(0, m.start() - 120): m.end() + 120])
        if info["skel"] or info["busy"]:
            deadline = time.monotonic() + 15
            while time.monotonic() < deadline:
                time.sleep(1)
                if self.current() != pid:
                    return
                again = p.evaluate("""() => { const v = document.getElementById('view');
                    const vis = (e) => !!(e.offsetWidth || e.offsetHeight || e.getClientRects().length);
                    return [...v.querySelectorAll('.skeleton, [aria-busy="true"], .is-loading')].filter(vis).length }""")
                if not again:
                    return
            self.finding("medium", "stuck-loading", f"page {pid}: loading placeholder visible > 15 s",
                         f"skeleton={info['skel']} busy={info['busy']}")

    def nav_mouse(self, pid: str) -> None:
        loc = self.page.locator(f"#nav button.nav-item[data-page='{pid}']").first
        loc.scroll_into_view_if_needed(timeout=5000)
        t = time.perf_counter()
        loc.click(timeout=10000)
        self.wait_rendered(pid, "mouse", t)

    def nav_palette(self, pid: str) -> None:
        p = self.page
        p.locator("#page-title").click()
        p.keyboard.press("Control+K")
        p.wait_for_selector("#palette:not([hidden])", timeout=5000)
        self.wait_palette_focus()
        p.keyboard.type(pid)
        # the first "Открыть · <title>" item whose keys contain the id
        idx = p.evaluate("""(pid) => [...document.querySelectorAll('#palette-list .palette-item')]
            .findIndex(e => e.innerText.startsWith('Открыть'))""", pid)
        if idx < 0:
            self.finding("low", "palette", f"no 'Открыть' item for '{pid}' in Ctrl+K", shot=False)
            p.keyboard.press("Escape")
            return
        for _ in range(idx):
            p.keyboard.press("ArrowDown")
        t = time.perf_counter()
        p.keyboard.press("Enter")
        if not p.locator("#palette").is_hidden():
            self.finding("medium", "palette", "palette stayed open after Enter")
        target = p.evaluate("() => location.hash.replace(/^#\\/?/, '').split('?')[0]")
        self.wait_rendered(target, "palette", t)

    def wait_palette_focus(self) -> None:
        # openPalette() focuses the input after a 20 ms timer; keys typed before that go
        # to the page. A user types slower than that; the harness must wait for focus.
        self.page.wait_for_function("() => document.activeElement && document.activeElement.id === 'palette-input'",
                                    timeout=3000)

    def settle(self) -> None:
        """Between interactions: no palette / modal left open by a failed step."""
        p = self.page
        for _ in range(3):
            open_ = p.evaluate("() => !document.getElementById('palette').hidden || "
                               "[...document.getElementById('modal-root').children].some(e => e.offsetWidth || e.offsetHeight)")
            if not open_:
                return
            p.keyboard.press("Escape")
            p.wait_for_timeout(200)

    def nav_tab(self, pid: str) -> None:
        """Keyboard only: Tab from the top of the document to the nav item, Enter."""
        p = self.page
        p.evaluate("() => { document.activeElement && document.activeElement.blur(); window.scrollTo(0,0); }")
        p.locator("#page-title").click()
        p.evaluate("() => document.getElementById('desktop-skip') && document.getElementById('desktop-skip').focus()")
        for i in range(120):
            p.keyboard.press("Tab")
            hit = p.evaluate("(pid) => { const a = document.activeElement; return !!(a && a.matches && "
                             "a.matches(`#nav button.nav-item[data-page='${pid}']`)) }", pid)
            if hit:
                t = time.perf_counter()
                p.keyboard.press("Enter")
                self.wait_rendered(pid, "tab", t)
                self.log("tab", pid=pid, tabs=i + 1)
                return
        self.finding("medium", "keyboard", f"nav item {pid} not reachable with Tab in 120 presses", shot=False)

    def esc_modal(self) -> None:
        p = self.page
        p.locator("#page-title").click()
        p.keyboard.press("Control+K")
        p.wait_for_selector("#palette:not([hidden])", timeout=5000)
        self.wait_palette_focus()
        p.keyboard.type("Новая задача")
        p.keyboard.press("Enter")
        try:
            p.wait_for_function("() => document.getElementById('modal-root').children.length > 0", timeout=8000)
        except Exception:  # noqa: BLE001
            self.finding("medium", "modal", "'Новая задача' from palette did not open a modal")
            return
        p.keyboard.press("Escape")
        try:
            p.wait_for_function("() => ![...document.getElementById('modal-root').children].some(e => "
                                "e.offsetWidth || e.offsetHeight)", timeout=4000)
        except Exception:  # noqa: BLE001
            self.finding("medium", "keyboard", "Esc did not close the 'Новая задача' modal")
            p.keyboard.press("Escape")

    def goto(self, pid: str) -> None:
        if self.current() != pid:
            self.nav_mouse(pid)

    def toast_text(self, timeout: float = 8.0) -> str:
        try:
            self.page.wait_for_selector("#toast-root .toast", timeout=timeout * 1000)
        except Exception:  # noqa: BLE001
            return ""
        return self.page.evaluate("() => [...document.querySelectorAll('#toast-root .toast')].map(e => e.innerText).join(' | ')")

    def clear_toasts(self) -> None:
        self.page.evaluate("() => { const r = document.getElementById('toast-root'); if (r) r.innerHTML = '' }")

    def task_ui(self, agent: str, text: str) -> int | None:
        p = self.page
        self.goto("home-v3")
        self.clear_toasts()
        before = set(self.tasks())
        box = p.locator("#view textarea").first
        box.click()
        box.fill(text)
        pick = p.locator("#view select[aria-label='Агент']")
        if pick.count():
            pick.first.select_option(str(self.agents[agent]))
        t = time.perf_counter()
        box.press("Control+Enter")
        msg = self.toast_text()
        dt = (time.perf_counter() - t) * 1000
        self.render_lat.setdefault("task-submit", []).append(dt)
        new = sorted(set(self.tasks()) - before)
        if "поставлена" not in msg or not new:
            self.finding("high", "task-ui", f"UI task submit: toast='{msg[:120]}' new={new}")
            return None
        return new[-1]

    # ------------------------------------------------------------------ memory
    def sample(self) -> None:
        t = self.now()
        rss = self.backend.rss_mb()
        if rss is not None:
            self.rss.append((t, rss))
        try:
            m = {x["name"]: x["value"] for x in self.cdp.send("Performance.getMetrics")["metrics"]}
            self.heap.append((t, m.get("JSHeapUsedSize", 0) / 1048576.0))
            self.nodes.append((t, m.get("Nodes", 0)))
        except Exception:  # noqa: BLE001
            pass
        try:
            total = 0
            seen: set[int] = set()
            for pr in psutil.process_iter(["name", "cmdline"]):
                cl = " ".join(pr.info.get("cmdline") or [])
                if "rc19-ux-profile" in cl and "python" not in (pr.info.get("name") or "").lower():
                    for q in [pr, *pr.children(recursive=True)]:
                        if q.pid not in seen:
                            seen.add(q.pid)
                            total += q.memory_info().rss
            self.browser_rss.append((t, total / 1048576.0))
        except psutil.Error:
            pass
        self.log("sample", rss_mb=rss, heap_mb=self.heap[-1][1] if self.heap else None,
                 nodes=self.nodes[-1][1] if self.nodes else None,
                 browser_mb=self.browser_rss[-1][1] if self.browser_rss else None)

    # --------------------------------------------------------------------- cli
    def cli(self, *argv: str, stdin: str | None = None, timeout: float = 120) -> subprocess.CompletedProcess:
        env = self.backend.env()
        t = time.perf_counter()
        # the owner's CMD runs `python -m bossman.cli` and finds the backend itself (desktop.lock /
        # BCC_PORT / data dir); only the plain-server mode passes --url.
        head = [sys.executable, "-m", "bossman.cli"] + ([] if self.desktop else ["--url", self.backend.base])
        r = subprocess.run([*head, *argv],
                           input=stdin, capture_output=True, text=True, encoding="utf-8", errors="replace",
                           env=env, cwd=str(self.backend.data_dir), timeout=timeout,
                           creationflags=subprocess.BELOW_NORMAL_PRIORITY_CLASS if sys.platform == "win32" else 0)
        self.log("cli", argv=list(argv), rc=r.returncode, ms=round((time.perf_counter() - t) * 1000),
                 out=r.stdout[-400:], err=r.stderr[-400:])
        return r

    def cli_check(self) -> None:
        r = self.cli("list", "tasks", "--json", "--limit", "1000")
        try:
            data = json.loads(r.stdout.strip().splitlines()[-1])
            cli = {int(i["id"]): i["status"] for i in data["items"]}
        except Exception as e:  # noqa: BLE001
            self.finding("high", "cli", f"list tasks --json unparsable: {e}", r.stdout[-300:] + r.stderr[-300:], shot=False)
            return
        api = self.statuses()
        missing = sorted(set(api) - set(cli))
        mismatch = [k for k in cli if k in api and cli[k] != api[k]
                    and api[k] not in stats.ACTIVE and cli[k] not in stats.ACTIVE]  # listed before/after a finish
        if missing or mismatch:
            self.finding("high", "cli", f"CLI vs API tasks differ: missing={missing[:10]} status-mismatch={mismatch[:10]}",
                         f"cli={len(cli)} api={len(api)}", shot=False)
        done = [k for k, v in api.items() if v == "completed"]
        if done:
            tid = self.rng.choice(done)
            rr = self.cli("result", str(tid))
            if rr.returncode != 0 or "soak-ok" not in rr.stdout and "soak-ok" not in rr.stderr:
                self.finding("medium", "cli", f"`result {tid}` of a done task has no answer",
                             (rr.stdout + rr.stderr)[-300:], shot=False)

    def cmd_console(self, *argv: str):
        """The owner's CMD window: `python -m bossman.cli …` in a real (pseudo) console."""
        from conpty import ConPty
        head = [sys.executable, "-m", "bossman.cli"] + ([] if self.desktop else ["--url", self.backend.base])
        return ConPty([*head, *argv], env=self.backend.env(), cwd=str(self.backend.data_dir))

    def cli_chat(self) -> None:
        """CMD chat (TTY), then resume: history replayed; the turns are visible in the UI."""
        word = f"чат{self.rng.randint(1000, 9999)}"
        first = not self.cli_sessions
        argv = ("chat", "--agent", "soak-fast") if first else ("resume", self.cli_sessions[-1])
        t = time.perf_counter()
        with self.cmd_console(*argv) as con:
            if not con.expect(r">\s", 60):
                self.finding("high", "cli-chat", f"`bossman {argv[0]}` shows no prompt in 60 s", con.text[-600:], shot=False)
                return
            prompt_ms = (time.perf_counter() - t) * 1000
            self.render_lat.setdefault("cli-prompt", []).append(prompt_ms)
            con.mark()
            t = time.perf_counter()
            con.send(f"{'первое сообщение' if first else 'следующее'} {word}\r")
            ok = con.expect(r"soak-ok", 90)
            self.render_lat.setdefault("cli-answer", []).append((time.perf_counter() - t) * 1000)
            con.expect(r">\s", 20)
            turn_text = con.text
            con.send("/exit\r")
            con.wait(20)
            out = con.text
        self.log("cli_chat", argv=list(argv), out=out[-1500:])
        if not ok:
            self.finding("high", "cli-chat", "chat turn printed no model answer", out[-800:], shot=False)
        if turn_text.count("NOT_APPLICABLE") > 1:
            self.finding("medium", "cli-chat", "verifier noise in the conversation", turn_text[-800:], shot=False)
        if "Traceback" in out:
            self.finding("high", "cli-chat", "traceback in the owner's console", out[-800:], shot=False)
        m = re.search(r"resume ([A-Za-z0-9_-]{4,64})", out)
        if first:
            if not m:
                self.finding("high", "cli-chat", "chat did not report a resumable session", out[-500:], shot=False)
                return
            self.cli_sessions.append(m.group(1))
        elif "продолжение сессии" not in out or self.cli_last_word not in out:
            # the replay shows the last few turns: the previous turn must be among them
            self.finding("high", "cli-chat", f"resume {argv[1]} did not replay history", out[-800:], shot=False)
        self.cli_last_word = word
        # the same turn is a task the UI shows
        match = [t for t in self.tasks().values() if word in (t.get("prompt") or "")]
        if not match:
            self.finding("high", "cli-chat", "CMD chat turn not visible as a task in API/UI", word, shot=False)
            return
        self.goto("tasks")
        self.verify_task_on_page(max(int(t["id"]) for t in match))

    # ---------------------------------------------------------------- restarts
    def restart_cycle(self, kind: str) -> None:
        """Kill the backend while the UI is open; verify offline UX and full recovery."""
        p = self.page
        self.restarts += 1
        slow_id = None
        draft = None
        if kind == "running-task":
            r = self.api("POST", "/api/tasks", json={"prompt": f"долгая задача {self.restarts}",
                                                     "title": f"slow-{self.restarts}",
                                                     "agent_id": self.agents["soak-slow"]})
            slow_id = int(r.json()["task"]["id"])
            self.slow_tasks.add(slow_id)
            deadline = time.monotonic() + 20
            while time.monotonic() < deadline and self.statuses().get(slow_id) != "running":
                time.sleep(0.5)
        target = self.rng.choice(["home-v3", "tasks", "agents", "mission_console"])
        self.goto(target)
        if target == "home-v3":
            draft = f"черновик до перезапуска {self.restarts}"
            box = p.locator("#view textarea").first
            box.click()
            box.fill(draft)
        before = self.statuses()
        agents_before = sorted(a["name"] for a in self.api("GET", "/api/agents").json())
        hash_before = p.evaluate("() => location.hash")
        self.log("restart.begin", n=self.restarts, restart_kind=kind, page=target, slow=slow_id, tasks=len(before))

        self.phase = "down"
        t = time.perf_counter()
        if kind == "close-window":
            # the owner closes the BOSSMAN window: the launcher must stop its server and exit
            launcher = self.backend.proc
            if not self.backend.click_close():
                self.finding("medium", "harness", "no visible BOSSMAN window to close", shot=False)
            try:
                launcher.wait(timeout=45)
            except Exception:  # noqa: BLE001
                self.finding("medium", "desktop", "closing the BOSSMAN window did not end the launcher within 45 s",
                             shot=False)
            self.backend.kill()
            self.backend.close_windows()
            draft = None
        else:
            # crash / the console window closed: the server dies, the window stays open
            self.backend.kill()
            try:
                p.wait_for_selector("#stale-banner:not([hidden])", timeout=45000)
                self.offline_detect_lat.append((time.perf_counter() - t) * 1000)
            except Exception:  # noqa: BLE001
                self.finding("high", "offline", "no 'Нет связи' banner within 45 s of backend death")
        time.sleep(self.rng.uniform(2, 8))
        ready = self.backend.start()          # the owner starts BOSSMAN again (shortcut / bcc)
        self.restart_ready.append(ready)
        t = time.perf_counter()
        self.phase = "up"
        if kind == "close-window":
            self.connect_window()
            p = self.page
            p.wait_for_function("() => !document.getElementById('login').hidden || "
                                "!!document.querySelector('#view[data-rendered]')", timeout=30000)
            if p.locator("#login").is_visible():
                self.finding("high", "session", "reopened BOSSMAN window asked for the token again")
                self.login()
        else:
            if self.desktop:
                self.check_windows()
            try:
                p.wait_for_selector("#stale-banner[hidden]", state="attached", timeout=60000)
                self.reconnect_lat.append((time.perf_counter() - t) * 1000)
            except Exception:  # noqa: BLE001
                self.finding("high", "reconnect", "UI did not reconnect within 60 s after the backend came back")
            # login must survive a restart (sessions are persistent)
            if p.locator("#login").is_visible():
                self.finding("high", "session", "UI fell back to the login screen after a backend restart")
                self.login()
            # open context: same page, draft kept
            if p.evaluate("() => location.hash") != hash_before:
                self.finding("medium", "context", f"hash changed across restart {hash_before} -> {p.evaluate('() => location.hash')}")
        if draft is not None:
            val = p.locator("#view textarea").first.input_value() if p.locator("#view textarea").count() else ""
            if val != draft:
                self.finding("high", "context", "home draft lost across backend restart", f"got '{val[:80]}'")
            p.locator("#view textarea").first.fill("")
            p.locator("#page-title").click()
        # history & state
        after = self.statuses()
        for prob in stats.diff_tasks(before, after):
            self.finding("high", "history", prob, shot=False)
        agents_after = sorted(a["name"] for a in self.api("GET", "/api/agents").json())
        if agents_after != agents_before:
            self.finding("high", "settings", "agents changed across restart", f"{agents_before} -> {agents_after}", shot=False)
        # no stale 'running' — every task active before must settle (the stub answers in <= 25 s)
        watched = [k for k, v in before.items() if v in stats.ACTIVE]
        deadline = time.monotonic() + 150
        stale = watched
        while time.monotonic() < deadline:
            stale = stats.stale_active(self.statuses(), watched)
            if not stale:
                break
            time.sleep(3)
        if stale:
            self.finding("high", "stale-running", f"tasks still active 150 s after restart: {stale}",
                         json.dumps({k: self.tasks()[k].get("last_run") for k in stale[:3]}, default=str)[:700])
        # the UI tasks page agrees with the API
        self.goto("tasks")
        if slow_id:
            self.verify_task_on_page(slow_id)
        self.log("restart.end", n=self.restarts, ready_s=round(ready, 2), stale=stale)

    # ------------------------------------------------------------ desktop window
    def app_pages(self) -> list:
        pages = []
        for ctx in self.cdp_browser.contexts:
            pages += [pg for pg in ctx.pages if pg.url.startswith(self.backend.base)]
        return pages

    def connect_window(self) -> None:
        """Attach to the --app window the launcher opened (CDP), exactly what the owner sees."""
        deadline = time.monotonic() + 60
        last = None
        while time.monotonic() < deadline:
            try:
                self.cdp_browser = self.pw.chromium.connect_over_cdp(f"http://127.0.0.1:{self.args.cdp_port}")
                pages = self.app_pages()
                if pages:
                    self.attach(pages[0])
                    self.page.wait_for_load_state("domcontentloaded")
                    self.log("window", pages=len(pages), url=self.page.url)
                    return
            except Exception as e:  # noqa: BLE001
                last = e
            time.sleep(1)
        raise RuntimeError(f"no BOSSMAN window reachable over CDP: {last}")

    def check_windows(self) -> None:
        """After the shortcut is started again while the old window is still open."""
        time.sleep(3)
        pages = self.app_pages()
        self.windows_seen.append(len(pages))
        if len(pages) > 1:
            self.finding("medium", "desktop",
                         f"starting BOSSMAN again while its window is open leaves {len(pages)} windows",
                         ", ".join(pg.url for pg in pages), shot=False)
            for pg in pages:            # the owner closes the extra one and keeps working
                if pg is not self.page:
                    try:
                        pg.close()
                    except Exception:  # noqa: BLE001
                        pass

    def open_task_card(self, tid: int) -> str:
        """Open the task card on Задачи and return its detail text."""
        p = self.page
        self.goto("tasks")
        title = (self.tasks()[tid].get("title") or "")[:40]
        head = p.locator("#view .task-head", has_text=title).first
        try:
            head.click(timeout=8000)
            p.wait_for_function("() => { const b = document.querySelector('#view .task.open .task-body'); "
                                "return b && !b.innerText.includes('Загрузка деталей') }", timeout=10000)
        except Exception:  # noqa: BLE001
            self.finding("medium", "tasks-page", f"task {tid} card did not open / details stuck loading")
            return ""
        return p.evaluate("() => document.querySelector('#view .task.open').innerText")

    def verify_task_on_page(self, tid: int) -> None:
        task = self.tasks().get(tid)
        if not task:
            return
        # the tasks list may render asynchronously / via live refresh
        title = (task.get("title") or "")[:40]
        try:
            self.page.wait_for_function("(t) => document.getElementById('view').innerText.includes(t)",
                                        arg=title, timeout=10000)
        except Exception:  # noqa: BLE001
            self.finding("medium", "tasks-page", f"task {tid} '{title}' ({task['status']}) not visible on Задачи page")

    # -------------------------------------------------------------- live model
    def live_check(self) -> None:
        if not self.args.live_model or self.live_calls >= 3:
            return
        if PAUSE.exists():
            self.log("live.paused")
            return
        if "soak-live" not in self.agents:
            prov = self.api("POST", "/api/providers", json={"name": "ollama-local", "kind": "openai_compat",
                                                            "base_url": "http://127.0.0.1:11434/v1"}).json()
            m = self.api("POST", "/api/models", json={"provider_id": prov["id"], "name": LIVE_MODEL,
                                                      "kind": "local", "context_window": 8192}).json()
            a = self.api("POST", "/api/agents", json={"name": "soak-live", "model_id": m["id"],
                                                      "system_prompt": "Отвечай очень коротко.",
                                                      "max_steps": 1, "max_tokens": 64}).json()
            self.agents["soak-live"] = int(a["id"])
        self.live_calls += 1
        t = time.perf_counter()
        r = self.api("POST", "/api/tasks", json={"prompt": "Ответь одним словом: столица Франции?",
                                                 "title": f"live-{self.live_calls}", "agent_id": self.agents["soak-live"]})
        tid = int(r.json()["task"]["id"])
        deadline = time.monotonic() + 400
        st = ""
        while time.monotonic() < deadline:
            st = self.statuses().get(tid, "")
            if st not in stats.ACTIVE:
                break
            time.sleep(3)
        data = self.api("GET", f"/api/tasks/{tid}").json()
        self.log("live", task=tid, status=st, secs=round(time.perf_counter() - t, 1),
                 result=str(data.get("result"))[:200], error=str(data.get("error"))[:200])
        if st != "completed":
            self.finding("medium", "live-model", f"live local-model task ended {st}", str(data.get("error"))[:300], shot=False)

    # ------------------------------------------------------------ interactions
    def pick(self) -> str:
        table = [("nav_mouse", 30), ("nav_palette", 10), ("nav_tab", 5), ("esc_modal", 5),
                 ("task_ui", 10), ("task_api", 8), ("bad_empty", 3), ("bad_long", 2), ("dead_ui", 3),
                 ("err500_api", 2), ("models_down", 1), ("tasks_verify", 6), ("cli_check", 4), ("cli_chat", 3),
                 ("theme", 2), ("reload", 3), ("agent_edit", 2)]
        total = sum(w for _, w in table)
        x = self.rng.uniform(0, total)
        for name, w in table:
            x -= w
            if x <= 0:
                return name
        return "nav_mouse"

    def nav_target(self) -> str:
        ids = [i for i in self.nav_ids() if i != self.current()]
        light = [i for i in ids if i not in HEAVY]
        return self.rng.choice(ids if self.rng.random() < 0.25 else light)

    def do(self, name: str) -> None:
        a = self.agents
        if name == "nav_mouse":
            self.nav_mouse(self.nav_target())
        elif name == "nav_palette":
            self.nav_palette(self.nav_target())
        elif name == "nav_tab":
            self.nav_tab(self.rng.choice(["tasks", "agents", "settings", "models", "home-v3", "approvals"]))
        elif name == "esc_modal":
            self.esc_modal()
        elif name == "task_ui":
            self.task_ui(self.rng.choice(["soak-fast"] * 4 + ["soak-dead"]),
                         f"UI задача {self.interactions}: проверь soak {self.rng.randint(1, 99999)}")
        elif name == "task_api":
            self.api("POST", "/api/tasks", json={"prompt": f"API задача {self.interactions}",
                                                 "title": f"api-{self.interactions}", "agent_id": a["soak-fast"]})
        elif name == "bad_empty":
            self.goto("home-v3")
            self.clear_toasts()
            box = self.page.locator("#view textarea").first
            box.fill("")
            box.press("Control+Enter")
            msg = self.toast_text(5)
            if "Опишите" not in msg:
                self.finding("medium", "bad-input", "empty submit gave no hint", msg)
        elif name == "bad_long":
            tid = self.task_ui("soak-fast", "Ж" * 50_000)
            if tid is None:
                return
        elif name == "dead_ui":
            tid = self.task_ui("soak-dead", f"недоступная модель {self.interactions}")
            if tid is None:
                return
            deadline = time.monotonic() + 90
            while time.monotonic() < deadline and self.statuses().get(tid) in stats.ACTIVE:
                time.sleep(2)
            task = self.tasks()[tid]
            run = task.get("last_run") or {}
            self.log("dead_ui", task=tid, status=task["status"], model=run.get("model_alias"), error=run.get("error"))
            if task["status"] == "completed" and run.get("model_alias") == "dead-model":
                self.finding("high", "model-down", f"dead-model task {tid} completed on the dead model?!", shot=False)
            elif task["status"] not in ("completed", "failed"):
                self.finding("high", "model-down", f"dead-model task {tid} ended {task['status']}", shot=False)
            self.open_task_card(tid)
        elif name == "models_down":
            # every model unreachable: the task must fail with a readable reason in the UI
            self.stub.shutdown()
            self.stub.server_close()
            try:
                tid = self.task_ui("soak-fast", f"все модели недоступны {self.interactions}")
                if tid is None:
                    return
                deadline = time.monotonic() + 150
                while time.monotonic() < deadline and self.statuses().get(tid) in stats.ACTIVE:
                    time.sleep(2)
                task = self.tasks()[tid]
                self.log("models_down", task=tid, status=task["status"], run=task.get("last_run"))
                if task["status"] != "failed":
                    self.finding("high", "model-down", f"all-models-down task {tid} ended {task['status']}", shot=False)
                text = self.open_task_card(tid)
                if "нет связи" not in text.lower():
                    self.finding("medium", "model-down", "task card shows no 'нет связи' reason", text[:500])
            finally:
                self.stub = fake_model.start(self.args.stub_port)
        elif name == "err500_api":
            self.api("POST", "/api/tasks", json={"prompt": "провайдер 500", "title": f"e500-{self.interactions}",
                                                 "agent_id": a["soak-500"]})
        elif name == "tasks_verify":
            self.goto("tasks")
            recent = sorted(self.tasks())[-3:]
            for tid in recent:
                self.verify_task_on_page(tid)
        elif name == "cli_check":
            self.cli_check()
        elif name == "cli_chat":
            self.cli_chat()
        elif name == "theme":
            before = self.page.evaluate("() => document.documentElement.dataset.theme || ''")
            self.page.click("#theme-toggle")
            after = self.page.evaluate("() => document.documentElement.dataset.theme || ''")
            if before == after:
                self.finding("low", "settings", "theme toggle did not change data-theme", f"{before}")
            self.theme_expected = after
        elif name == "reload":
            h = self.page.evaluate("() => location.hash")
            t = time.perf_counter()
            self.page.reload(wait_until="domcontentloaded")
            try:
                self.page.wait_for_selector("#view[data-rendered]", timeout=30000)
                self.render_lat.setdefault("reload", []).append((time.perf_counter() - t) * 1000)
            except Exception:  # noqa: BLE001
                if self.page.locator("#login").is_visible():
                    self.finding("high", "session", "reload asked for the token again")
                    self.login()
                else:
                    self.finding("high", "reload", "page did not render after reload")
            if self.page.evaluate("() => location.hash") != h:
                self.finding("medium", "context", f"reload lost the open page {h}")
            if self.theme_expected:
                th = self.page.evaluate("() => document.documentElement.dataset.theme || ''")
                if th != self.theme_expected:
                    self.finding("medium", "settings", f"theme not kept after reload {self.theme_expected} -> {th}")
        elif name == "agent_edit":
            role = f"роль {self.interactions}"
            self.api("PATCH", f"/api/agents/{a['soak-fast']}", json={"role": role})
            self.goto("agents")
            self.page.wait_for_timeout(800)
            if role not in self.page.evaluate("() => document.getElementById('view').innerText"):
                # live refresh might not rerender a page already open: an explicit refresh must
                self.page.click("#refresh-btn")
                self.page.wait_for_timeout(1500)
                if role not in self.page.evaluate("() => document.getElementById('view').innerText"):
                    self.finding("low", "agents-page", "edited agent role not shown on Агенты page", role)
            self.agent_role = role

    # -------------------------------------------------------------------- main
    def run(self) -> int:
        from playwright.sync_api import sync_playwright
        self.stub = fake_model.start(self.args.stub_port)
        self.backend.start()
        self.setup_backend_data()
        deadline = self.t0 + self.args.minutes * 60
        with sync_playwright() as pw:
            self.pw = pw
            ctx = None
            if self.desktop:
                self.connect_window()
            else:
                ctx = pw.chromium.launch_persistent_context(
                    self.args.profile, headless=not self.args.headed, viewport={"width": 1440, "height": 900},
                    args=["--disable-background-timer-throttling"])
                self.attach(ctx.pages[0] if ctx.pages else ctx.new_page())
            self.phase = "login"
            self.login()
            self.phase = "up"
            self.sample()
            self.live_check()
            kinds = ["running-task", "idle", "close-window"] if self.desktop else ["running-task", "idle"]
            script = [x for x in (self.args.script or "").split(",") if x]
            try:
                while time.monotonic() < deadline and self.interactions < self.args.max_interactions:
                    if self.interactions and self.interactions % self.args.restart_every == 0:
                        self.restart_cycle(kinds[self.restarts % len(kinds)])
                        self.sample()
                    name = script.pop(0) if script else self.pick()
                    self.interactions += 1
                    self.counts[name] = self.counts.get(name, 0) + 1
                    t = time.perf_counter()
                    try:
                        self.settle()
                        self.do(name)
                        self.log("step", n=self.interactions, name=name, ms=round((time.perf_counter() - t) * 1000),
                                 page=self.current())
                    except Exception as e:  # noqa: BLE001
                        self.finding("medium", "harness", f"{name} raised {type(e).__name__}",
                                     traceback.format_exc()[-600:])
                        try:
                            self.page.keyboard.press("Escape")
                        except Exception:  # noqa: BLE001
                            pass
                    if self.interactions % 10 == 0:
                        self.sample()
                    time.sleep(self.args.pause)
                self.live_check()
                self.restart_cycle("final")
                self.sample()
            finally:
                try:
                    if ctx is not None:
                        ctx.close()
                    elif getattr(self, "cdp_browser", None) is not None:
                        self.cdp_browser.close()        # disconnects; the window is closed below
                except Exception:  # noqa: BLE001
                    pass
                self.backend.kill()
                if self.desktop:
                    self.backend.close_windows()
                self.stub.shutdown()
        return self.write_summary(stub_calls=self.stub.calls)

    def write_summary(self, stub_calls: int) -> int:
        dur = time.monotonic() - self.t0
        up_console = [c for c in self.console if c["phase"] == "up"]
        up_net = [n for n in self.netfail if n["phase"] == "up"]
        all_render = [v for vs in self.render_lat.values() for v in vs]
        metrics = {
            "duration_min": round(dur / 60, 1), "interactions": self.interactions, "counts": self.counts,
            "restarts": self.restarts, "backend_starts": self.backend.starts, "stub_model_calls": stub_calls,
            "live_model_calls": self.live_calls,
            "api_latency_ms": stats.summarize(self.api_lat),
            "render_latency_ms": stats.summarize(all_render),
            "render_latency_by_page_ms": {k: stats.summarize(v) for k, v in sorted(self.render_lat.items())},
            "restart_ready_s": stats.summarize(self.restart_ready),
            "offline_detect_ms": stats.summarize(self.offline_detect_lat),
            "reconnect_ms": stats.summarize(self.reconnect_lat),
            "backend_rss_mb": stats.growth(self.rss), "js_heap_mb": stats.growth(self.heap),
            "dom_nodes": stats.growth(self.nodes), "browser_rss_mb": stats.growth(self.browser_rss),
            "console_errors": {"total": len(self.console), "while_up": len(up_console),
                               "while_down": len(self.console) - len(up_console)},
            "failed_requests": {"total": len(self.netfail), "while_up": len(up_net)},
            "findings": len(self.findings),
            "by_severity": {s: sum(1 for f in self.findings if f["severity"] == s) for s in ("high", "medium", "low")},
        }
        (self.out / "metrics.json").write_text(json.dumps(metrics, ensure_ascii=False, indent=2), encoding="utf-8")
        (self.out / "findings.json").write_text(json.dumps(self.findings, ensure_ascii=False, indent=2), encoding="utf-8")
        uniq_console: dict[str, int] = {}
        for c in up_console:
            key = re.sub(r"\d+", "#", c["text"])[:160]
            uniq_console[key] = uniq_console.get(key, 0) + 1
        uniq_net: dict[str, int] = {}
        for n in up_net:
            key = f"{n.get('status', n.get('failure'))} {n.get('method', '')} " + re.sub(r"\d+", "#", n["url"].split("?")[0])
            uniq_net[key] = uniq_net.get(key, 0) + 1
        (self.out / "console_up.json").write_text(json.dumps(uniq_console, ensure_ascii=False, indent=1), encoding="utf-8")
        (self.out / "net_up.json").write_text(json.dumps(uniq_net, ensure_ascii=False, indent=1), encoding="utf-8")
        print(json.dumps(metrics, ensure_ascii=False, indent=1))
        return 1 if metrics["by_severity"]["high"] else 0


def main(argv=None) -> int:
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--port", type=int, default=8870)
    ap.add_argument("--stub-port", type=int, default=8878)
    ap.add_argument("--dead-port", type=int, default=8879, help="nothing listens here (model unreachable)")
    ap.add_argument("--data-dir", required=True)
    ap.add_argument("--out", required=True)
    ap.add_argument("--profile", default=r"C:\Users\asd\Bossman\rc19-ux-profile")
    ap.add_argument("--minutes", type=float, default=60)
    ap.add_argument("--max-interactions", type=int, default=600)
    ap.add_argument("--restart-every", type=int, default=25)
    ap.add_argument("--pause", type=float, default=1.0, help="seconds between interactions")
    ap.add_argument("--seed", type=int, default=19)
    ap.add_argument("--live-model", action="store_true")
    ap.add_argument("--headed", action="store_true")
    ap.add_argument("--script", default="", help="comma-separated interactions to run first (debug)")
    ap.add_argument("--launch", choices=("desktop", "server"), default="desktop",
                    help="desktop = the owner's shortcut (python -m bcc.desktop + its --app window, driven "
                         "over CDP); server = python -m bcc + a Playwright Chromium page")
    ap.add_argument("--cdp-port", type=int, default=8877)
    ap.add_argument("--browser", default=None, help="window browser (default: bcc.desktop.find_browser())")
    ap.add_argument("--pylib", default="", help="extra PYTHONPATH entry (prompt_toolkit as in the bundle)")
    args = ap.parse_args(argv)
    try:
        psutil.Process().nice(psutil.BELOW_NORMAL_PRIORITY_CLASS if sys.platform == "win32" else 10)
    except (psutil.Error, AttributeError):
        pass
    return Soak(args).run()


if __name__ == "__main__":
    sys.exit(main())
