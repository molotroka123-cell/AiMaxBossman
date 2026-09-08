"""Аудит §7: критерий успеха синтетического прогона обязан быть честным.

Три претензии, каждая — воспроизводима на прежнем коде: подставной терминал
ничего не создавал; PASS принимал `status=failed`; токены-константы читались
как измеренная экономия. Здесь чистые функции — вердикт и терминал — без
всего harness'а, плюс форма отчёта.
"""
from __future__ import annotations

from pathlib import Path

from scripts.convergence_metrics import (NOTES, NOTES_FIXED, doc_edit_verdict, fake_terminal,
                                         result_verified)

WRITE = "python - <<'PY'\nopen('docs/NOTES.md','w').write('fixed\\n')\nPY"


def test_a_failed_run_is_never_a_pass_however_good_its_numbers():
    v = doc_edit_verdict(final_status="failed", approvals_pass=True, tokens_total=920, verified=True)
    assert v["completed"] is False and v["success"] is False and v["pass"] is False


def test_completed_without_a_verified_document_is_not_success():
    v = doc_edit_verdict(final_status="completed", approvals_pass=True, tokens_total=920, verified=False)
    assert v["completed"] is True and v["success"] is False and v["pass"] is False


def test_completed_and_verified_with_good_numbers_passes():
    v = doc_edit_verdict(final_status="completed", approvals_pass=True, tokens_total=920, verified=True)
    assert v == {"completed": True, "result_verified": True, "success": True,
                 "tokens_pass": True, "pass": True}


def test_good_result_still_fails_on_approvals_or_tokens():
    assert not doc_edit_verdict(final_status="completed", approvals_pass=False,
                                tokens_total=920, verified=True)["pass"]
    assert not doc_edit_verdict(final_status="completed", approvals_pass=True,
                                tokens_total=100_000, verified=True)["pass"]


def test_the_fake_terminal_actually_produces_the_document(tmp_path: Path):
    assert not result_verified(tmp_path)
    missing = fake_terminal(tmp_path, "cat docs/NOTES.md")
    assert missing.error and "No such file" in missing.content
    wrote = fake_terminal(tmp_path, WRITE)
    assert not wrote.error and (tmp_path / NOTES).read_text(encoding="utf-8") == NOTES_FIXED
    assert result_verified(tmp_path)
    read = fake_terminal(tmp_path, "cat docs/NOTES.md")
    assert not read.error and read.content.startswith(NOTES_FIXED)


def test_a_wrong_document_is_not_a_verified_result(tmp_path: Path):
    (tmp_path / NOTES).parent.mkdir(parents=True)
    (tmp_path / NOTES).write_text("not what was asked\n", encoding="utf-8")
    assert not result_verified(tmp_path)


def test_an_unsupported_command_is_an_error_not_a_silent_ok(tmp_path: Path):
    r = fake_terminal(tmp_path, "rm -rf /")
    assert r.error and "exit_code=127" in r.content
