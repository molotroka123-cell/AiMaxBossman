"""Rights gate. Nothing is cut, rendered or queued from a source unless the owner approved its rights, and the
approval is bound to the exact file (size + content fingerprint): a swapped or edited file voids it."""
from __future__ import annotations

import hashlib
from pathlib import Path

from .store import KINDS


class RightsError(PermissionError):
    def __init__(self, code: str, message: str):
        super().__init__(message)
        self.code, self.message = code, message


def fingerprint(path: str | Path) -> str | None:
    """size + sha256 of the first and last MiB: cheap, and changes when the file is replaced or re-encoded."""
    p = Path(path)
    if not p.is_file():
        return None
    size = p.stat().st_size
    h = hashlib.sha256(str(size).encode())
    with p.open("rb") as f:
        h.update(f.read(1 << 20))
        if size > 2 << 20:
            f.seek(-(1 << 20), 2)
            h.update(f.read(1 << 20))
    return f"{size}:{h.hexdigest()[:32]}"


def validate_registration(doc: dict) -> None:
    if doc.get("kind") not in KINDS:
        raise RightsError("BAD_KIND", f"kind должен быть одним из {KINDS}")
    if not str(doc.get("rights_note") or "").strip():
        raise RightsError("RIGHTS_NOTE_REQUIRED", "нужна rights_note: откуда материал и на каком основании его можно использовать")
    if doc["kind"] == "licensed" and not (doc.get("licence_url") or doc.get("licence_doc")):
        raise RightsError("LICENCE_REQUIRED", "для стороннего материала нужна ссылка на лицензию/разрешение (licence_url или licence_doc)")


def check(source: dict | None) -> None:
    """Raise RightsError unless the source may be used right now."""
    if source is None:
        raise RightsError("SOURCE_UNKNOWN", "источник не зарегистрирован")
    validate_registration(source)
    if source.get("approved_by_owner") is not True or not source.get("rights_approval"):
        raise RightsError("RIGHTS_NOT_APPROVED", f"права на источник {source.get('id')} не подтверждены владельцем: резка и очередь запрещены")
    appr = source["rights_approval"]
    if source.get("path"):
        fp = fingerprint(source["path"])
        if fp is None:
            raise RightsError("SOURCE_MISSING", "файл источника не найден")
        if appr.get("fingerprint") and appr["fingerprint"] != fp:
            raise RightsError("SOURCE_CHANGED", "файл источника изменился после одобрения прав: нужно новое одобрение владельца")


def consent_blocked(source: dict | None) -> bool:
    """People are in frame and the owner has not recorded their consent."""
    return bool(source and source.get("people_in_frame") and not (source.get("consent_recorded") and source.get("consent")))


def check_consent(source: dict | None) -> None:
    """Approval/publication gate: no short with people in frame without the owner-recorded consent."""
    if consent_blocked(source):
        raise RightsError("CONSENT_REQUIRED", "в кадре люди, а согласие на использование их лица/голоса владелец не подтвердил: "
                                              "одобрение и публикация заблокированы (POST /sources/{id}/consent)")
