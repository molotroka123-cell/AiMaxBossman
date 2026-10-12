import sys
import threading
from types import SimpleNamespace

import numpy as np

from pokervision.sources import ScreenWindowSource


def test_capture_is_created_read_and_closed_on_worker(monkeypatch):
    calls = []

    class Capture:
        def __init__(self):
            calls.append(("create", threading.get_ident()))

        def grab(self, rect):
            calls.append(("read", threading.get_ident()))
            return np.zeros((rect["height"], rect["width"], 4), dtype=np.uint8)

        def close(self):
            calls.append(("close", threading.get_ident()))

    monkeypatch.setitem(sys.modules, "mss", SimpleNamespace(mss=Capture))
    source = ScreenWindowSource([0, 0, 5, 6])
    assert calls == []
    errors = []

    def worker():
        try:
            assert source.read().bgr.shape == (6, 5, 3)
        except Exception as exc:
            errors.append(exc)
        finally:
            source.close()

    thread = threading.Thread(target=worker)
    thread.start()
    thread.join()
    assert not errors
    assert [name for name, _ in calls] == ["create", "read", "close"]
    assert len({ident for _, ident in calls}) == 1
    assert calls[0][1] != threading.get_ident()
