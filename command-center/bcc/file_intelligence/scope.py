"""§8/§29 — граница действия File Intelligence. Решается ДО запуска сайдкара.

Почему граница своя, а не upstream'овская. Upstream умеет узнавать структурные
папки проектов и не трогать их — это его политика СОРТИРОВКИ. Здесь живёт
политика ПОЛНОМОЧИЙ Bossman'а, и она стоит выше: сайдкар предлагает, Bossman
разрешает. Две независимые защиты в одном месте — это не дублирование, потому
что отвечают они на разные вопросы и отказ любой из них означает «нет».

Главное правило — §29. Файловый ИИ, наведённый на репозиторий разработки,
опасен именно тогда, когда он умный: он «наведёт порядок» в дереве исходников,
и каждое такое движение будет выглядеть осмысленным. Поэтому репозитории,
состояние Bossman'а, песочницы и каталоги секретов запрещены ПО УМОЛЧАНИЮ, а
мутация исходников — запрещена вообще, без опции включить.
"""
from __future__ import annotations

import os
import sys
from pathlib import Path
from typing import Iterable, Sequence

from .models import Denied, Refusal

#: Каталоги, мутация внутри которых небезопасна независимо от того, что о них
#: думает владелец: системные корни и места установки.
_SYSTEM_PREFIXES_POSIX = (
    "/bin", "/boot", "/dev", "/etc", "/lib", "/lib32", "/lib64", "/proc",
    "/run", "/sbin", "/sys", "/usr", "/var",
)
_SYSTEM_PREFIXES_WINDOWS = (
    "c:\\windows", "c:\\program files", "c:\\program files (x86)",
    "c:\\programdata", "c:\\$recycle.bin",
)

#: Имена, встреча с которыми означает «это не обычная папка владельца».
_REPOSITORY_MARKERS = (".git", ".hg", ".svn")
_SECRET_DIR_NAMES = frozenset({
    ".ssh", ".gnupg", ".gpg", ".aws", ".azure", ".config/gcloud", ".kube",
    ".docker", ".netrc", "keyrings", "credentials", "secrets", ".secrets",
    ".password-store",
})
#: Каталоги проектов/песочниц: анализ по явному выбору владельца, мутация — нет.
_WORKSPACE_MARKERS = ("openhands", "workspace", "workspaces", "sandbox")


def _norm(path: Path) -> str:
    text = str(path)
    return text.lower() if os.name == "nt" or sys.platform == "darwin" else text


def canonical(raw: str | os.PathLike[str]) -> Path:
    """Каноническая форма пути: symlink/junction разрешены, `..` свёрнуты.

    Разрешение делается ДО любой проверки прав. Проверять права по тексту
    пути — это и есть дыра: `root/link` лежит внутри корня ровно до тех пор,
    пока не посмотришь, куда указывает `link`.

    `strict=False`, потому что назначение может ещё не существовать; для него
    разрешается существующая часть пути, а несуществующий хвост добавляется
    как есть.
    """
    return Path(os.path.abspath(os.path.realpath(str(raw))))


def _is_within(child: Path, parent: Path) -> bool:
    """Строгое вложение по компонентам пути.

    Сравнение строк с префиксом здесь неверно: `/home/user/downloads-old`
    начинается с `/home/user/downloads`, но лежит не в нём.
    """
    try:
        child_parts = _norm(child).split(os.sep)
        parent_parts = _norm(parent).split(os.sep)
    except Exception:
        return False
    if len(child_parts) < len(parent_parts):
        return False
    return child_parts[:len(parent_parts)] == parent_parts


def _has_repository_marker(path: Path) -> Path | None:
    """Вернуть корень репозитория, если путь внутри него (или сам им является)."""
    for candidate in (path, *path.parents):
        for marker in _REPOSITORY_MARKERS:
            if (candidate / marker).exists():
                return candidate
        # сам путь ЯВЛЯЕТСЯ внутренностями репозитория
        if candidate.name in _REPOSITORY_MARKERS:
            return candidate.parent
    return None


