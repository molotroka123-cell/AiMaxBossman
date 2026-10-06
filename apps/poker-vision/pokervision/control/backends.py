"""Click backends. The executor decides IF and WHAT; a backend only performs one press on a located target.

* PointerBackend  - screen pointer at mapped coordinates (Playwright page mouse in the sandbox; pyautogui/Win32 SendInput on Windows).
* ComputerUseBackend - delegates to Bossman's existing Computer Use (`computer.act`), addressing the element by NAME. The Computer Use
  perimeter (allow-listed processes, one owner approval per action, STOP epoch) stays in force: whatever it refuses is a refusal here."""
from __future__ import annotations

import asyncio
from typing import Any, Awaitable, Callable, Protocol


class ClickRefused(RuntimeError):
    pass


class Backend(Protocol):
    name: str
    def press(self, label: str, point: tuple[float, float] | None) -> dict: ...


class PointerBackend:
    name = "pointer"

    def __init__(self, click: Callable[[float, float], None]):
        self._click = click

    def press(self, label, point):
        if point is None:
            raise ClickRefused("no screen point")
        self._click(point[0], point[1])
        return {"via": "pointer", "point": [round(point[0], 1), round(point[1], 1)]}


class ComputerUseBackend:
    name = "computer_use"

    def __init__(self, observe: Callable[[], Awaitable[dict]], act: Callable[[dict], Awaitable[dict]], loop: asyncio.AbstractEventLoop | None = None):
        self._observe, self._act, self._loop = observe, act, loop

    def _run(self, coro):
        if self._loop is not None:
            return asyncio.run_coroutine_threadsafe(coro, self._loop).result(30)
        return asyncio.run(coro)

    def press(self, label, point):
        try:
            obs = self._run(self._observe())
            res = self._run(self._act({"action": "click", "target": label, "generation": obs.get("generation")}))
        except Exception as exc:  # noqa: BLE001 - Computer Use refusals (allow-list, approval, STOP) surface verbatim
            raise ClickRefused(f"computer.act refused: {exc}") from exc
        return {"via": "computer_use", "result": {k: res.get(k) for k in ("ok", "verified", "action") if isinstance(res, dict)}}
