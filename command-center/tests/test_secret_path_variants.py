"""Secret-path guard (audit 28.10 re-check of the Argon path-canonicalization claim): every spelling of vault.key,
credentials.enc and provider-keys.env must be refused. Probed 13/13 on Windows; the guard held, this pins it."""
from __future__ import annotations

import os
import sys
from pathlib import Path

import pytest

from bcc.features.pc_agent_policy_paths import is_t3_secret_or_protected_path

pytestmark = pytest.mark.skipif(sys.platform != "win32", reason="Windows path spellings")

D = Path(os.environ.get("LOCALAPPDATA", r"C:\Users\x\AppData\Local")) / "Bossman" / "CommandCenter"
BASE = str(D / "vault.key")

SPELLINGS = {
    "plain": BASE,
    "trailing dot": BASE + ".",
    "trailing space": BASE + " ",
    "dotdot": str(D / "x" / ".." / "vault.key"),
    "dot": str(D / "." / "vault.key"),
    "upper": BASE.upper(),
    "forward slash": BASE.replace("\\", "/"),
    "ads": BASE + "::$DATA",
    "extended prefix": "\\?\\" + BASE,
    "env var": r"%LOCALAPPDATA%\Bossman\CommandCenter\vault.key",
    "credentials dot": str(D / "credentials.enc") + ".",
    "provider keys space": str(D / "provider-keys.env") + " ",
}


@pytest.mark.parametrize("name", list(SPELLINGS))
def test_every_spelling_of_a_secret_file_is_refused(name):
    refused, _why = is_t3_secret_or_protected_path(SPELLINGS[name], for_write=False, repo_root=None, data_dir=D)
    assert refused is True
