"""Разрешаемая улика для условия цели (P0-2, IV5-STORE-002).

Раньше `ObjectiveStore.set_condition(..., "SATISFIED", evidence_ref=...)`
проверял ровно одно: что ссылка — непустая строка. `"x"` непустая. Значит
зелёный статус цели выставлялся ПРОЗОЙ МОДЕЛИ: достаточно было написать любое
слово в поле улики. Это и есть релиз-блокер.

Здесь улика перестаёт быть строкой и становится ЗАПИСЬЮ, которую обязан
РАЗРЕШИТЬ резолвер. Ссылка `oev1:<32 hex>` указывает на долговременную запись в
хранилище; запись подписана HMAC-ключом процесса (`bossman_shared.evidence` —
единственный подписант, своей криптографии здесь нет) и ПРИВЯЗАНА к:

  * цели            — `objective_id`;
  * условию         — `condition` (улика здоровья не является уликой SATISFIED);
  * телу цели       — `spec_digest` + `revision` (ревизия под целью сменилась —
                      улика о прежней редакции больше ни о чём не говорит);
  * применимости    — `owner_id`, `scope_id`, `lifecycle` (улика, снятая на
                      активной цели, не применима к приостановленной);
  * породившему прогону — `run_id` (миссия/бронь, в которой улика получена);
  * свежести        — `minted_at`/`fresh_until` плюс отзыв и одноразовость.

Всё fail-closed: неразрешимая ссылка, неизвестный идентификатор, испорченная
подпись, несовпадение любой привязки, отозванная или протухшая запись —
ОТКАЗ писать SATISFIED, а не «улики нет, значит и претензий нет».

ЭТО НЕ КАНАРЕЙКА. `bossman_shared.objective_canary.CanaryAttestation`
свидетельствует о ЗДОРОВЬЕ ВЫПУСКА в когорте; это другое утверждение, и оно
само по себе никогда не удовлетворяет условие цели. Формат ссылки отдельный
(`oev1:` против `cev1:`), поля привязки отдельные, и ни одна канареечная
аттестация здесь не разрешается.

Модуль ЧИСТЫЙ: ни SQL, ни часов, ни ввода-вывода. Время входит аргументом,
хранение — забота `ObjectiveStore`.
"""
from __future__ import annotations

import math
import uuid
from typing import Any, Mapping

from . import evidence as _evidence

# Версионированный префикс ссылки. Меняется формат — меняется префикс, и старые
# ссылки перестают разрешаться, а не «разрешаются как-нибудь».
SCHEME = "oev1"
REF_PREFIX = SCHEME + ":"
_HEX = frozenset("0123456789abcdef")

# Подписант улик условия. Аллоулист живёт в `evidence.TRUSTED_SIGNERS`: модуль
# V5 не вправе дописать туда себя.
DEFAULT_SIGNER = "bossman_v3.verifier"

# Окно свежести по умолчанию: улика — наблюдение о мире, а мир меняется.
DEFAULT_TTL_SECONDS = 3600.0

# Поля, входящие в подписываемое тело. `issued_at` НЕ используется: это
# служебное поле `evidence.sign_fields`, и одноимённая привязка была бы им
# затёрта. Время чеканки называется `minted_at` именно поэтому.
BINDING_FIELDS = (
    "schema", "evidence_id", "objective_id", "condition", "spec_digest", "revision",
    "owner_id", "scope_id", "lifecycle", "run_id", "minted_at", "fresh_until",
    "single_use", "observation_digests", "detail",
)

# Машиночитаемые причины отказа. Попадают в текст исключения хранилища.
MALFORMED_REF = "malformed_reference"
UNRESOLVED = "unresolved_reference"
TAMPERED = "tampered_record"
WRONG_OBJECTIVE = "wrong_objective"
WRONG_CONDITION = "wrong_condition"
WRONG_REVISION = "wrong_revision"
WRONG_APPLICABILITY = "wrong_applicability"
WRONG_RUN = "wrong_producing_run"
REVOKED = "revoked"
STALE = "stale"
CONSUMED = "already_consumed"
NO_CLOCK = "unusable_resolution_time"


class ConditionEvidenceError(ValueError):
    """Улику нельзя было даже отчеканить: входные данные не годятся."""


def _finite(value: Any) -> bool:
    return type(value) in (int, float) and type(value) is not bool and math.isfinite(value)


def _nonempty(value: Any) -> bool:
    return type(value) is str and bool(value.strip())


def new_evidence_id() -> str:
    return uuid.uuid4().hex


def make_ref(evidence_id: str) -> str:
    return REF_PREFIX + evidence_id


