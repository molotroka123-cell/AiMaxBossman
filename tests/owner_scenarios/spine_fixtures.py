"""Строители хребта для владельческих сценариев: цель, план, хранилище, улики.

Здесь нет ни одной проверки — только сборка НАСТОЯЩИХ объектов продукта
(`bossman_shared.*`), чтобы сценарии проверяли поведение продукта, а не макетов.
Макетами остаются ровно три внешних порта админа (политика, казна, конфликты):
их канонические реализации — отдельные сервисы Bossman, и подменять здесь надо
именно их, иначе сценарий проверял бы чужой сервис, а не хребет.

Зависимости: только стандартная библиотека и `bossman_shared` (ставится
корневым `pip install -e .`). Ни sqlalchemy, ни httpx, ни asyncio-плагина —
корневой CI их для набора `tests/` не ставит.
"""
from __future__ import annotations

import contextlib
import os
import sys
import time
from pathlib import Path
from typing import Any, Iterator

# Сценарии обязаны проверять код ЭТОЙ ветки. Запуск скриптом кладёт в sys.path[0]
# каталог скрипта, а не корень репозитория, и `bossman_shared` тогда приезжает из
# editable-установки — то есть из ДРУГОГО рабочего каталога с другим коммитом.
# Прогон бы «зеленел» на чужом коде. Корень ветки встаёт первым.
_REPO_ROOT = str(Path(__file__).resolve().parents[2])
if sys.path[:1] != [_REPO_ROOT]:
    sys.path.insert(0, _REPO_ROOT)

from bossman_shared import evidence as _evidence  # noqa: E402
from bossman_shared.mission_ir import MissionIR  # noqa: E402
from bossman_shared.objective_admission import (AdmissionKernel,  # noqa: E402
                                                CostEstimate, build_proposal)
from bossman_shared.objective_mission import to_mission_ir  # noqa: E402
from bossman_shared.objective_spec import ObjectiveSpec  # noqa: E402
from bossman_shared.objective_store import ObjectiveStore  # noqa: E402

NOW = 1_700_000_000.0


def real_now() -> float:
    """Время по НАСТОЯЩИМ часам.

    `objective_reconcile.reconcile_after_mission` принимает `now`, но вниз, в
    `ObjectiveStore.set_condition`, его НЕ передаёт: там свежесть улики меряется
    `time.time()`. Сценарий на выдуманных часах получает `stale` и падает не по
    делу. Поэтому цепочки, доходящие до SATISFIED, живут на настоящих часах.
    """
    return time.time()

OWNER = "owner:boss"
SCOPE_A = "scope:project-a"
SCOPE_B = "scope:project-b"
OBJECTIVE_A = "objective:project-a-green"
OBJECTIVE_B = "objective:project-b-green"
CAPABILITY = "files.write"
PERMISSION = "perm:repo.write"
SOURCE = "src:build"


# ------------------------------------------------------------------ цель владельца
def spec_dict(*, objective_id: str = OBJECTIVE_A, scope_id: str = SCOPE_A,
              revision: int = 1, previous_digest: str | None = None,
              expires_at: float = NOW + 100_000.0, max_age: float = 3600.0,
              cooldown: float = 0.0, source_ref: str = SOURCE) -> dict[str, Any]:
    """Запрос владельца, уже переведённый в проверяемую цель (без модели)."""
    return {
        "schema_version": 1,
        "owner_id": OWNER,
        "scope_id": scope_id,
        "objective_id": objective_id,
        "revision": revision,
        "previous_digest": previous_digest,
        "predicates": [{"predicate_id": "p-green", "source_ref": source_ref, "field": "green",
                        "value_type": "boolean", "operator": "eq", "expected": True}],
        "sources": [{"source_ref": source_ref, "source_revision": "r1",
                     "max_age_seconds": max_age}],
        "expires_at": expires_at,
        "priority": 5,
        "allowed_triggers": ["scheduled"],
        "permission_refs": [PERMISSION],
        "conflict_keys": [f"{scope_id}:main"],
        "cooldown_seconds": cooldown,
        "limits": {"max_observations": 100, "max_missions": 5,
                   "max_wall_seconds": 1000.0, "max_cost_usd": 10.0},
        "stop_conditions": ["owner_stop"],
    }


