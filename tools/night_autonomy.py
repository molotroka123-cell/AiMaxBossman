#!/usr/bin/env python3
"""Bossman night autonomy (owner, 11.10.2026): work all night without Claude, and bring Claude in
whenever it is available.

What one cycle does, in order, each step independent of the others:
  1. archive check    tools/verify_windows_bundle.py on the built candidate (once per night);
  2. evening test     app-support/bundle_evening_test.py --full --ui-sweep from the unpacked bundle
                      (the UX pass of the real build; once per night, re-run after a Claude fix);
  3. security audit   tools/security_audit.ps1 (read-only), diff against the previous run;
  4. dialogue         Bossman writes a question to Claude (the findings), runs `claude -p` headless
                      in the checkout; if Claude is rate-limited or absent, records CLAUDE_UNAVAILABLE
                      and retries next cycle — the night does not stop;
  5. report           a short Russian report to the owner's pult (owner_report --send).
Evidence: docs/evidence/night-<date>/cycle-<n>.json, dialogue.jsonl, report.md.
STOP file: <evidence dir>/STOP or %LOCALAPPDATA%\\Bossman\\remote\\STOP ends the loop after the step in progress.
Never touches the owner's data dir, never force-pushes, never merges; Claude runs with the permission mode
the task passes (owner asked bypassPermissions) and the same repo rules (AGENTS.md).

  python tools/night_autonomy.py --archive <zip> --until 08:00 --every 45 [--once]
"""
from __future__ import annotations

import argparse
import hashlib
import json
import os
import re
import shutil
import subprocess
import sys
import time
import zipfile
from datetime import datetime, timedelta
from pathlib import Path

REPO = Path(__file__).resolve().parents[1]
CC = REPO / "command-center"
PY = sys.executable
CLAUDE_TIMEOUT_S = 50 * 60
RATE_LIMIT_RE = re.compile(r"rate.?limit|usage limit|limit reached|overloaded|429|quota|try again later", re.I)
SECRET_ENV_RE = re.compile(r"TOKEN|SECRET|PASSW|API_KEY|_KEY$|^BCC_|^TG_COMPANION_|CREDENTIAL", re.I)
SECRET_TEXT_RE = re.compile(r"(?i)(token|secret|key|password|auth|bearer)[\s:=]+\S+")


def sanitize(text: str) -> str:
    """Anything that looks like a credential in a log tail becomes *** before it leaves the PC."""
    return SECRET_TEXT_RE.sub(lambda m: m.group(1) + "=***", text)


def now() -> str:
    return datetime.now().strftime("%Y-%m-%dT%H:%M:%S")


def run(cmd: list[str], *, cwd: Path, timeout: float, env: dict | None = None) -> tuple[int | None, str]:
    try:
        p = subprocess.run(cmd, cwd=str(cwd), capture_output=True, timeout=timeout, env=env)
        return p.returncode, (p.stdout + p.stderr).decode("utf-8", "replace")
    except subprocess.TimeoutExpired as exc:
        return None, ((exc.stdout or b"") + (exc.stderr or b"")).decode("utf-8", "replace") + "\nTIMEOUT"
    except OSError as exc:
        return -1, f"{type(exc).__name__}: {exc}"


