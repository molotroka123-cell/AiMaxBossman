from pathlib import Path
from bcc.v2.code_index import CodeIndex


def test_code_index_symbol_and_honest_lines(tmp_path: Path):
    repo = tmp_path / "repo"; repo.mkdir()
    src = repo / "sample.py"
    src.write_text("VALUE=1\n\ndef decide_effect(spec,args):\n    reason='policy decision'\n    return reason\n", encoding="utf-8")
    index = CodeIndex(roots=[repo], index_path=tmp_path / "idx.json")
    index.index_sync()
    hits = index.search_sync("decide effect policy", top_k=5)
    assert hits
    top = hits[0]
    lines = src.read_text(encoding="utf-8").splitlines()
    excerpt = "\n".join(lines[top["start_line"]-1:top["end_line"]])
    assert "def decide_effect" in excerpt


def test_code_index_respects_gitignore(tmp_path: Path):
    repo = tmp_path / "repo"; repo.mkdir()
    (repo / ".gitignore").write_text("ignored/\n", encoding="utf-8")
    (repo / "ok.py").write_text("def visible_symbol(): pass\n", encoding="utf-8")
    ignored = repo / "ignored"; ignored.mkdir()
    (ignored / "hidden.py").write_text("def secret_symbol(): pass\n", encoding="utf-8")
    index = CodeIndex(roots=[repo], index_path=tmp_path / "idx.json")
    index.index_sync()
    assert index.search_sync("visible symbol")
    # Проверяем ИСТОЧНИКИ, а не факт выдачи: запрос «secret symbol» всё равно
    # находит visible_symbol по общему токену «symbol». Игнор означает, что
    # файла нет в индексе, а не что поиск ничего не вернёт.
    sources = {hit["source"] for hit in index.search_sync("secret symbol")}
    assert "ignored/hidden.py" not in sources
    assert not any(s.startswith("ignored/") for s in sources)
