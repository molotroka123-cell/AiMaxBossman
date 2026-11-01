"""Owner 01.11: GLM writing a Roblox game was blocked as «confidential» - a code comment said «pass.key».
A .key is a secret file only when its name says so or it sits in a path; .env/.pem/vault/id_rsa stay confidential."""
from __future__ import annotations

import pytest

from bcc.security.ai_gateway import _CONFIDENTIAL_PATH_RE as R


@pytest.mark.parametrize("text", [" pass.key ", " Config.key ", "-- ключи = pass.key из Config", "local t = data.key "])
def test_code_words_ending_in_key_are_not_secret_files(text):
    assert R.search(text) is None


@pytest.mark.parametrize("text", [" private.key ", " server.key ", r"C:\certs\foo.key", "/etc/ssl/site.key", " .env ",
                                  " id_rsa ", " cert.pem ", " vault.json ", " api_token.key ", " wallet.key "])
def test_real_secret_files_stay_confidential(text):
    assert R.search(text) is not None
