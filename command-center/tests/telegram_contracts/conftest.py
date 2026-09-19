"""Expose real core sources only while a companion test runs.

Command Center CI deliberately does not install bossman-core in the parent
pytest process. The final Windows product does ship both wheels. A scoped
source path avoids changing unrelated suites or pretending the core is a stub.
"""
from pathlib import Path
import importlib.util
import sys

import pytest


@pytest.fixture(autouse=True)
def companion_core_source(monkeypatch):
    existing = {name for name in sys.modules if name == "bossman" or name.startswith("bossman.")}
    source = Path(__file__).resolve().parents[3] / "bossman-core"
    if (source / "bossman" / "__init__.py").is_file():
        monkeypatch.syspath_prepend(str(source))
    if importlib.util.find_spec("bossman") is None:
        raise RuntimeError("Telegram companion needs real bossman-core sources or its installed wheel")
    try:
        yield
    finally:
        for name in tuple(sys.modules):
            if (name == "bossman" or name.startswith("bossman.")) and name not in existing:
                sys.modules.pop(name, None)
