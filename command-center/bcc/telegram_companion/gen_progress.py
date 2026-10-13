"""Generation progress plate for the pult: one status message, edited in place.

Owner, 2026-10-13: «Добавь параметр плашку с ответом по генерации сколько % осталось».
While a Studio job runs, the status message sent by ``Companion.generate`` is edited about every
25 s with a line like «▓▓▓▓░░░░░░ 42% · шаг 9/20 · осталось ~6 мин». The numbers come from
``GET /api/studio/jobs/{id}`` (``progress`` + ``progress_detail``, parsed from the engine output by
``bcc.studio.progress``). Without a ``progress_detail`` the plate says «прогресс неизвестен»: the
0.05 the queue writes at claim time is not a measurement and is never shown as a percentage.

Telegram limits: one edit per ``interval`` (25 s), no edit when the text did not change, and after
a refused/rate-limited edit the interval doubles (up to 2 min). An edit failure never touches the
generation itself.
"""
from __future__ import annotations

import math
import time

EDIT_EVERY_S = 25.0
MAX_EVERY_S = 120.0
BAR_CELLS = 10

PHASE_RU = {
    "load": "загрузка модели",
    "encode": "разбор промпта",
    "sample": "шаги",
    "decode": "сборка кадров (VAE)",
    "save": "сохранение",
    "verify": "проверка файла",
}


def bar(fraction: float, cells: int = BAR_CELLS) -> str:
    filled = max(0, min(cells, int(fraction * cells + 1e-9)))
    return "▓" * filled + "░" * (cells - filled)


def eta_text(seconds) -> str | None:
    if not isinstance(seconds, (int, float)) or isinstance(seconds, bool) or seconds < 0 or math.isnan(seconds):
        return None
    if seconds < 90:
        return f"осталось ~{max(5, int(round(seconds / 5.0)) * 5)} с"
    return f"осталось ~{int(math.ceil(seconds / 60.0))} мин"


def progress_line(job: dict | None) -> str:
    """One Russian line for the plate. Unknown progress is said out loud, never invented."""
    job = job or {}
    status = job.get("status")
    if status in ("queued", "pending"):
        return "⏳ в очереди Студии · прогресс неизвестен"
    detail = job.get("progress_detail")
    value = job.get("progress")
    if (not isinstance(detail, dict) or not detail.get("known")
            or not isinstance(value, (int, float)) or isinstance(value, bool)):
        return "⏳ прогресс неизвестен"
    fraction = max(0.0, min(1.0, float(value)))
    if status != "completed":
        fraction = min(fraction, 0.99)          # 100% only for a finished, verified job
    parts = [f"{bar(fraction)} {int(fraction * 100)}%"]
    phase = detail.get("phase")
    step, steps = detail.get("step"), detail.get("steps")
    if phase == "sample" and isinstance(step, int) and isinstance(steps, int) and steps > 0:
        parts.append(f"шаг {step}/{steps}")
    elif phase in PHASE_RU:
        parts.append(PHASE_RU[phase])
    segments = detail.get("segments")
    if isinstance(segments, int) and segments > 1:
        parts.append(f"часть {detail.get('segment')}/{segments}")
    eta = eta_text(detail.get("eta_s"))
    if eta:
        parts.append(eta)
    return " · ".join(parts)


class GenProgressPlate:
    """Edits the generation status message in place; throttled; failures are swallowed."""

    def __init__(self, telegram, person, message_id, header: str, keyboard=None, *,
                 every_s: float | None = None, clock=time.monotonic):
        self.telegram, self.person, self.message_id = telegram, person, message_id
        self.header, self.keyboard = header, keyboard
        self.every_s = EDIT_EVERY_S if every_s is None else every_s
        self.clock = clock
        self.last_at = clock()           # the message itself was just sent
        self.last_text = header
        self.edits = 0

    @property
    def usable(self) -> bool:
        return (type(self.message_id) is int and self.message_id > 0
                and callable(getattr(self.telegram, "edit_message", None)))

    async def _edit(self, text: str, keyboard) -> bool:
        edit = self.telegram.edit_message
        try:
            try:
                await edit(self.person, self.message_id, text, keyboard=keyboard)
            except TypeError:            # an adapter without keyboard support
                await edit(self.person, self.message_id, text)
        except Exception:  # noqa: BLE001 - cosmetic edit; the generation must go on
            self.every_s = min(self.every_s * 2, MAX_EVERY_S)
            return False
        self.edits += 1
        return True

    async def tick(self, job: dict | None) -> bool:
        if not self.usable:
            return False
        now = self.clock()
        if now - self.last_at < self.every_s:
            return False
        text = f"{self.header}\n{progress_line(job)}"
        self.last_at = now
        if text == self.last_text:
            return False
        ok = await self._edit(text, self.keyboard)
        if ok:
            self.last_text = text
        return ok

    async def done(self, seconds) -> bool:
        """After the media is delivered: 100% only now, the file was verified (sha256)."""
        if not self.usable or self.edits == 0:
            return False                 # nothing was shown: no extra edit for a quick job
        return await self._edit(f"{self.header}\n{bar(1.0)} 100% · готово за {int(seconds)} с, файл проверен",
                                None)
