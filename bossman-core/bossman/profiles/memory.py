"""Отдельная main-папка профиля, где накапливаются знания.

Каждый профиль получает свой корень `workspace_dir/_profiles/<id>/` с подпапкой
`knowledge/` — сюда копятся знания именно этого аккаунта. Для существующего
context_engine namespace знаний профиля = `profile:<id>` (кладётся в колонку
`project`), поэтому память копится изолированно, без изменения схемы БД.

Доступ к «личным данным» гейтится тумблером `personal_data`: `personal.read`
проходит через capability gate, как и остальные capability.
"""
from __future__ import annotations

import hashlib
from pathlib import Path

from .models import Profile
from .store import _SAFE, safe_id


def profile_root(workspace_dir: str | Path, profile_id: str) -> Path:
    """Корень профиля под workspace. Имя санитизировано → выход за workspace невозможен."""
    base = Path(workspace_dir).resolve() / "_profiles"
    sid = safe_id(profile_id)
    if not sid:
        raise ValueError("empty profile id")
    # safe_id режет до 48 символов: у профиля с длинным именем это отрезало
    # ровно случайный суффикс new_profile_id, и два профиля делили одну папку
    # знаний. Длинный id → префикс + хэш полного санитизированного id.
    full = _SAFE.sub("-", str(profile_id).strip().lower()).strip("-.")
    if full != sid:
        sid = f"{full[:39].rstrip('-.')}-{hashlib.sha256(full.encode('utf-8')).hexdigest()[:8]}"
    root = (base / sid).resolve()
    if base.resolve() not in root.parents and root != (base / sid).resolve():
        raise ValueError("profile root escapes workspace")
    return root


def knowledge_dir(workspace_dir: str | Path, profile_id: str, *, create: bool = True) -> Path:
    """Папка знаний профиля. Создаётся по требованию."""
    kd = profile_root(workspace_dir, profile_id) / "knowledge"
    if create:
        kd.mkdir(parents=True, exist_ok=True)
    return kd


def memory_namespace(profile: Profile) -> str:
    """Namespace для context_engine (`project`). Копит знания на профиль."""
    return profile.memory_namespace or f"profile:{profile.id}"
