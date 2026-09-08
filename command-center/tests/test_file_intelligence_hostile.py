"""§23/§29 — враждебные контроли области действия File Intelligence.

Почему это отдельный набор. Файловый ИИ, наведённый на репозиторий разработки,
опасен именно тогда, когда он умный: он «наведёт порядок» в дереве исходников, и
каждое движение будет выглядеть осмысленным. Отказ обязан случиться ДО запуска
процесса — в момент, когда двигать ещё нечего.

Каждый тест ниже проверяет ДВЕ вещи: что отказ произошёл с правильным именем и
что число эффектов осталось нулевым.
"""
from __future__ import annotations

import os
import sys
from pathlib import Path

import pytest

from bcc.file_intelligence.models import Denied, JobState, Refusal
from bcc.file_intelligence.scope import ScopePolicy, canonical

from . import fileintel_fake as fake


@pytest.fixture
def sandbox(tmp_path):
    root = tmp_path / "Downloads"
    fake.make_corpus(root, 2)
    state = tmp_path / "state"
    state.mkdir(parents=True, exist_ok=True)
    (state / "bcc.db").write_text("pretend database\n", encoding="utf-8")
    repo = tmp_path / "repo"
    (repo / ".git").mkdir(parents=True)
    (repo / "src").mkdir()
    (repo / "src" / "main.py").write_text("print('hi')\n", encoding="utf-8")
    outside = tmp_path / "elsewhere"
    outside.mkdir()
    (outside / "secret.txt").write_text("not yours\n", encoding="utf-8")
    policy = ScopePolicy(authorized_roots=[root], protected_paths=[state],
                         repository_root=repo)
    return type("SB", (), {"root": root, "state": state, "repo": repo,
                           "outside": outside, "policy": policy, "tmp": tmp_path})


# ------------------------------------------------------------------ §23 обход

def test_dotdot_traversal_is_refused_even_when_it_lands_inside(sandbox):
    """`..` отклоняется как ПОПЫТКА, а не по тому, куда она привела.

    Путь `root/sub/../file` разрешается внутрь корня, и пропустить его значило
    бы принимать «обход, который на этот раз не сработал».
    """
    sb = sandbox
    (sb.root / "sub").mkdir()
    with pytest.raises(Denied) as denied:
        sb.policy.check(str(sb.root / "sub" / ".." / "file0.pdf"), mutating=False)
    assert denied.value.refusal is Refusal.PATH_TRAVERSAL


def test_absolute_path_outside_every_root_is_refused(sandbox):
    with pytest.raises(Denied) as denied:
        sandbox.policy.check(str(sandbox.outside), mutating=False)
    assert denied.value.refusal is Refusal.PATH_OUTSIDE_AUTHORIZED_ROOTS


def test_a_sibling_with_a_shared_prefix_is_not_inside(tmp_path):
    """`/x/downloads-old` начинается с `/x/downloads` и лежит НЕ в нём.

    Сравнение строк с префиксом дало бы здесь доступ; сравнение по компонентам
    пути — нет.
    """
    inside = tmp_path / "downloads"
    sibling = tmp_path / "downloads-old"
    inside.mkdir()
    sibling.mkdir()
    policy = ScopePolicy(authorized_roots=[inside])
    policy.check(str(inside), mutating=False)               # законный случай
    with pytest.raises(Denied) as denied:
        policy.check(str(sibling), mutating=False)
    assert denied.value.refusal is Refusal.PATH_OUTSIDE_AUTHORIZED_ROOTS


@pytest.mark.skipif(os.name == "nt", reason="POSIX symlink semantics")
def test_symlink_escape_is_resolved_before_authorization(sandbox):
    """Текст пути внутри корня, цель — снаружи. Проверять права по тексту и
    есть дыра."""
    sb = sandbox
    link = sb.root / "escape"
    link.symlink_to(sb.outside, target_is_directory=True)
    with pytest.raises(Denied) as denied:
        sb.policy.check(str(link), mutating=False)
    assert denied.value.refusal is Refusal.SYMLINK_ESCAPE
    assert (sb.outside / "secret.txt").read_text() == "not yours\n"


@pytest.mark.skipif(os.name == "nt", reason="POSIX symlink semantics")
def test_symlinked_file_inside_a_root_escaping_outward_is_refused(sandbox):
    sb = sandbox
    link = sb.root / "note.txt"
    link.symlink_to(sb.outside / "secret.txt")
    with pytest.raises(Denied) as denied:
        sb.policy.check(str(link), mutating=True)
    assert denied.value.refusal is Refusal.SYMLINK_ESCAPE


@pytest.mark.skipif(
    sys.platform != "win32",
    reason="NTFS junction/reparse semantics need Windows; owner-machine level")
