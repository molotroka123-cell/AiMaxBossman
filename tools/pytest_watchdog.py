"""Watchdog для pytest на Windows: тесты прошли — а интерпретатор виснет на teardown.

Гонит pytest сабпроцессом, копит вывод; как только увидели финальную сводку —
даём 5 секунд на чистый выход и убиваем процесс (вывод уже сохранён).
Usage: python tools_dev/pytest_watchdog.py <pytest args...>
"""
from __future__ import annotations

import re
import subprocess
import sys
import time

SUMMARY = re.compile(r"(=+\s+.*\b(passed|failed|error|no tests ran)\b.*\s*=+)", re.I)
GRACE_S = 5.0
ABSOLUTE_S = 900.0


def main() -> int:
    proc = subprocess.Popen(
        [sys.executable, "-u", "-m", "pytest", *sys.argv[1:]],
        stdout=subprocess.PIPE,
        stderr=subprocess.STDOUT,
        text=True,
        encoding="utf-8",
        errors="replace",
    )
    lines: list[str] = []
    import os

    log_path = os.getenv("BOSSMAN_WATCHDOG_LOG")
    log_fh = open(log_path, "w", encoding="utf-8") if log_path else None
    done = threading_done = False
    import threading

    def reader() -> None:
        for line in proc.stdout:
            lines.append(line)
            if log_fh:
                try:
                    log_fh.write(line)
                    log_fh.flush()
                except Exception:
                    pass

    t = threading.Thread(target=reader, daemon=True)
    t.start()
    started = time.time()
    summary_seen_at: float | None = None
    last_count = 0
    last_progress_at: float | None = None
    try:
        while True:
            if proc.poll() is not None:
                break
            if len(lines) != last_count:
                last_count = len(lines)
                last_progress_at = time.time()
            if summary_seen_at is None:
                tail = "".join(lines[-40:])
                m = SUMMARY.search(tail)
                if m:
                    summary_seen_at = time.time()
            else:
                if time.time() - summary_seen_at > GRACE_S:
                    done = True
                    break
            # exit-hang: вывод шёл и замолчал (summary не долетел из буфера)
            if (
                last_progress_at is not None
                and time.time() - last_progress_at > 30.0
                and time.time() - started > 45.0
            ):
                done = True
                break
            if time.time() - started > ABSOLUTE_S:
                done = True
                break
            time.sleep(0.5)
    except KeyboardInterrupt:
        # «Протухший» Ctrl-C из общей консоли или сигнал инструмента: не теряем
        # накопленный вывод — добиваем ребёнка и печатаем что есть.
        done = True
    if proc.poll() is None:
        proc.kill()
        proc.wait(timeout=10)
    t.join(timeout=5)
    if log_fh:
        log_fh.close()
    out = "".join(lines)
    sys.stdout.write(out)
    if done and summary_seen_at is None:
        sys.stdout.write("\n[WATCHDOG] no summary seen: killed after silence/timeout (teardown hang?)\n")
    elif done:
        sys.stdout.write("\n[WATCHDOG] teardown hang: killed 5s after final summary\n")
    code = proc.returncode if proc.returncode is not None else 0
    # падение по тестам важнее нашего kill: если сводка есть — вернём её код
    return code


if __name__ == "__main__":
    raise SystemExit(main())
