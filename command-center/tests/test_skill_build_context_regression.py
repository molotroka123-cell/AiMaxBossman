"""Exercise the real pristine-copy function and skill asset hooks offline.

This uses a small build-input fixture, not a full application wheel build.
"""
from __future__ import annotations

import ast
import importlib.util
from pathlib import Path
import shutil
import tomllib

import pytest

ROOT = Path(__file__).resolve().parents[2]


def _copy_function(repository: Path, siblings=None):
    source = ROOT / 'command-center/tests/test_installed_product_paths.py'
    tree = ast.parse(source.read_text(encoding='utf-8'))
    assignment = next(n for n in tree.body if isinstance(n, ast.Assign)
                      and any(isinstance(t, ast.Name) and t.id == 'SIBLING_BUILD_INPUTS' for t in n.targets))
    function = next(n for n in tree.body if isinstance(n, ast.FunctionDef) and n.name == '_pristine_component')
    namespace = {'Path': Path, 'shutil': shutil, 'REPOSITORY': repository,
                 'COMPONENT': repository / 'command-center',
                 '_BUILD_JUNK': shutil.ignore_patterns('build', 'dist', '__pycache__')}
    exec(compile(ast.Module(body=[assignment, function], type_ignores=[]), str(source), 'exec'), namespace)
    if siblings is not None:
        namespace['SIBLING_BUILD_INPUTS'] = siblings
    return namespace['_pristine_component']


def _fixture(tmp_path):
    repository = tmp_path / 'input'
    for relative in ('integrations/open-news', 'integrations/mimik', '.agents/skills/open-news',
                     '.agents/skills/mimik', 'command-center/bcc/open_news_skill/_vendor'):
        shutil.copytree(ROOT / relative, repository / relative)
    for relative in ('apps', 'tools'):
        (repository / relative).mkdir()
    return repository


def _builder(monkeypatch):
    import setuptools
    monkeypatch.setattr(setuptools, 'setup', lambda **kwargs: None)
    spec = importlib.util.spec_from_file_location('skill_copy_regression', ROOT / 'command-center/setup.py')
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def test_actual_pristine_context_keeps_both_skill_build_inputs(monkeypatch, tmp_path):
    source = _copy_function(_fixture(tmp_path))(tmp_path / 'clean').parent
    builder = _builder(monkeypatch)
    out = tmp_path / 'assets'
    builder.copy_open_news_assets(source, out)
    builder.copy_mimik_assets(source, out)
    for name in ('open-news', 'mimik'):
        assert (out / 'bcc/_skills' / name / 'SKILL.md').read_bytes() == (
            ROOT / '.agents/skills' / name / 'SKILL.md').read_bytes()


def test_previous_context_without_agents_reproduces_missing_asset(monkeypatch, tmp_path):
    source = _copy_function(_fixture(tmp_path), ('apps', 'integrations', 'tools'))(tmp_path / 'clean').parent
    assert not (source / '.agents').exists()
    with pytest.raises(RuntimeError, match='required package asset missing'):
        _builder(monkeypatch).copy_open_news_assets(source, tmp_path / 'assets')


def test_setuptools_is_explicit_dev_dependency_not_new_runtime_dependency():
    project = tomllib.loads((ROOT / 'command-center/pyproject.toml').read_text(encoding='utf-8'))['project']
    assert 'setuptools>=68' in project['optional-dependencies']['dev']
    assert not any(item.startswith('setuptools') for item in project['dependencies'])
