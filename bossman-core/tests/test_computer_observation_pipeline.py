"""Latency and disk behaviour of one desktop observation.

Three measured defects in the observation path, all on the owner's Windows host:

* ``Observer.observe`` awaited foreground, then the UI tree, then the screenshot
  strictly in sequence, so one observation cost the SUM of three operations and
  its fragments described three different moments.
* ``WindowsDesktop`` resolved the foreground window twice per observation (once
  in ``foreground``, once in ``ui_tree``); the UIA ``Desktop`` wrapper is the
  most expensive part of the call.
* ``ui_tree`` called ``descendants()`` — which materialises the entire subtree of
  the window — and only then sliced it to 500 elements.

Plus a disk defect: every observation wrote a PNG into the temp directory and
nothing ever deleted it.
"""
import asyncio
import time

import pytest

from bossman.computer_operator.adapters.screenshot import LocalScreenshotProvider
from bossman.computer_operator.adapters.windows import MAX_TREE_NODES, WindowsDesktop
from bossman.computer_operator.observer import Observer


class SlowStructured:
    """No ``snapshot``: exercises the two-call fallback path."""

    def __init__(self, delay=0.05):
        self.delay = delay
        self.foreground_calls = 0
        self.tree_calls = 0

    async def foreground(self):
        self.foreground_calls += 1
        await asyncio.sleep(self.delay)
        return {"title": "Notepad", "app": "notepad.exe"}

    async def ui_tree(self):
        self.tree_calls += 1
        await asyncio.sleep(self.delay)
        return {"elements": [{"name": "Save"}]}


class SnapshotStructured(SlowStructured):
    """Declares ``snapshot``: one call, one window resolution."""

    def __init__(self, delay=0.05):
        super().__init__(delay)
        self.snapshot_calls = 0

    async def snapshot(self):
        self.snapshot_calls += 1
        await asyncio.sleep(self.delay)
        return {"title": "Notepad", "app": "notepad.exe"}, {"elements": [{"name": "Save"}]}


class SlowScreenshot:
    def __init__(self, delay=0.05):
        self.delay = delay
        self.calls = 0

    async def capture(self):
        self.calls += 1
        await asyncio.sleep(self.delay)
        return "/tmp/screen.png", False


async def test_structured_and_screenshot_are_captured_concurrently():
    """Serial capture cost delay*2 (fallback) + delay for the shot. Concurrent
    capture costs the slower branch, and the margin here is wide enough that a
    regression to sequential awaits cannot pass."""
    structured, shot = SnapshotStructured(0.05), SlowScreenshot(0.05)
    started = time.monotonic()
    obs = await Observer(structured, shot).observe(generation=3)
    elapsed = time.monotonic() - started
    assert obs.foreground["app"] == "notepad.exe" and obs.ui_tree == {"elements": [{"name": "Save"}]}
    assert obs.screenshot_ref == "/tmp/screen.png" and obs.generation == 3
    assert elapsed < 0.09, f"observation still serialised: {elapsed:.3f}s for two 50 ms sources"


async def test_snapshot_capable_provider_resolves_the_window_once():
    structured = SnapshotStructured(0)
    await Observer(structured, SlowScreenshot(0)).observe(generation=0)
    assert structured.snapshot_calls == 1
    assert structured.foreground_calls == 0 and structured.tree_calls == 0


async def test_provider_without_snapshot_still_works():
    structured = SlowStructured(0)
    obs = await Observer(structured, SlowScreenshot(0)).observe(generation=0)
    assert structured.foreground_calls == 1 and structured.tree_calls == 1
    assert obs.foreground["title"] == "Notepad"