def parse_ref(ref: Any) -> str | None:
    """Идентификатор записи из ссылки, либо None. `"x"` не ссылка — и это точка."""
    if type(ref) is not str:
        return None
    text = ref.strip()
    if not text.startswith(REF_PREFIX):
        return None
    evidence_id = text[len(REF_PREFIX):]
    if len(evidence_id) != 32 or not set(evidence_id) <= _HEX:
        return None
    return evidence_id


def binding_payload(*, evidence_id: str, objective_id: str, condition: str, spec_digest: str,
                    revision: int, owner_id: str, scope_id: str, lifecycle: str, run_id: str,
                    minted_at: float, fresh_until: float, single_use: bool,
                    observation_digests: tuple[str, ...] = (), detail: str = "") -> dict[str, Any]:
    """Тело, которое подписывает чеканщик. Каждое поле — привязка.

    Без них подпись доказывает лишь, что НЕКТО доверенный сказал НЕЧТО, а это
    не доказательство про ЭТУ цель, ЭТО условие и ЭТУ редакцию.
    """
    if not _nonempty(objective_id) or not _nonempty(condition) or not _nonempty(spec_digest):
        raise ConditionEvidenceError("evidence must bind objective, condition and spec digest")
    if not _nonempty(owner_id) or not _nonempty(scope_id) or not _nonempty(lifecycle):
        raise ConditionEvidenceError("evidence must bind owner, scope and lifecycle")
    if not _nonempty(run_id):
        raise ConditionEvidenceError("evidence must name the run that produced it")
    if type(revision) is not int or type(revision) is bool or revision < 1:
        raise ConditionEvidenceError("evidence must bind a positive integer revision")
    if not _finite(minted_at) or not _finite(fresh_until) or fresh_until <= minted_at:
        raise ConditionEvidenceError("evidence must carry a positive freshness window")
    if type(single_use) is not bool:
        raise ConditionEvidenceError("single_use must be boolean")
    digests = tuple(observation_digests)
    if any(not _nonempty(d) for d in digests):
        raise ConditionEvidenceError("observation digests must be non-empty strings")
    return {
        "schema": SCHEME,
        "evidence_id": str(evidence_id),
        "objective_id": objective_id,
        "condition": condition,
        "spec_digest": spec_digest,
        "revision": int(revision),
        "owner_id": owner_id,
        "scope_id": scope_id,
        "lifecycle": lifecycle,
        "run_id": run_id,
        "minted_at": float(minted_at),
        "fresh_until": float(fresh_until),
        "single_use": bool(single_use),
        "observation_digests": list(digests),
        "detail": str(detail)[:500],
    }


def mint_record(payload: Mapping[str, Any], *, signer: str = DEFAULT_SIGNER,
                key: bytes | None = None) -> dict[str, Any]:
    """Подписать тело привязок. Подпись — из `bossman_shared.evidence`, не своя."""
    body = dict(payload)
    return {**body, **_evidence.sign_fields(body, signer=signer, key=key)}


def verify_record(record: Any, *, key: bytes | None = None) -> bool:
    """Подпись валидна, подписант доверенный, и все поля привязки на месте."""
    if not isinstance(record, Mapping):
        return False
    if record.get("schema") != SCHEME:
        return False
    if any(field not in record for field in BINDING_FIELDS):
        return False
    try:
        return bool(_evidence.verify_signed(record, key=key))
    except Exception:  # noqa: BLE001 — недоступный ключ это отказ, не падение
        return False


def check_bindings(record: Mapping[str, Any], *, objective_id: str, condition: str,
                   spec_digest: str, revision: int, owner_id: str, scope_id: str,
                   lifecycle: str, now: float) -> str | None:
    """Причина отказа, либо None если улика применима здесь и сейчас.

    Порядок проверок — часть контракта: первая непройденная привязка и называет
    отказ, поэтому отказ объясним.
    """
    if not _finite(now):
        return NO_CLOCK
    if record.get("objective_id") != objective_id:
        return WRONG_OBJECTIVE
    if record.get("condition") != condition:
        return WRONG_CONDITION
    if record.get("spec_digest") != spec_digest or record.get("revision") != int(revision):
        return WRONG_REVISION
    if (record.get("owner_id") != owner_id or record.get("scope_id") != scope_id
            or record.get("lifecycle") != lifecycle):
        return WRONG_APPLICABILITY
    if not _nonempty(record.get("run_id")):
        return WRONG_RUN
    minted_at, fresh_until = record.get("minted_at"), record.get("fresh_until")
    if not _finite(minted_at) or not _finite(fresh_until):
        return TAMPERED
    if float(now) < float(minted_at) or float(now) > float(fresh_until):
        return STALE
    return None
