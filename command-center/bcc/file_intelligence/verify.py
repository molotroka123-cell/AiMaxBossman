"""§11–§14 — доказательства вокруг эффекта.

Четыре инварианта, каждый из которых закрывает свой способ соврать:

    PROPOSAL != AUTHORIZATION   план сайдкара — предложение, не разрешение
    APPROVAL != POST_STATE      согласие владельца — не доказательство результата
    PROCESS_EXIT_0 != VERIFIED  ноль на выходе процесса — не наблюдение
    MODEL_TEXT != PROOF         объяснение модели — не улика

Практически это значит: до применения — снять личность источников (§11) и
перепроверить её в последний момент (§12); проверить назначения (§13); после
применения — посмотреть на файловую систему самостоятельно (§14).
"""
from __future__ import annotations

import hashlib
import os
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path
from typing import Iterable, Sequence

from .models import (AppliedEffect, Denied, PlanEntry, Refusal, SourceIdentity)
from .scope import ScopePolicy, canonical

#: Хеширование ограничено: по §11 считаем только ВЫБРАННЫЕ файлы и не устраиваем
#: неограниченный веер. Четырёх потоков хватает, чтобы диск был занят, и мало,
#: чтобы работа владельца не встала.
_HASH_WORKERS = 4
_CHUNK = 1024 * 1024

#: Имена, зарезервированные Windows. Файл `CON.txt` создать нельзя, и узнать об
#: этом лучше до того, как половина плана уже применена.
_RESERVED_WINDOWS = frozenset(
    {"con", "prn", "aux", "nul"}
    | {f"com{i}" for i in range(1, 10)}
    | {f"lpt{i}" for i in range(1, 10)}
)
_ILLEGAL_NAME_CHARS = set('<>:"/\\|?*') | {chr(c) for c in range(32)}
_MAX_PATH = 255
_MAX_FULL_PATH = 4096 if os.name != "nt" else 259


def sha256_file(path: Path) -> str:
    digest = hashlib.sha256()
    with open(path, "rb") as handle:
        for chunk in iter(lambda: handle.read(_CHUNK), b""):
            digest.update(chunk)
    return digest.hexdigest()


def _identity(path: Path, *, planned_destination: str = "",
              planned_name: str = "") -> SourceIdentity:
    resolved = canonical(path)
    stat = os.stat(resolved)
    try:
        file_identity = f"{stat.st_dev}:{stat.st_ino}"
    except AttributeError:                              # pragma: no cover
        file_identity = ""
    return SourceIdentity(
        source_path=str(path),
        canonical_source_path=str(resolved),
        size=stat.st_size,
        mtime_ns=stat.st_mtime_ns,
        content_sha256=sha256_file(resolved),
        file_identity=file_identity,
        planned_destination=planned_destination,
        planned_name=planned_name,
    )


def capture_identities(entries: Sequence[PlanEntry], *,
                       selected_only: bool = False) -> list[PlanEntry]:
    """§11 — снять личность файлов ДО эффекта, с ограниченным параллелизмом.

    Снимается по записям ПЛАНА, а не по всему просканированному каталогу: план
    уже и есть отобранное анализом подмножество, и хешировать мимо него было бы
    неограниченным веером ради формы.

    Момент важен. Личность снимается на РЕВЬЮ, потому что §12 требует поймать
    «источник изменился с момента ревью». Снять её только перед применением
    значило бы записать уже подменённое содержимое и радостно подтвердить, что
    оно совпадает с самим собой.

    `selected_only=True` — пересъёмка перед применением, когда выбор владельца
    уже известен.
    """
    targets = [e for e in entries if (e.selected if selected_only else True)]
    targets = [e for e in targets if e.file_path and Path(e.file_path).exists()]
    if not targets:
        return list(entries)
    with ThreadPoolExecutor(max_workers=_HASH_WORKERS) as pool:
        futures = {
            pool.submit(_identity, Path(entry.file_path),
                        planned_destination=entry.destination,
                        planned_name=entry.suggested_name or entry.file_name): entry
            for entry in targets
        }
        for future, entry in futures.items():
            try:
                entry.identity = future.result()
            except OSError:
                # Файл исчез между перечислением и снятием. Это не сбой снятия,
                # а факт о файле; §12 назовёт его STALE, увидев отсутствующую
                # личность.
                entry.identity = None
    return list(entries)


