"""Audit 2026-09-28: LearningStore() must never default into an installed package.

Installed from the bossman-shared wheel, ROOT is site-packages, so the checkout
default ``ROOT/data/learning`` wrote learning data into the installed app."""
from __future__ import annotations

import sys
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
sys.path.insert(0, str(ROOT / "bossman-core"))

from learning import trace  # noqa: E402


@pytest.fixture
def installed(monkeypatch, tmp_path):
    fake = tmp_path / "venv" / "Lib" / "site-packages"
    monkeypatch.setattr(trace, "ROOT", fake)
    monkeypatch.setattr(trace, "DATA_DIR", fake / "data" / "learning")
    monkeypatch.setattr(trace, "DOCS_DIR", fake / "docs" / "learning" / "fix_logs")
    monkeypatch.delenv(trace.ENV_LEARNING_DIR, raising=False)
    return fake


def test_installed_package_refuses_the_implicit_default(installed):
    with pytest.raises(trace.LearningStoreLocationError, match=trace.ENV_LEARNING_DIR):
        trace.LearningStore()
    assert not (installed / "data").exists()


def test_installed_package_uses_the_explicit_env_dir(installed, tmp_path, monkeypatch):
    monkeypatch.setenv(trace.ENV_LEARNING_DIR, str(tmp_path / "owner-learning"))
    store = trace.LearningStore()
    assert store.data_dir == tmp_path / "owner-learning"
    assert installed not in store.data_dir.parents and installed not in store.docs_dir.parents


def test_installed_package_with_explicit_data_dir_keeps_docs_beside_it(installed, tmp_path):
    store = trace.LearningStore(tmp_path / "corpus")
    assert store.docs_dir == tmp_path / "corpus" / "docs"


def test_runtime_bridge_does_not_resolve_episodes_into_the_install(installed):
    from bossman.learning_guard import runtime_bridge
    assert runtime_bridge.default_episodes_path() is None


def test_checkout_default_is_unchanged(monkeypatch):
    monkeypatch.delenv(trace.ENV_LEARNING_DIR, raising=False)
    if trace._installed_package():
        pytest.skip("running from an installed package")
    assert trace.default_dirs() == (trace.DATA_DIR, trace.DOCS_DIR)