def make_spec(**kwargs: Any) -> ObjectiveSpec:
    return ObjectiveSpec.from_dict(spec_dict(**kwargs))


def observation(spec: ObjectiveSpec, *, green: bool = False, observed_at: float = NOW - 10.0,
                observation_id: str = "obs-1", source_ref: str = SOURCE,
                scope_id: str = SCOPE_A) -> dict[str, Any]:
    """Ровно восемь ключей, которые принимает `objective_spec.evaluate`."""
    return {"observation_id": observation_id, "owner_id": OWNER, "scope_id": scope_id,
            "objective_digest": spec.digest, "source_ref": source_ref,
            "source_revision": "r1", "observed_at": observed_at, "values": {"green": green}}


def effect(target: str | Path, *, effect_id: str = "e-write",
           kind: str = "IDEMPOTENT_WRITE", verifiers: list[dict] | None = None) -> dict[str, Any]:
    """Эффект с ПОСТ-СОСТОЯНИЕМ: проверяется файл, а не факт вызова инструмента."""
    declared = verifiers if verifiers is not None else [
        {"kind": "file", "target": str(target), "expect": {"exists": True, "min_bytes": 2},
         "max_age_seconds": 300}]
    return {"effect_id": effect_id, "kind": kind,
            "description": "переписать файл результата", "capabilities": [CAPABILITY],
            "depends_on": [], "verifiers": declared}


# ---------------------------------------------------------- внешние порты (макеты)
class FakePolicy:
    """Текущие гранты владельца. Полномочие вне набора — не полномочие."""

    def __init__(self, permissions: set[str] | None = None,
                 capabilities: set[str] | None = None) -> None:
        self.permissions = permissions if permissions is not None else {PERMISSION}
        self.capabilities = capabilities if capabilities is not None else {CAPABILITY}
        self.calls = 0

    def check_grants(self, owner_id, scope_id, permission_refs, capabilities):
        self.calls += 1
        if not set(permission_refs) <= self.permissions:
            return False, "permission_not_granted"
        if not set(capabilities) <= self.capabilities:
            return False, "capability_not_granted"
        return True, "granted"


class FakeTreasury:
    """Счётчик резервов/возвратов поверх канонической семантики казны."""

    def __init__(self, *, allow: bool = True) -> None:
        self.allow = allow
        self.reserved: list[tuple] = []
        self.released: list[tuple] = []
        self.committed: list[tuple] = []

    def reserve(self, scopes, estimate):
        self.reserved.append((scopes, estimate))
        if not self.allow:
            return False, "", "бюджет исчерпан"
        return True, f"treasury:{len(self.reserved)}", "в пределах конверта"

    def release(self, scopes, estimate):
        self.released.append((scopes, estimate))

    def commit(self, scopes, estimate, actual):
        self.committed.append((scopes, estimate, actual))


class FakeConflicts:
    """Исключительный реестр конфликтных ключей."""

    def __init__(self) -> None:
        self.held: dict[str, str] = {}
        self.releases: list[tuple[tuple[str, ...], str]] = []

    def claim(self, conflict_keys, objective_id, priority):
        blocked = [k for k in conflict_keys if self.held.get(k, objective_id) != objective_id]
        if blocked:
            return False, f"удерживает {self.held[blocked[0]]}"
        for key in conflict_keys:
            self.held[key] = objective_id
        return True, "claimed"

    def release(self, conflict_keys, objective_id):
        self.releases.append((tuple(conflict_keys), objective_id))
        for key in conflict_keys:
            if self.held.get(key) == objective_id:
                del self.held[key]


def kernel(*, policy=None, treasury=None, conflicts=None) -> AdmissionKernel:
    return AdmissionKernel(policy or FakePolicy(), treasury or FakeTreasury(),
                           conflicts or FakeConflicts())


# ------------------------------------------------------------------- хранилище
def make_store(path: Path, spec: ObjectiveSpec | None = None) -> tuple[ObjectiveStore, ObjectiveSpec, Any]:
    """Настоящий файловый ObjectiveStore: долговечность и есть предмет проверки."""
    store = ObjectiveStore(path)
    used = spec if spec is not None else make_spec()
    return store, used, store.create(used)