def _is_system_path(path: Path) -> bool:
    text = _norm(path)
    prefixes = _SYSTEM_PREFIXES_WINDOWS if os.name == "nt" else _SYSTEM_PREFIXES_POSIX
    for prefix in prefixes:
        prefix_norm = prefix.lower() if os.name == "nt" else prefix
        if text == prefix_norm or text.startswith(prefix_norm + os.sep):
            # /var/tmp и /run/user — законные рабочие места; корень — нет.
            if text.startswith(("/var/tmp", "/run/user")):
                return False
            return True
    return False


def _touches_secrets(path: Path) -> bool:
    parts = [p.lower() for p in path.parts]
    for name in _SECRET_DIR_NAMES:
        needle = name.split("/")[-1].lower()
        if needle in parts:
            return True
    return False


def _touches_workspace(path: Path) -> bool:
    parts = [p.lower() for p in path.parts]
    return any(marker in parts for marker in _WORKSPACE_MARKERS)


class ScopePolicy:
    """Политика области действия для одной установки Bossman.

    `authorized_roots` — то, что владелец РАЗРЕШИЛ. Пустой список означает, что
    не разрешено ничего: по умолчанию у File Intelligence нет доступа никуда, и
    это правильное «по умолчанию» для инструмента, который двигает файлы.
    """

    def __init__(self, authorized_roots: Sequence[str | os.PathLike[str]] = (),
                 *, protected_paths: Sequence[str | os.PathLike[str]] = (),
                 repository_root: str | os.PathLike[str] | None = None,
                 allow_analysis_in_repositories: bool = False):
        self.authorized_roots = [canonical(r) for r in authorized_roots]
        self.protected_paths = [canonical(p) for p in protected_paths]
        self.repository_root = canonical(repository_root) if repository_root else None
        # §29: владелец МОЖЕТ явно включить НЕмутирующий анализ репозитория.
        # Мутация исходников остаётся запрещённой в любом случае — для неё нет
        # флага, потому что её governs отдельная будущая способность.
        self.allow_analysis_in_repositories = bool(allow_analysis_in_repositories)

    # ------------------------------------------------------------------ проверки

    def check(self, raw: str | os.PathLike[str], *, mutating: bool) -> Path:
        """Разрешить путь и доказать, что он в области действия.

        `mutating=False` — подготовка к анализу (сайдкар только читает).
        `mutating=True` — подготовка к применению плана.

        Возвращает канонический путь либо поднимает Denied с названной причиной.
        """
        raw_text = str(raw)
        resolved = canonical(raw_text)

        # 1. Обход вверх. Проверяется по ИСХОДНОМУ тексту: `..` в аргументе —
        #    это попытка, даже если она никуда не привела.
        if ".." in Path(raw_text).parts:
            raise Denied(Refusal.PATH_TRAVERSAL,
                         "path contains an upward traversal component",
                         path=raw_text)

        # 2. Побег по symlink/junction: текст внутри корня, цель — снаружи.
        #    Проверяется до всего остального, чтобы отказ назывался своим именем.
        literal = Path(os.path.abspath(raw_text))
        if _norm(literal) != _norm(resolved) and self.authorized_roots:
            literal_inside = any(_is_within(literal, r) for r in self.authorized_roots)
            resolved_inside = any(_is_within(resolved, r) for r in self.authorized_roots)
            if literal_inside and not resolved_inside:
                raise Denied(Refusal.SYMLINK_ESCAPE,
                             "path resolves outside the authorized roots",
                             path=raw_text, resolved=str(resolved))

        # 3. Состояние Bossman и явно защищённое.
        for protected in self.protected_paths:
            if _is_within(resolved, protected) or resolved == protected:
                raise Denied(Refusal.PROTECTED_BOSSMAN_STATE,
                             "path is inside protected Bossman state",
                             path=str(resolved), protected=str(protected))

        # 4. Системные корни и места установки.
        if _is_system_path(resolved):
            raise Denied(Refusal.PROTECTED_SYSTEM_PATH,
                         "path is inside a system or installation directory",
                         path=str(resolved))

        # 5. Секреты и учётные данные — никогда, ни для анализа.
        if _touches_secrets(resolved):
            raise Denied(Refusal.PROTECTED_SECRETS_PATH,
                         "path is inside a credentials or secrets directory",
                         path=str(resolved))

        # 6. §29 — репозитории. Мутация запрещена ВСЕГДА; анализ — только по
        #    явному включению владельцем.
        repo = _has_repository_marker(resolved)
        if self.repository_root is not None and (
                _is_within(resolved, self.repository_root)
                or resolved == self.repository_root):
            repo = repo or self.repository_root
        if repo is not None:
            if mutating:
                raise Denied(Refusal.PROTECTED_REPOSITORY,
                             "mutating a source repository is not governed by this "
                             "capability", path=str(resolved), repository=str(repo))
            if not self.allow_analysis_in_repositories:
                raise Denied(Refusal.PROTECTED_REPOSITORY,
                             "repository analysis requires an explicit owner opt-in",
                             path=str(resolved), repository=str(repo))

        # 7. Активные песочницы/рабочие каталоги проектов — мутация запрещена.
        if mutating and _touches_workspace(resolved):
            raise Denied(Refusal.PROTECTED_REPOSITORY,
                         "path is inside an active workspace or sandbox",
                         path=str(resolved))

        # 8. И, наконец, разрешил ли владелец этот корень вообще.
        if not self.authorized_roots:
            raise Denied(Refusal.PATH_OUTSIDE_AUTHORIZED_ROOTS,
                         "no authorized roots are configured for File Intelligence",
                         path=str(resolved))
        if not any(_is_within(resolved, root) for root in self.authorized_roots):
            raise Denied(Refusal.PATH_OUTSIDE_AUTHORIZED_ROOTS,
                         "path is outside every authorized root",
                         path=str(resolved),
                         roots=[str(r) for r in self.authorized_roots])
        return resolved

    # ------------------------------------------------------------------ §9

    def single_parent(self, paths: Iterable[str | os.PathLike[str]], *,
                      mutating: bool = False) -> tuple[Path, list[Path]]:
        """§9 — headless-контракт берёт ОДНУ папку или файлы одного родителя.

        Разнородный выбор не «нормализуется» в широкий обход диска: это
        превратило бы «разложи вот эти два файла» в «пройди по всему, что
        найдёшь». Такой запрос отклоняется, и вызывающий делит его на
        независимые работы.
        """
        resolved = [self.check(p, mutating=mutating) for p in paths]
        if not resolved:
            raise Denied(Refusal.PATH_DOES_NOT_EXIST, "no target paths given")
        for path in resolved:
            if not path.exists():
                raise Denied(Refusal.PATH_DOES_NOT_EXIST, "target does not exist",
                             path=str(path))
        if len(resolved) == 1 and resolved[0].is_dir():
            return resolved[0], resolved
        parents = {_norm(p.parent) for p in resolved}
        if len(parents) != 1:
            raise Denied(Refusal.MULTIPLE_PARENT_FOLDERS,
                         "headless analysis supports one folder, or files sharing one "
                         "parent folder; split this into separate jobs",
                         parents=sorted(parents))
        if any(p.is_dir() for p in resolved):
            raise Denied(Refusal.MULTIPLE_PARENT_FOLDERS,
                         "mixing a folder with individual files is not a supported "
                         "headless selection")
        return resolved[0].parent, resolved


def default_protected_paths(data_dir: str | os.PathLike[str] | None) -> list[Path]:
    """Состояние Bossman, которое File Intelligence не трогает никогда."""
    if not data_dir:
        return []
    root = canonical(data_dir)
    return [root]
