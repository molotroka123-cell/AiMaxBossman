from relpath import normalize_rel


def test_simple():
    assert normalize_rel("a/b/c.txt") == ["a", "b", "c.txt"]
