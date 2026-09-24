import pytest
from relpath import normalize_rel


@pytest.mark.parametrize("raw,want", [
    ("Мои документы\\отчёт 2026/итог  final.md", ["Мои документы", "отчёт 2026", "итог  final.md"]),
    ("a\\\\b//c", ["a", "b", "c"]),
    (".\\a\\.\\b", ["a", "b"]),
    ("a\\b\\..\\c", ["a", "c"]),
    ("  папка с пробелом\\файл.txt  ", ["папка с пробелом", "файл.txt"]),
])
def test_ok(raw, want):
    assert normalize_rel(raw) == want


@pytest.mark.parametrize("raw", ["..\\x", "a/../../x", "C:\\x", "C:x", "\\\\server\\share\\x", "/etc/x",
                                 "\\x", "", "   ", ".", "a/.."])
def test_refused(raw):
    with pytest.raises(ValueError):
        normalize_rel(raw)
