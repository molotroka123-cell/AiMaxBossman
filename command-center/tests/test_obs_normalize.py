"""Tests: observation normalizer (spec Part G, §38)."""
from __future__ import annotations

from bcc.v2.obs_normalize import (
    MAX_OBSERVATION_CHARS,
    bound_text,
    normalize_git_status,
    normalize_process,
    normalize_pytest_output,
    normalize_stage13,
)

PYTEST_SAMPLE = """\
============================= test session starts =============================
collected 15 items

test_calc.py ..F..
test_math.py ..s..

================================== FAILURES ===================================
_________________________________ test_divide _________________________________
    def test_divide():
>       assert divide(1, 0) == 0
E       ZeroDivisionError: division by zero

test_calc.py:12: ZeroDivisionError
=========================== short summary info ============================
FAILED test_calc.py::test_divide - ZeroDivisionError: division by zero
FAILED test_math.py::test_mul - assert 3 == 4
= 2 failed, 12 passed, 1 skipped in 0.42s =
"""

GIT_SAMPLE = """ M src/app.py
?? new_file.txt
12\t3\tsrc/app.py
1\t0\tnew_file.txt
"""


def test_pytest_counts_and_failure_names():
    obs = normalize_pytest_output(PYTEST_SAMPLE)
    assert obs.kind == "pytest"
    assert obs.ok is False
    assert obs.fields["passed"] == 12
    assert obs.fields["failed"] == 2
    assert obs.fields["skipped"] == 1
    assert obs.failure_names == ["test_calc.py::test_divide", "test_math.py::test_mul"]
    assert "division by zero" in obs.fields["error_block"]


def test_pytest_all_pass_no_failures():
    obs = normalize_pytest_output("= 3 passed in 0.01s =")
    assert obs.ok is True
    assert obs.fields["failed"] == 0
    assert obs.failure_names == []


def test_pytest_unparsable_is_tolerant():
    obs = normalize_pytest_output("some random noise without summary")
    assert obs.ok is False
    assert "unparsed" in obs.summary
    assert obs.raw_artifact == "some random noise without summary"


def test_git_porcelain_and_numstat():
    obs = normalize_git_status(GIT_SAMPLE)
    assert obs.kind == "git"
    assert obs.fields["dirty"] is True
    assert obs.fields["changed_count"] == 2
    assert obs.fields["insertions"] == 13
    assert obs.fields["deletions"] == 3
    assert "src/app.py" in obs.fields["changed_files"]
    empty = normalize_git_status("")
    assert empty.fields["dirty"] is False


def test_process_exit_code_and_stderr_bounding():
    obs = normalize_process("done", exit_code=0)
    assert obs.ok is True
    assert obs.fields["exit_code"] == 0
    big_err = "e" * 10_000
    obs2 = normalize_process("out", exit_code=1, stderr=big_err)
    assert obs2.ok is False
    assert len(obs2.fields["stderr_tail"]) <= MAX_OBSERVATION_CHARS  # bounded
    assert obs2.fields["stderr_tail"].endswith("eee")


def test_stage13_payload_keys():
    obs = normalize_stage13({
        "action": "edit", "target": "src/app.py", "effect": "patched",
        "fresh_observation": {"diff_lines": 5},
        "verification": {"pytest": "12 passed"},
    })
    assert obs.kind == "stage13"
    assert obs.fields["action"] == "edit"
    assert obs.fields["target"] == "src/app.py"
    assert obs.fields["effect"] == "patched"
    assert obs.fields["verification_present"] is True
    assert obs.ok is True
    # фолбэки на отсутствующих ключах — без исключений
    empty = normalize_stage13({})
    assert empty.fields["action"] == ""
    assert empty.ok is False
    weird = normalize_stage13("not a dict")
    assert weird.ok is False


def test_huge_input_truncated_but_raw_preserved():
    huge = ("= 1 failed, 0 passed in 1s =\n" + "x" * 50_000)
    obs = normalize_pytest_output(huge)
    assert obs.truncated is True
    assert obs.raw_artifact == huge                    # raw сохранён ПОЛНОСТЬЮ
    assert len(obs.raw_artifact) == len(huge)
    view = obs.summary + str(obs.fields) + obs.failure_names.__repr__()
    assert len(view) < MAX_OBSERVATION_CHARS + 2000    # компактный вид ограничен
    assert len(obs.fields.get("error_block", "")) <= MAX_OBSERVATION_CHARS


def test_bound_text_deterministic():
    head, cut = bound_text("short", 100)
    assert (head, cut) == ("short", False)
    long_text = "a" * 500
    out, cut = bound_text(long_text, 100)
    assert cut is True
    assert len(out) <= 100
    assert "truncated" in out
    assert out == bound_text(long_text, 100)[0]  # детерминированность
    out2, cut2 = bound_text(long_text, 500)
    assert cut2 is False and out2 == long_text


def test_malformed_input_never_raises():
    for bad in (None, "", "\x00\x01binary", "перечёркнутый \ufffd мусор", 12345):
        for fn in (normalize_pytest_output, normalize_git_status):
            obs = fn(bad)  # не должно бросить
            assert obs.kind in ("pytest", "git")
        obs = normalize_process(None, exit_code=-1)
        assert obs.fields["exit_code"] == -1
