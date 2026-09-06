"""Run pytest with a real deadline and bounded, redacted output.

Silence and a printed pytest summary are NOT process completion. Only the child
exit code is authoritative. Cleanup targets this launch's process tree, never
all python/browser/ffmpeg processes on the owner's machine.

BOSSMAN_WATCHDOG_TIMEOUT_SECONDS (default 900) and BOSSMAN_WATCHDOG_LOG
configure the deadline and optional log. psutil is required before launch so
Windows cleanup can bind process identity to creation time.
"""
from __future__ import annotations

from collections import deque
import math
import os
from pathlib import Path
import re
import subprocess
import sys
import threading
import time
from typing import TextIO

import psutil

_SECRET = re.compile(
    r'(?i)(authorization\s*[:=]\s*(?:(?:bearer|basic)\s+)?|bearer\s+|(?:api[_-]?key|access[_-]?token|'
    r'refresh[_-]?token|password|secret|cookie)\s*["\x27]?\s*[:=]\s*["\x27]?)([^\s,;]+)'
)


def redact(text: str) -> str:
    """Best-effort label redaction; raw logs must still stay local until reviewed."""
    return _SECRET.sub(lambda m: m.group(1) + '[REDACTED]', text)


def _remember(parent: psutil.Process, owned: dict[int, psutil.Process]) -> None:
    try:
        # is_running binds the Process object to PID + creation time.
        if parent.is_running():
            for child in parent.children(recursive=True):
                owned.setdefault(child.pid, child)
    except (psutil.NoSuchProcess, psutil.AccessDenied):
        pass


def _cleanup(owned: dict[int, psutil.Process], root_pid: int) -> list[int]:
    """Only identities observed as descendants of this launch are candidates."""
    targets = []
    denied: list[int] = []
    for proc in sorted(owned.values(), key=lambda p: p.pid == root_pid):
        try:
            if proc.pid != os.getpid() and proc.is_running():
                proc.terminate()
                targets.append(proc)
        except psutil.AccessDenied:
            denied.append(proc.pid)
        except psutil.NoSuchProcess:
            pass
    _, alive = psutil.wait_procs(targets, timeout=2)
    for proc in alive:
        try:
            if proc.is_running():
                proc.kill()
        except (psutil.NoSuchProcess, psutil.AccessDenied):
            pass
    _, alive = psutil.wait_procs(alive, timeout=2)
    # Zombies are already terminated; their real parent must reap them.
    residual = []
    for proc in alive:
        try:
            if proc.is_running() and proc.status() != psutil.STATUS_ZOMBIE:
                residual.append(proc.pid)
        except psutil.NoSuchProcess:
            pass
    return sorted(set(residual + denied))


def run_watchdog(argv: list[str], *, timeout_s: float = 900,
                 log_path: str | None = None, output: TextIO | None = None) -> int:
    if not argv or not math.isfinite(timeout_s) or timeout_s <= 0:
        raise ValueError('nonempty argv and finite positive timeout required')
    out = output or sys.stdout
    tail: deque[str] = deque(maxlen=200)
    log = open(Path(log_path), 'x', encoding='utf-8') if log_path else None
    reader_errors: list[Exception] = []
    proc = None
    thread = None
    owned: dict[int, psutil.Process] = {}
    code = 1
    reason = ''
    try:
        proc = subprocess.Popen(argv, stdout=subprocess.PIPE, stderr=subprocess.STDOUT,
                                text=True, encoding='utf-8', errors='replace',
                                start_new_session=os.name != 'nt', shell=False)
        try:
            root = psutil.Process(proc.pid)
            root.create_time()  # bind identity before collecting descendants
            owned[root.pid] = root
        except psutil.NoSuchProcess:
            root = None

        def reader() -> None:
            assert proc is not None and proc.stdout is not None
            discarding = False
            try:
                while True:
                    chunk = proc.stdout.readline(16384)
                    if not chunk:
                        break
                    # Drop overlong lines whole, so split credentials aren't leaked.
                    if not chunk.endswith('\n') and len(chunk) == 16384:
                        if not discarding:
                            safe = '[WATCHDOG] overlong output line omitted\n'
                        else:
                            continue
                        discarding = True
                    elif discarding:
                        discarding = False
                        continue
                    else:
                        safe = redact(chunk)
                    tail.append(safe)
                    if log:
                        log.write(safe)
                        log.flush()
            except Exception as exc:
                reader_errors.append(exc)

        thread = threading.Thread(target=reader, daemon=True)
        thread.start()
        started = time.monotonic()
        while proc.poll() is None:
            if root:
                _remember(root, owned)
            if reader_errors:
                reason, code = 'LOG_WRITE_FAILED', 1
                break
            if time.monotonic() - started >= timeout_s:
                reason, code = 'TIMEOUT', 124
                break
            time.sleep(0.05)
        else:
            code = proc.returncode if proc.returncode is not None else 1
    except KeyboardInterrupt:
        reason, code = 'INTERRUPTED', 130
    finally:
        residual = _cleanup(owned, proc.pid) if proc else []
        if proc:
            try:
                proc.wait(timeout=3)
            except subprocess.TimeoutExpired:
                # Popen still owns the unreaped direct child; no name/PID scan.
                proc.kill()
                proc.wait(timeout=3)
                reason, code = reason or 'CLEANUP_REQUIRED', code or 1
        if thread:
            thread.join(timeout=3)
            if thread.is_alive():
                reason, code = reason or 'OUTPUT_READER_STUCK', code or 1
        if reader_errors or residual:
            reason, code = reason or 'INCOMPLETE_CLEANUP_OR_LOG', code or 1
        if log and (not thread or not thread.is_alive()):
            log.close()
        out.write(''.join(tail))
        out.write(f'\n[WATCHDOG] exit={code} reason={reason or "PROCESS_EXIT"}'
                  f' owned_seen={len(owned)} residual_pids={residual}\n')
    return code


def main() -> int:
    try:
        timeout = float(os.getenv('BOSSMAN_WATCHDOG_TIMEOUT_SECONDS', '900'))
        return run_watchdog([sys.executable, '-u', '-m', 'pytest', *sys.argv[1:]],
                            timeout_s=timeout, log_path=os.getenv('BOSSMAN_WATCHDOG_LOG'))
    except (ValueError, OSError) as exc:
        print(f'[WATCHDOG] setup failed: {type(exc).__name__}', file=sys.stderr)
        return 2


if __name__ == '__main__':
    raise SystemExit(main())
