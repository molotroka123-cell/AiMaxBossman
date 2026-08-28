"""The persistent runtime.

Jobs are how the control plane asks for one thing. This is how the service
lives: one long-running loop that samples the room, survives a camera that
goes away, survives a network that drops, backs off with a capped delay
instead of hammering the camera, and never spins.

Two deliberate differences from the per-capture retry in
:mod:`ai_webcam_vision.transport.retry`:

* the attempt budget there is finite — one capture must not hang forever.
  Here there is no budget: a clinic camera that has been offline for an hour
  must still be picked up when it comes back;
* the delay here is bounded by ``retry.max_delay``. "Bounded backoff", not
  "bounded attempts", is what a permanent service needs.
"""

from __future__ import annotations

import asyncio
from enum import StrEnum

from ..errors import BaselineMissing, DependencyMissing, VisionError
from ..logging_setup import get_logger

log = get_logger("runtime")

#: Never sleep less than this between cycles, whatever the configuration says.
#: A sampling loop with a zero wait is a busy loop, and a busy loop on a video
#: pipeline burns a core and starves the event loop.
MIN_CYCLE_SECONDS = 0.05


class RuntimeState(StrEnum):
    STOPPED = "stopped"
    STARTING = "starting"
    RUNNING = "running"
    RECOVERING = "recovering"
    STOPPING = "stopping"


class RuntimeSupervisor:
    """Owns the long-lived sampling loop for one :class:`VisionService`."""

    def __init__(
        self,
        service,
        *,
        sleep=None,
        min_cycle_seconds: float = MIN_CYCLE_SECONDS,
        stop_timeout: float = 10.0,
    ) -> None:
        self.service = service
        self.min_cycle_seconds = max(float(min_cycle_seconds), 1e-3)
        self.stop_timeout = stop_timeout
        self.state = RuntimeState.STOPPED
        self.cycles = 0
        self.loops_started = 0
        self.consecutive_failures = 0
        self.recoveries = 0
        self.unexpected_errors = 0
        self.last_error: str | None = None
        self.last_error_code: str | None = None
        self.recorded_delays: list[float] = []
        self._sleep = sleep
        self._stop = asyncio.Event()
        self._task: asyncio.Task | None = None
        self._tick = asyncio.Event()
        self._state_changed = asyncio.Event()

    # ------------------------------------------------------------ lifecycle
    @property
    def running(self) -> bool:
        return self._task is not None and not self._task.done()

    async def start(self) -> None:
        if self.running:
            return
        self._stop.clear()
        self._set_state(RuntimeState.STARTING)
        self.loops_started += 1
        self._task = asyncio.create_task(self._loop(), name="runtime:supervisor")

    async def stop(self) -> None:
        """Stop the loop and wait for it. Idempotent, and bounded in time."""
        if self._task is None:
            self._set_state(RuntimeState.STOPPED)
            return
        self._set_state(RuntimeState.STOPPING)
        self._stop.set()
        task, self._task = self._task, None
        if not task.done():
            done, _ = await asyncio.wait({task}, timeout=self.stop_timeout)
            if not done:
                task.cancel()
                await asyncio.gather(task, return_exceptions=True)
        else:
            await asyncio.gather(task, return_exceptions=True)
        self._set_state(RuntimeState.STOPPED)

    # ----------------------------------------------------------- observation
    def _set_state(self, state: RuntimeState) -> None:
        if state is not self.state:
            self.state = state
            self._state_changed.set()
            self._state_changed = asyncio.Event()

    async def wait_for_cycles(self, count: int, timeout: float = 10.0) -> bool:
        loop = asyncio.get_running_loop()
        deadline = loop.time() + timeout
        while self.cycles < count:
            remaining = deadline - loop.time()
            if remaining <= 0:
                return False
            waiter = self._tick
            try:
                await asyncio.wait_for(waiter.wait(), timeout=remaining)
            except asyncio.TimeoutError:
                return False
        return True

    async def wait_for_state(self, state: RuntimeState, timeout: float = 10.0) -> bool:
        loop = asyncio.get_running_loop()
        deadline = loop.time() + timeout
        while self.state is not state:
            remaining = deadline - loop.time()
            if remaining <= 0:
                return False
            waiter = self._state_changed
            try:
                await asyncio.wait_for(waiter.wait(), timeout=remaining)
            except asyncio.TimeoutError:
                return False
        return True

    def status(self) -> dict:
        return {
            "state": self.state.value,
            "running": self.running,
            "cycles": self.cycles,
            "loops_started": self.loops_started,
            "consecutive_failures": self.consecutive_failures,
            "recoveries": self.recoveries,
            "unexpected_errors": self.unexpected_errors,
            "min_cycle_seconds": self.min_cycle_seconds,
            "max_backoff_seconds": self.service.settings.retry.max_delay,
            "last_error": self.last_error,
            "last_error_code": self.last_error_code,
        }

    # ----------------------------------------------------------------- loop
    async def _wait(self, delay: float) -> None:
        delay = max(delay, self.min_cycle_seconds)
        self.recorded_delays.append(delay)
        if self._sleep is not None:
            await self._sleep(delay)
            return
        try:
            # Waking on the stop signal keeps shutdown fast even in the middle
            # of a long reconnect backoff.
            await asyncio.wait_for(self._stop.wait(), timeout=delay)
        except asyncio.TimeoutError:
            pass

    def _next_backoff(self) -> float:
        retry = self.service.settings.retry
        base = retry.base_delay if retry.base_delay > 0 else self.min_cycle_seconds
        delay = base * (retry.factor ** max(0, self.consecutive_failures - 1))
        return min(delay, retry.max_delay)

    def _record_failure(self, exc: BaseException, code: str) -> None:
        self.consecutive_failures += 1
        self.last_error = getattr(exc, "safe_message", None) or code
        self.last_error_code = code
        self._set_state(RuntimeState.RECOVERING)

    def _record_success(self) -> None:
        if self.consecutive_failures:
            self.recoveries += 1
            log.info("runtime recovered after %s failed cycles", self.consecutive_failures)
        self.consecutive_failures = 0
        self.last_error = None
        self.last_error_code = None
        self._set_state(RuntimeState.RUNNING)

    async def _loop(self) -> None:
        log.info("runtime loop started")
        try:
            while not self._stop.is_set() and not self.service.stopping:
                delay = await self._cycle()
                self.cycles += 1
                self._tick.set()
                self._tick = asyncio.Event()
                if self._stop.is_set() or self.service.stopping:
                    break
                await self._wait(delay)
        except asyncio.CancelledError:
            raise
        finally:
            log.info("runtime loop stopped after %s cycles", self.cycles)

    async def _cycle(self) -> float:
        """One sampling cycle. Returns how long to wait before the next one."""
        try:
            await self.service.sample_once()
        except BaselineMissing as exc:
            # The detector is not ready. Waiting is the only correct action;
            # retrying at full rate would just hammer the camera for nothing.
            self._record_failure(exc, "baseline_missing")
            return self._next_backoff()
        except DependencyMissing as exc:
            # Retrying will not install ffmpeg. Wait the maximum.
            self._record_failure(exc, "dependency_missing")
            return self.service.settings.retry.max_delay
        except VisionError as exc:
            self._record_failure(exc, getattr(exc, "code", "vision_error"))
            return self._next_backoff()
        except asyncio.CancelledError:
            raise
        except Exception as exc:  # noqa: BLE001 - the loop must outlive surprises
            self.unexpected_errors += 1
            log.error("unexpected error in the runtime loop")
            self._record_failure(exc, "internal_error")
            return self._next_backoff()
        self._record_success()
        return self.service._sample_interval()