class Night:
    def __init__(self, a: argparse.Namespace):
        self.a = a
        self.ev = REPO / "docs" / "evidence" / f"night-{datetime.now():%Y%m%d}"
        self.ev.mkdir(parents=True, exist_ok=True)
        self.state_path = self.ev / "state.json"
        self.state = json.loads(self.state_path.read_text(encoding="utf-8")) if self.state_path.is_file() else {}
        self.state.setdefault("cycles", 0)

    # ------------------------------------------------------------ helpers
    def save(self):
        self.state_path.write_text(json.dumps(self.state, ensure_ascii=False, indent=1), encoding="utf-8")

    def stop_requested(self) -> bool:
        remote_stop = Path(os.environ.get("LOCALAPPDATA", "")) / "Bossman" / "remote" / "STOP"
        return (self.ev / "STOP").is_file() or remote_stop.is_file()

    def pult(self, text: str, *, pin: bool = False):
        f = self.ev / "pult-last.txt"
        f.write_text(text, encoding="utf-8")
        config = Path(os.environ.get("LOCALAPPDATA", "")) / "Bossman" / "telegram-companion" / "config.json"
        cmd = [PY, "-X", "utf8", "-m", "bcc.telegram_companion.owner_report", "--file", str(f), "--send",
               "--config", str(config)]
        if pin:
            cmd.append("--pin")
        code, out = run(cmd, cwd=CC, timeout=120)
        return code == 0, out[-300:]

    # ------------------------------------------------------------ steps
    def step_verify(self) -> dict:
        if self.state.get("verify"):
            return self.state["verify"]
        archive = Path(self.a.archive)
        sha = self.state.get("source_sha") or run(["git", "rev-parse", "HEAD"], cwd=REPO, timeout=30)[1].strip()
        self.state["source_sha"] = sha
        if not archive.is_file():
            res = {"status": "BLOCKED", "detail": f"archive missing: {archive.name}"}
        else:
            digest = hashlib.sha256(archive.read_bytes()).hexdigest()
            out_dir = self.ev / "verify-report.json"
            code, out = run([PY, "tools/verify_windows_bundle.py", "--archive", str(archive), "--expected-sha", sha,
                             "--out", str(out_dir)], cwd=REPO, timeout=1800)
            res = {"status": "PASS" if code == 0 else "FAIL", "exit": code, "sha256": digest, "tail": out[-800:]}
        self.state["verify"] = res
        self.save()
        return res

    def step_unpack(self) -> Path | None:
        target = self.ev / "install"
        if (target / "bundle").is_dir():
            return next(iter((target / "bundle").glob("BOSSMAN-Windows-x64-*")), None)
        archive = Path(self.a.archive)
        if not archive.is_file():
            return None
        (target / "bundle").mkdir(parents=True, exist_ok=True)
        with zipfile.ZipFile(archive) as z:
            z.extractall(target / "bundle")
        return next(iter((target / "bundle").glob("BOSSMAN-Windows-x64-*")), None)

    def step_evening(self, force: bool = False) -> dict:
        if self.state.get("evening") and not force:
            return self.state["evening"]
        root = self.step_unpack()
        if root is None:
            res = {"status": "BLOCKED", "detail": "bundle not unpacked"}
        else:
            runtime = root / "runtime" / "python.exe"
            test = root / "app-support" / "bundle_evening_test.py"
            data = self.ev / "install" / "data"
            env = {**os.environ, "BCC_DATA_DIR": str(data), "BOSSMAN_DATA_DIR": str(data)}
            code, out = run([str(runtime), "-I", "-X", "utf8", str(test), "--full", "--ui-sweep"], cwd=root,
                            timeout=3600, env=env)
            (self.ev / "evening-test.log").write_text(out, encoding="utf-8")
            verdict = re.findall(r"(BUNDLE_EVENING_TEST|VERDICT|EVENING_TEST)[=: ]+([A-Z_]+)", out)
            res = {"status": "PASS" if code == 0 else "FAIL", "exit": code, "verdict": verdict[-1] if verdict else None,
                   "tail": out[-1500:]}
        self.state["evening"] = res
        self.save()
        return res

    def step_security(self) -> dict:
        code, out = run(["powershell", "-NoProfile", "-ExecutionPolicy", "Bypass", "-File", "tools/security_audit.ps1",
                         "-Quiet"], cwd=REPO, timeout=600)
        sec = Path(os.environ.get("LOCALAPPDATA", "")) / "Bossman" / "security"
        latest = sorted(sec.glob("audit-*.json")) if sec.is_dir() else []
        if not latest:
            res = {"status": "BLOCKED", "detail": f"audit produced no report (exit {code}): {out[-300:]}"}
            self.state["security"] = res
            self.save()
            return res
        rows = json.loads(latest[-1].read_text(encoding="utf-8-sig"))
        bad = {r["id"]: r["status"] for r in rows if r.get("status") in ("FAIL", "WARN")}
        prev = self.state.get("security", {}).get("bad", {})
        changed = {k: v for k, v in bad.items() if prev.get(k) != v}
        res = {"status": "FAIL" if any(v == "FAIL" for v in bad.values()) else "PASS", "bad": bad, "changed": changed}
        self.state["security"] = res
        self.save()
        return res

    def step_dialogue(self, findings: dict) -> dict:
        """Bossman -> Claude -> Bossman. The question is Bossman's own summary; the answer is Claude's."""
        question = {
            "from": "bossman", "at": now(), "cycle": self.state["cycles"],
            "text": ("Bossman ночной прогон. Проверь результаты и исправь реальные дефекты в этом checkout: "
                     "тесты не ослаблять, коммит явными файлами, fast-forward push в текущую ветку, без merge в main. "
                     "В конце ответь JSON {\"to\": \"bossman\", \"done\": [...], \"blocked\": [...], \"next\": \"...\"}.\n"
                     + json.dumps(findings, ensure_ascii=False)[:12000]),
        }
        with (self.ev / "dialogue.jsonl").open("a", encoding="utf-8") as fh:
            fh.write(json.dumps(question, ensure_ascii=False) + "\n")
        binary = shutil.which("claude")
        if not binary:
            res = {"status": "CLAUDE_UNAVAILABLE", "detail": "claude CLI not on PATH"}
        else:
            # No parent-session variables and no secret-looking variables reach the child (review 11.10).
            env = {k: v for k, v in os.environ.items()
                   if k not in ("CLAUDECODE", "CLAUDE_CODE_ENTRYPOINT", "CLAUDE_CODE_SSE_PORT")
                   and not SECRET_ENV_RE.search(k)}
            cmd = [binary, "-p", "--output-format", "json", "--permission-mode", self.a.permission_mode]
            try:
                p = subprocess.run(cmd, cwd=str(REPO), input=question["text"].encode("utf-8"), capture_output=True,
                                   timeout=CLAUDE_TIMEOUT_S, env=env)
                out = p.stdout.decode("utf-8", "replace"); err = p.stderr.decode("utf-8", "replace")
                try:
                    data = json.loads(out.strip().splitlines()[-1])
                    text = str(data.get("result") or "")
                    limited = bool(data.get("is_error")) and bool(RATE_LIMIT_RE.search(text))
                    res = {"status": "CLAUDE_UNAVAILABLE" if limited else ("PASS" if not data.get("is_error") else "FAIL"),
                           "answer": text[-6000:], "cost_usd": data.get("total_cost_usd"), "session": data.get("session_id")}
                except (ValueError, IndexError):
                    tail = (err or out)[-1500:]
                    res = {"status": "CLAUDE_UNAVAILABLE" if RATE_LIMIT_RE.search(tail) or p.returncode != 0 else "FAIL",
                           "answer": tail}
            except subprocess.TimeoutExpired:
                res = {"status": "FAIL", "answer": "claude timeout"}
        answer = {"from": "claude", "at": now(), "cycle": self.state["cycles"], **res}
        with (self.ev / "dialogue.jsonl").open("a", encoding="utf-8") as fh:
            fh.write(json.dumps(answer, ensure_ascii=False) + "\n")
        self.state.setdefault("claude", []).append({"at": answer["at"], "status": res["status"]})
        self.save()
        return res

    # ------------------------------------------------------------ cycle
    def cycle(self) -> dict:
        self.state["cycles"] += 1
        n = self.state["cycles"]
        rec = {"cycle": n, "started": now()}
        for name, fn in (("verify", self.step_verify), ("evening", self.step_evening), ("security", self.step_security)):
            if self.stop_requested():
                rec["stopped"] = True
                break
            try:
                rec[name] = fn()
            except Exception as exc:  # noqa: BLE001 - one broken step must not end the night
                rec[name] = {"status": "ERROR", "detail": f"{type(exc).__name__}: {exc}"[:400]}
        findings = {k: {kk: vv for kk, vv in v.items() if kk != "tail"} for k, v in rec.items() if isinstance(v, dict)}
        findings["evening_tail"] = sanitize((rec.get("evening") or {}).get("tail", "")[-2500:])
        if not rec.get("stopped"):
            rec["dialogue"] = self.step_dialogue(findings)
            if rec["dialogue"].get("status") == "PASS":
                # Claude may have fixed something: the evening test is the proof, so it runs again.
                rec["evening_after_claude"] = self.step_evening(force=True)
        rec["finished"] = now()
        (self.ev / f"cycle-{n:02d}.json").write_text(json.dumps(rec, ensure_ascii=False, indent=1), encoding="utf-8")
        self.report(rec)
        return rec

    def report(self, rec: dict):
        def st(k):
            v = rec.get(k) or {}
            return v.get("status", "—") if isinstance(v, dict) else "—"
        lines = [f"🌙 Bossman ночь, цикл {rec['cycle']} ({rec['started'][11:16]}–{rec['finished'][11:16]})",
                 f"архив: {st('verify')} · UX-тест сборки: {st('evening')} ({(rec.get('evening') or {}).get('verdict')})"
                 f" · безопасность: {st('security')}",
                 f"Claude: {st('dialogue')}" + (f" · после фикса UX: {st('evening_after_claude')}" if rec.get("evening_after_claude") else "")]
        changed = (rec.get("security") or {}).get("changed")
        if changed:
            lines.append("изменения безопасности: " + ", ".join(f"{k}={v}" for k, v in changed.items()))
        ans = (rec.get("dialogue") or {}).get("answer", "")
        m = re.search(r"\{[^{}]*\"to\"\s*:\s*\"bossman\"[^{}]*\}", ans or "")
        if m:
            lines.append("ответ Claude Bossman'у: " + m.group(0)[:600])
        elif ans:
            lines.append("Claude: " + ans[-400:].replace("\n", " "))
        lines.append(f"улики: docs/evidence/{self.ev.name}/cycle-{rec['cycle']:02d}.json")
        text = "\n".join(lines)
        (self.ev / "report.md").open("a", encoding="utf-8").write(text + "\n\n")
        self.pult(text)

    def loop(self):
        until = datetime.strptime(self.a.until, "%H:%M").replace(year=datetime.now().year, month=datetime.now().month,
                                                                   day=datetime.now().day)
        if until <= datetime.now():
            until += timedelta(days=1)
        self.pult(f"🌙 Bossman: ночной автономный прогон начат ({now()[11:16]}), до {self.a.until}, цикл каждые "
                  f"{self.a.every} мин. Стоп: файл docs/evidence/{self.ev.name}/STOP.")
        while True:
            rec = self.cycle()
            if self.a.once or rec.get("stopped") or datetime.now() >= until:
                break
            wake = time.time() + self.a.every * 60
            while time.time() < wake:
                if self.stop_requested():
                    break
                time.sleep(30)
            if self.stop_requested():
                break
        claude_ok = sum(1 for c in self.state.get("claude", []) if c["status"] == "PASS")
        self.pult(f"🌙 Bossman: ночь завершена. Циклов: {self.state['cycles']}, Claude отвечал: {claude_ok}, "
                  f"недоступен: {sum(1 for c in self.state.get('claude', []) if c['status'] == 'CLAUDE_UNAVAILABLE')}. "
                  f"Отчёт: docs/evidence/{self.ev.name}/report.md", pin=True)


def main(argv=None) -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--archive", required=True)
    ap.add_argument("--until", default="08:00")
    ap.add_argument("--every", type=int, default=45)
    ap.add_argument("--permission-mode", default="bypassPermissions")
    ap.add_argument("--once", action="store_true")
    a = ap.parse_args(argv)
    Night(a).loop()
    return 0


if __name__ == "__main__":
    sys.exit(main())