def test_junction_escape_is_refused_on_windows(sandbox):     # pragma: no cover
    """§23 — junction/reparse на Windows.

    Помечен пропуском ЧЕСТНО: на Linux-раннере эта граница не проверяется, и
    выдавать её за пройденную нельзя. Проверяется на машине владельца (§24
    уровень C). Разрешение пути идёт через os.path.realpath, который на Windows
    раскрывает junction'ы так же, как symlink'и, — поэтому ожидание то же.
    """
    sb = sandbox
    link = sb.root / "junction"
    os.system(f'mklink /J "{link}" "{sb.outside}"')
    with pytest.raises(Denied) as denied:
        sb.policy.check(str(link), mutating=False)
    assert denied.value.refusal in (Refusal.SYMLINK_ESCAPE,
                                    Refusal.PATH_OUTSIDE_AUTHORIZED_ROOTS)


# ------------------------------------------------------------- §29 репозиторий

def test_a_git_repository_cannot_be_mutated_even_if_the_owner_authorized_it(
        sandbox, tmp_path):
    """Мутация исходников запрещена БЕЗ опции включить.

    Владелец может явно разрешить немутирующий анализ; кнопки «и всё-таки
    переложи мой репозиторий» не существует, потому что этим управляет отдельная
    будущая способность.
    """
    sb = sandbox
    permissive = ScopePolicy(authorized_roots=[sb.repo], repository_root=sb.repo,
                             allow_analysis_in_repositories=True)
    permissive.check(str(sb.repo / "src"), mutating=False)     # анализ разрешён
    with pytest.raises(Denied) as denied:
        permissive.check(str(sb.repo / "src"), mutating=True)
    assert denied.value.refusal is Refusal.PROTECTED_REPOSITORY


def test_repository_analysis_requires_an_explicit_opt_in(sandbox):
    strict = ScopePolicy(authorized_roots=[sandbox.repo],
                         repository_root=sandbox.repo)
    with pytest.raises(Denied) as denied:
        strict.check(str(sandbox.repo / "src"), mutating=False)
    assert denied.value.refusal is Refusal.PROTECTED_REPOSITORY


def test_git_internals_are_refused(tmp_path):
    repo = tmp_path / "project"
    (repo / ".git" / "objects").mkdir(parents=True)
    policy = ScopePolicy(authorized_roots=[repo])
    for target in (repo / ".git", repo / ".git" / "objects"):
        with pytest.raises(Denied) as denied:
            policy.check(str(target), mutating=True)
        assert denied.value.refusal is Refusal.PROTECTED_REPOSITORY


def test_any_nested_repository_is_found_not_only_the_configured_one(tmp_path):
    """Владелец разрешил Downloads; внутри оказался клон.

    Защита ищет маркер репозитория вверх по дереву, а не сверяется со списком
    известных путей: неизвестный клон опаснее известного.
    """
    downloads = tmp_path / "Downloads"
    clone = downloads / "someones-project"
    (clone / ".git").mkdir(parents=True)
    (clone / "README.md").write_text("hi\n", encoding="utf-8")
    policy = ScopePolicy(authorized_roots=[downloads])
    policy.check(str(downloads), mutating=True)              # сам корень законен
    with pytest.raises(Denied) as denied:
        policy.check(str(clone / "README.md"), mutating=True)
    assert denied.value.refusal is Refusal.PROTECTED_REPOSITORY


def test_bossman_state_directory_is_refused(sandbox):
    for target in (sandbox.state, sandbox.state / "bcc.db"):
        with pytest.raises(Denied) as denied:
            sandbox.policy.check(str(target), mutating=True)
        assert denied.value.refusal is Refusal.PROTECTED_BOSSMAN_STATE


@pytest.mark.skipif(os.name == "nt", reason="POSIX system layout")
@pytest.mark.parametrize("target", ["/etc", "/usr/bin", "/etc/passwd", "/boot"])
def test_system_directories_are_refused(target):
    policy = ScopePolicy(authorized_roots=[Path("/")])
    with pytest.raises(Denied) as denied:
        policy.check(target, mutating=True)
    assert denied.value.refusal is Refusal.PROTECTED_SYSTEM_PATH


@pytest.mark.parametrize("name", [".ssh", ".aws", ".gnupg", "credentials", ".secrets"])
def test_credential_directories_are_refused_even_for_analysis(tmp_path, name):
    """Секреты не читаются даже «просто чтобы посмотреть»: имена файлов в
    каталоге ключей — это уже сведения о ключах."""
    home = tmp_path / "home"
    secret = home / name
    secret.mkdir(parents=True)
    policy = ScopePolicy(authorized_roots=[home])
    with pytest.raises(Denied) as denied:
        policy.check(str(secret), mutating=False)
    assert denied.value.refusal is Refusal.PROTECTED_SECRETS_PATH