async def test_summarizer_still_sees_every_fragment():
    seen = {}

    async def summarizer(*, foreground, ui_tree, screenshot_ref, sensitive):
        seen.update(foreground=foreground, ui_tree=ui_tree, ref=screenshot_ref, sensitive=sensitive)
        return "summary text"

    obs = await Observer(SnapshotStructured(0), SlowScreenshot(0), summarizer).observe(generation=1)
    assert obs.summary == "summary text"
    assert seen["ref"] == "/tmp/screen.png" and seen["ui_tree"]["elements"][0]["name"] == "Save"


# ------------------------------------------------------------- bounded UI walk
class _Node:
    """Counts how many children() round trips the walk actually costs."""
    visits = 0

    def __init__(self, name, depth, breadth, max_depth):
        self.name = name
        self.depth, self.breadth, self.max_depth = depth, breadth, max_depth
        self.element_info = type("E", (), {"name": name, "control_type": "Button",
                                           "automation_id": name})()

    def children(self):
        type(self).visits += 1
        if self.depth >= self.max_depth:
            return []
        return [_Node(f"{self.name}.{i}", self.depth + 1, self.breadth, self.max_depth)
                for i in range(self.breadth)]

    def descendants(self, title=None):       # the whole subtree, as pywinauto does
        out = []
        stack = [self]
        while stack:
            node = stack.pop()
            kids = node.children()
            out.extend(kids)
            stack.extend(kids)
        return out


def test_walk_stops_at_the_node_cap_instead_of_materialising_the_subtree():
    _Node.visits = 0
    root = _Node("root", 0, 8, 8)                      # 8**8 nodes if fully walked
    elements = WindowsDesktop._walk(root)
    assert len(elements) == MAX_TREE_NODES
    assert elements[0]["control_type"] == "Button" and elements[0]["automation_id"] == "root.0"
    # One children() call per expanded node, and expansion stops at the cap.
    assert _Node.visits <= MAX_TREE_NODES, f"{_Node.visits} COM round trips for {MAX_TREE_NODES} nodes"


def test_walk_respects_the_depth_bound():
    root = _Node("root", 0, 1, 100)                    # a single very deep chain
    assert len(WindowsDesktop._walk(root)) == 12       # MAX_TREE_DEPTH


def test_walk_falls_back_to_descendants_for_providers_without_children():
    class Old:
        def descendants(self, title=None):
            return [_Node(f"n{i}", 0, 0, 0) for i in range(3)]

    assert [e["name"] for e in WindowsDesktop._walk(Old())] == ["n0", "n1", "n2"]


# ------------------------------------------------------------ screenshot disk
async def test_screenshot_files_are_retained_within_a_bounded_window(tmp_path, monkeypatch):
    provider = LocalScreenshotProvider(tmp_path, retention=3)
    written = []

    async def fake_to_thread(fn):
        p = tmp_path / f"screen-{len(written)}.png"
        p.write_bytes(b"png")
        written.append(p)
        return str(p), False

    monkeypatch.setattr("bossman.computer_operator.adapters.screenshot.asyncio.to_thread",
                        lambda fn: fake_to_thread(fn))
    for _ in range(10):
        await provider.capture()
    on_disk = sorted(p.name for p in tmp_path.glob("screen-*.png"))
    assert len(on_disk) == 3, f"unbounded screenshot retention: {on_disk}"
    assert on_disk == ["screen-7.png", "screen-8.png", "screen-9.png"]


async def test_capture_survives_a_frame_that_was_already_removed(tmp_path, monkeypatch):
    provider = LocalScreenshotProvider(tmp_path, retention=1)

    async def fake_to_thread(fn):
        p = tmp_path / f"screen-{time.time_ns()}.png"
        p.write_bytes(b"png")
        return str(p), False

    monkeypatch.setattr("bossman.computer_operator.adapters.screenshot.asyncio.to_thread",
                        lambda fn: fake_to_thread(fn))
    ref, _ = await provider.capture()
    from pathlib import Path
    Path(ref).unlink()                                  # something else cleaned temp
    ref2, _ = await provider.capture()                  # must not raise
    assert Path(ref2).exists()