# ------------------------------------------------------------------ §12 stale

def assert_not_stale(entries: Iterable[PlanEntry]) -> None:
    """§12 — перепроверить источники НЕПОСРЕДСТВЕННО перед применением.

    Отказ — на всю атомарную единицу применения, а не на одну запись: план
    утверждался целиком, и «применим ту часть, которая ещё сходится» означало бы
    исполнить решение, которого владелец не принимал.

    Пересчитывается именно содержимое, а не только размер и mtime. Замена файла
    с восстановленным mtime и тем же размером — это ровно тот случай, который
    поверхностная проверка пропускает.
    """
    problems: list[str] = []
    for entry in entries:
        if not entry.selected:
            continue
        identity = entry.identity
        if identity is None:
            problems.append(f"{entry.file_path}: no pre-effect identity was captured")
            continue
        path = Path(identity.canonical_source_path)
        if path.is_symlink():
            problems.append(f"{identity.source_path}: source became a symlink")
            continue
        if not path.exists():
            problems.append(f"{identity.source_path}: source no longer exists")
            continue
        try:
            stat = os.stat(path)
        except OSError as exc:
            problems.append(f"{identity.source_path}: unreadable ({exc.strerror})")
            continue
        if stat.st_size != identity.size:
            problems.append(f"{identity.source_path}: size changed since review")
            continue
        if sha256_file(path) != identity.content_sha256:
            problems.append(f"{identity.source_path}: contents changed since review")
            continue
        if getattr(stat, "st_ino", None) and identity.file_identity:
            now = f"{stat.st_dev}:{stat.st_ino}"
            if now != identity.file_identity:
                problems.append(f"{identity.source_path}: replaced by a different file")
    if problems:
        raise Denied(Refusal.STALE_REVIEW_PLAN,
                     "sources changed after review; re-analysis is required",
                     problems=problems)


# ------------------------------------------------------- §13 destination safety

def _check_name(name: str) -> None:
    if not name or name in (".", ".."):
        raise Denied(Refusal.INVALID_FILENAME, "planned name is empty or a dot entry",
                     name=name)
    if any(ch in _ILLEGAL_NAME_CHARS for ch in name):
        raise Denied(Refusal.INVALID_FILENAME,
                     "planned name contains a path separator or control character",
                     name=name)
    if name != name.strip() or name.endswith("."):
        raise Denied(Refusal.INVALID_FILENAME,
                     "planned name has leading/trailing whitespace or a trailing dot",
                     name=name)
    stem = name.split(".")[0].lower()
    if stem in _RESERVED_WINDOWS:
        raise Denied(Refusal.RESERVED_FILENAME,
                     f"{name!r} uses a name Windows reserves for a device", name=name)
    if len(name.encode("utf-8")) > _MAX_PATH:
        raise Denied(Refusal.PATH_TOO_LONG, "planned name exceeds the filename limit",
                     name=name[:80] + "…")


def _case_key(path: Path) -> str:
    """Ключ столкновения имён.

    На Windows и macOS `Report.pdf` и `report.pdf` — один файл. План, в котором
    они оба, на такой машине не «перезапишет один другим», а сделает это тихо,
    поэтому столкновение ловится по нормализованному ключу ВСЕГДА, а не только
    там, где файловая система нечувствительна к регистру.
    """
    return str(path).lower()