def test_an_active_workspace_cannot_be_mutated(tmp_path):
    home = tmp_path / "home"
    sandbox_dir = home / "openhands" / "project"
    sandbox_dir.mkdir(parents=True)
    policy = ScopePolicy(authorized_roots=[home])
    with pytest.raises(Denied) as denied:
        policy.check(str(sandbox_dir), mutating=True)
    assert denied.value.refusal is Refusal.PROTECTED_REPOSITORY


def test_no_authorized_roots_means_no_access_at_all(tmp_path):
    """Умолчание для способности, которая двигает файлы, — «никуда»."""
    empty = ScopePolicy(authorized_roots=[])
    with pytest.raises(Denied) as denied:
        empty.check(str(tmp_path), mutating=False)
    assert denied.value.refusal is Refusal.PATH_OUTSIDE_AUTHORIZED_ROOTS


# --------------------------------------------------- законные случаи (контроль)

def test_ordinary_owner_folders_are_allowed(tmp_path):
    """Негативный контроль ко всему набору выше.

    Политика, отказывающая всему, «проходит» каждый враждебный тест и при этом
    бесполезна. Обычные папки владельца обязаны работать.
    """
    home = tmp_path / "home"
    for name in ("Downloads", "Documents", "Pictures", "Videos",
                 "Downloads/Счета 2026", "Documents/my project notes"):
        (home / name).mkdir(parents=True, exist_ok=True)
    policy = ScopePolicy(authorized_roots=[home])
    for name in ("Downloads", "Documents", "Pictures", "Videos",
                 "Downloads/Счета 2026", "Documents/my project notes"):
        resolved = policy.check(str(home / name), mutating=True)
        assert resolved == canonical(home / name)


def test_unicode_spaces_and_long_names_are_ordinary_files(tmp_path):
    """§23 — имена с пробелами, юникодом и большой длиной не являются атакой."""
    root = tmp_path / "Downloads"
    root.mkdir()
    for name in ("отчёт за сентябрь.pdf", "vacation photo 2026.jpg",
                 "a" * 120 + ".txt", "файл с пробелами и — тире.docx"):
        target = root / name
        target.write_text("x\n", encoding="utf-8")
    policy = ScopePolicy(authorized_roots=[root])
    for child in root.iterdir():
        policy.check(str(child), mutating=True)


def test_shell_metacharacters_in_a_filename_are_just_characters(tmp_path):
    """Имя — это данные. Ни одна из этих строк не должна ничего исполнить и ни
    одна не должна быть отвергнута как «подозрительная»."""
    root = tmp_path / "Downloads"
    root.mkdir()
    policy = ScopePolicy(authorized_roots=[root])
    for name in ("; rm -rf ~", "$(whoami).txt", "`id`.pdf", "a|b.txt",
                 "file&name.doc", "--not-a-flag.txt"):
        target = root / name
        target.write_text("harmless\n", encoding="utf-8")
        resolved = policy.check(str(target), mutating=True)
        assert resolved.name == name
        assert resolved.read_text() == "harmless\n"


def test_read_only_source_is_not_refused_by_policy(tmp_path):
    """Права доступа — вопрос файловой системы, а не области действия.

    Отказать здесь значило бы прятать настоящую причину (файл только для
    чтения) за чужой (путь вне области).
    """
    root = tmp_path / "Downloads"
    root.mkdir()
    target = root / "readonly.pdf"
    target.write_text("locked\n", encoding="utf-8")
    target.chmod(0o444)
    policy = ScopePolicy(authorized_roots=[root])
    assert policy.check(str(target), mutating=True) == canonical(target)


def test_empty_folder_is_a_valid_target(tmp_path):
    root = tmp_path / "Downloads"
    (root / "empty").mkdir(parents=True)
    policy = ScopePolicy(authorized_roots=[root])
    folder, resolved = policy.single_parent([str(root / "empty")])
    assert folder == canonical(root / "empty") and len(resolved) == 1


# ------------------------------------------------- отказ ДО запуска процесса

async def test_every_denial_happens_before_the_sidecar_is_launched(sandbox, tmp_path):
    """Ни один отказ области не должен стоить запущенного процесса."""
    from bcc.file_intelligence.service import FileIntelligenceService
    sb = sandbox
    sidecar = fake.FakeSidecar(plan=fake.review_plan([]))
    service = FileIntelligenceService(
        state_dir=sb.tmp / "svc", policy=sb.policy,
        discovery=fake.fake_discovery(fake.make_executable(tmp_path)),
        config_path=fake.local_config(tmp_path), runner=sidecar)

    for target in (str(sb.outside), str(sb.repo), str(sb.state),
                   str(sb.root / ".." / "elsewhere")):
        job = await service.analyze([target])
        assert job.state == JobState.DENIED.value, target
        assert job.refusal is not None
    assert sidecar.calls == [], "процесс запускался при отказе области"
