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


async def test_code_root_outside_allowed_is_denied(env, tmp_path: Path):
    """Гейт «Code root safety»: корень вне разрешённых — отказ, а не тихий поиск.

    Проверяется через ту же функцию, которой пользуются инструменты `code.*`,
    а не через отдельную копию логики.
    """
    import pytest
    from bcc.features import tools_code

    outside = tmp_path / "чужой-проект"
    outside.mkdir()
    (outside / "secret.py").write_text("API_TOKEN = 'нельзя-читать'\n", encoding="utf-8")

    with pytest.raises(PermissionError, match="вне разрешённых корней"):
        await tools_code.resolve_root(env.svc, str(outside))

    # корень по умолчанию при этом рабочий — правило не сузило обычную работу
    default_root = await tools_code.resolve_root(env.svc, None)
    assert default_root.is_dir()


async def test_code_root_symlink_does_not_escape(env, tmp_path: Path):
    """Ссылка внутри разрешённого корня не открывает то, что снаружи."""
    import pytest
    from bcc.features import tools_code

    allowed = tmp_path / "проект"; allowed.mkdir()
    outside = tmp_path / "снаружи"; outside.mkdir()
    (outside / "secret.py").write_text("X = 1\n", encoding="utf-8")
    await tools_code._write_setting_json(env.svc, tools_code.CODE_ROOTS_KEY, [str(allowed)])

    link = allowed / "мостик"
    try:
        link.symlink_to(outside, target_is_directory=True)
    except (OSError, NotImplementedError) as exc:
        # Windows без Developer Mode: создание симлинка требует привилегии
        # SeCreateSymbolicLink и падает с WinError 1314. Само правило при этом
        # никуда не девается — проверить его этим способом нельзя, и честный
        # ответ здесь «пропущено», а не «сломано».
        pytest.skip(f"ФС не даёт создать символическую ссылку: {exc}")
    with pytest.raises(PermissionError, match="вне разрешённых корней"):
        await tools_code.resolve_root(env.svc, str(link))