def check_destinations(entries: Sequence[PlanEntry], policy: ScopePolicy, *,
                       allow_overwrite: bool = False) -> None:
    """§13 — доказать безопасность назначений ДО применения.

    Решение «столкновение здесь безопасно» не отдаётся модели: оно принимается
    этими правилами, одинаково для всех записей.
    """
    seen: dict[str, str] = {}
    for entry in entries:
        if not entry.selected:
            continue
        destination_raw = entry.destination
        if not destination_raw:
            raise Denied(Refusal.INVALID_FILENAME,
                         "plan entry has no destination", source=entry.file_path)
        name = Path(destination_raw).name
        _check_name(name)

        # Канонизация + проверка области действия: назначение обязано лежать
        # внутри разрешённых корней, и это проверяется по РАЗРЕШЁННОМУ пути,
        # поэтому symlink/junction наружу не проходит.
        destination = policy.check(destination_raw, mutating=True)
        if len(str(destination).encode("utf-8")) > _MAX_FULL_PATH:
            raise Denied(Refusal.PATH_TOO_LONG, "destination path is too long",
                         destination=str(destination)[:120] + "…")

        key = _case_key(destination)
        if key in seen:
            raise Denied(Refusal.DUPLICATE_PLANNED_DESTINATION,
                         "two selected entries plan the same destination",
                         destination=str(destination),
                         sources=[seen[key], entry.file_path])
        seen[key] = entry.file_path

        if destination.exists() and _case_key(destination) != _case_key(
                canonical(entry.file_path)):
            if not allow_overwrite:
                raise Denied(Refusal.DESTINATION_COLLISION,
                             "destination already exists and the plan does not "
                             "represent an overwrite",
                             destination=str(destination), source=entry.file_path)
            raise Denied(Refusal.OVERWRITE_NOT_PERMITTED,
                         "overwrite is not permitted by Bossman policy",
                         destination=str(destination))


# ------------------------------------------------------- §14 post-state receipt

def observe_effects(entries: Sequence[PlanEntry]) -> list[AppliedEffect]:
    """§14 — посмотреть на файловую систему САМОСТОЯТЕЛЬНО.

    Ничего из отчёта сайдкара сюда не входит. Вопрос не «что сказал процесс», а
    «что теперь лежит на диске», и отвечает на него только `os.stat` и заново
    посчитанный sha256: содержимое назначения обязано совпасть с содержимым
    источника, СНЯТЫМ ДО эффекта. Совпадение имени и размера тем же самым не
    является.
    """
    observed: list[AppliedEffect] = []
    for entry in entries:
        if not entry.selected or entry.identity is None:
            continue
        identity = entry.identity
        source = Path(identity.canonical_source_path)
        destination = canonical(identity.planned_destination or entry.destination)

        source_absent = not source.exists()
        destination_exists = destination.exists() and destination.is_file()
        content_matches = False
        note = ""
        if destination_exists:
            try:
                content_matches = sha256_file(destination) == identity.content_sha256
                if not content_matches:
                    note = "destination content does not match the pre-effect source"
            except OSError as exc:
                note = f"destination unreadable: {exc.strerror}"
        else:
            note = "destination does not exist"

        # Переименование в том же каталоге: источник и назначение — один путь,
        # тогда «источник отсутствует» законно ложно.
        same_path = _case_key(source) == _case_key(destination)
        verified = destination_exists and content_matches and (
            source_absent or same_path)
        if not verified and not note:
            note = "source still present after a move"
        observed.append(AppliedEffect(
            source_path=identity.source_path,
            destination_path=str(destination),
            source_absent=source_absent,
            destination_exists=destination_exists,
            content_sha256_matches=content_matches,
            verified=verified,
            note=note,
        ))
    return observed


def scan_scope(root: Path) -> dict[str, tuple[int, int]]:
    """Снимок управляемой области: путь → (размер, mtime_ns).

    Нужен, чтобы §14 мог сказать «внутри области не произошло НИЧЕГО лишнего»,
    а не только «то, что просили, произошло». Без этого применение, которое
    заодно переставило соседние файлы, выглядело бы полностью успешным.
    """
    snapshot: dict[str, tuple[int, int]] = {}
    for current, _dirs, files in os.walk(root):
        for name in files:
            path = Path(current) / name
            try:
                stat = path.stat()
            except OSError:
                continue
            snapshot[str(path)] = (stat.st_size, stat.st_mtime_ns)
    return snapshot


def unexpected_mutations(before: dict[str, tuple[int, int]],
                         after: dict[str, tuple[int, int]],
                         expected: Iterable[str]) -> list[str]:
    """Что изменилось внутри области помимо запланированного."""
    allowed = {str(canonical(p)) for p in expected}
    changed: list[str] = []
    for path, meta in after.items():
        if str(canonical(path)) in allowed:
            continue
        if path not in before:
            changed.append(f"appeared: {path}")
        elif before[path] != meta:
            changed.append(f"modified: {path}")
    for path in before:
        if path in after or str(canonical(path)) in allowed:
            continue
        changed.append(f"disappeared: {path}")
    return sorted(changed)