def activate(store: ObjectiveStore, state, *, objective_id: str = OBJECTIVE_A,
             source_ref: str = SOURCE, now: float = NOW):
    state = store.enroll_sources(objective_id, (source_ref,), owner_id=OWNER,
                                 expected_version=state.version)
    return store.transition(objective_id, "ACTIVE", now=now, owner_id=OWNER,
                            expected_version=state.version)


def ready_store(path: Path, *, spec: ObjectiveSpec | None = None,
                objective_id: str = OBJECTIVE_A, source_ref: str = SOURCE):
    store, used, state = make_store(path, spec)
    state = activate(store, state, objective_id=objective_id, source_ref=source_ref)
    return store, used, state


def propose(store: ObjectiveStore, spec: ObjectiveSpec, *, target: str | Path,
            now: float = NOW, objective_id: str = OBJECTIVE_A,
            estimate: CostEstimate | None = None, capabilities=(CAPABILITY,),
            observed_at: float = NOW - 10.0, observation_id: str = "obs-1",
            effects: tuple[dict, ...] | None = None, scope_id: str = SCOPE_A,
            source_ref: str = SOURCE):
    """Отклонение наблюдалось → предложение. Предложение НЕ есть полномочие."""
    batch = [observation(spec, observed_at=observed_at, observation_id=observation_id,
                         scope_id=scope_id, source_ref=source_ref)]
    return build_proposal(
        store, objective_id, batch, now, "scheduled",
        requested_capabilities=tuple(capabilities),
        expected_effects=effects if effects is not None else (effect(target),),
        cost_estimate=estimate if estimate is not None else CostEstimate(0.5, 1000, 30.0))


def admitted_mission(store: ObjectiveStore, spec: ObjectiveSpec, *, target: str | Path,
                     now: float = NOW, objective_id: str = OBJECTIVE_A,
                     scope_id: str = SCOPE_A, source_ref: str = SOURCE, admission=None,
                     observation_id: str = "obs-1", observed_at: float = NOW - 10.0,
                     goal: str = "привести проект в зелёное состояние"):
    """Полный участок «планирование»: предложение → допуск → MissionIR."""
    proposal = propose(store, spec, target=target, now=now, objective_id=objective_id,
                       scope_id=scope_id, source_ref=source_ref,
                       observation_id=observation_id, observed_at=observed_at)
    if proposal is None:
        raise AssertionError("цель не породила предложение: наблюдение не отклонилось")
    krn = admission or kernel()
    decision = krn.admit(store, proposal, now=now)
    if not decision.admitted:
        raise AssertionError(f"допуск отказал: {decision.reason}")
    mission = to_mission_ir(proposal, decision, owner_id=OWNER, project_id=scope_id, goal=goal)
    return proposal, decision, mission


# ------------------------------------------------------------------- подпись улик
@contextlib.contextmanager
def evidence_key(directory: Path) -> Iterator[Path]:
    """Ключ подписи улик — во временном каталоге сценария, не в ~/.bossman.

    Кэш модуля сбрасывается на входе и на выходе: иначе следующий сценарий
    подписывал бы чужим ключом и «проверял» бы не то, что думает.
    """
    path = Path(directory) / "keys" / "evidence.key"
    path.parent.mkdir(parents=True, exist_ok=True)
    previous = os.environ.get("BOSSMAN_EVIDENCE_KEY_FILE")
    os.environ["BOSSMAN_EVIDENCE_KEY_FILE"] = str(path)
    _evidence.reset_cache()
    try:
        _evidence.load_or_create_key()
        yield path
    finally:
        if previous is None:
            os.environ.pop("BOSSMAN_EVIDENCE_KEY_FILE", None)
        else:
            os.environ["BOSSMAN_EVIDENCE_KEY_FILE"] = previous
        _evidence.reset_cache()


def mission_of(store: ObjectiveStore, spec: ObjectiveSpec, target: Path, **kwargs) -> MissionIR:
    return admitted_mission(store, spec, target=target, **kwargs)[2]
