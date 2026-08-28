"""Хранилище секретов: наружу выдаётся ССЫЛКА, значение — только адаптеру.

Одно решение определяет форму всего файла: **домен, аудит, логи и мост
оперируют `auth_ref`, а не токеном**. Значение достаётся ровно в одном месте —
в адаптере, в момент вызова провайдера, — и живёт столько, сколько длится
вызов. Токен, попавший в строку аргумента или в поле события, оттуда уже не
убирается: он останется в резервной копии лога, которую никто не перечитает.

Отсюда же вытекает, чего в интерфейсе НЕТ. Спека (`04_AUTH_AND_SECRET_VAULT`)
перечисляет разрешённые операции — `resolve_for_adapter`, `rotate`, `revoke`,
`health` — и отдельной строкой запрещает `get_plaintext_for_model()`. Метода с
таким смыслом здесь нет и не должно появиться: отсутствие метода — единственная
защита, которая не забывается под давлением срока.

## Про шифрование

`LocalEncryptedVault` — хранилище **для разработки**. Оно шифрует файл на диске
так, что случайно открытый `secrets.json` не выдаёт токен, и связывает запись с
владельцем, чтобы работа одного аккаунта не расшифровала секрет другого
(инвариант A1). Оно не заменяет KMS: мастер-ключ лежит рядом, на той же машине,
и злоумышленник с правами процесса получит и ключ, и файл. Это сказано прямо,
потому что «локальное шифрованное хранилище» звучит надёжнее, чем оно есть.

В бою под интерфейс `SecretVault` подставляется внешнее хранилище. Ничего,
кроме этого файла, менять при этом не придётся: домен видит только ссылки.

Криптография собрана из стандартной библиотеки (новых зависимостей приложение
не берёт): HKDF на HMAC-SHA256 для вывода ключей записи, потоковое шифрование
на HMAC-SHA256 в режиме счётчика, аутентификация encrypt-then-MAC. Связанные
данные (`ref`, владелец, вид секрета) входят в MAC — переклеить запись с одного
аккаунта на другой не получится.
"""
from __future__ import annotations

import base64
import hashlib
import hmac
import json
import os
import secrets as _secrets
import stat
from dataclasses import dataclass
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Protocol, runtime_checkable

_KEY_BYTES = 32
_NONCE_BYTES = 16
_ENC_INFO = b"social-farm/vault/enc/v1"
_MAC_INFO = b"social-farm/vault/mac/v1"
_RECORD_VERSION = 1


class VaultError(RuntimeError):
    """Общая ошибка хранилища секретов."""


class SecretNotFound(VaultError):
    """Ссылки нет. Это не «пустой токен», это отсутствующая запись."""


class SecretRevoked(VaultError):
    """Секрет отозван. Возвращать значение после отзыва — значит не отзывать."""


class SecretOwnershipError(VaultError):
    """Ссылка принадлежит другому аккаунту (инвариант A1).

    Работа одного аккаунта не должна уметь достать секрет другого, даже зная
    его ссылку. Проверка владельца — не удобство, а изоляция.
    """


def _utc() -> str:
    return datetime.now(timezone.utc).isoformat()


class SecretValue:
    """Раскрытое значение секрета. Показать его можно только явно.

    Тип существует ради одного: `f"{token}"`, `repr(token)`, `print(token)` и
    любая сериализация по умолчанию дают маску, а не значение. Секрет
    протекает не через злой умысел, а через отладочный вывод, который забыли
    убрать, и через `logger.info("...%s", ctx)` со словарём контекста внутри.
    """

    __slots__ = ("_value",)

    def __init__(self, value: str) -> None:
        self._value = str(value)

    def reveal(self) -> str:
        """Единственный способ получить значение. Вызывается в адаптере."""
        return self._value

    @property
    def fingerprint(self) -> str:
        """Короткий отпечаток для сверки «тот же секрет или другой».

        Его можно писать в лог: по восьми шестнадцатеричным знакам от sha256
        значение не восстанавливается, а «токен сменился» видно.
        """
        return hashlib.sha256(self._value.encode("utf-8")).hexdigest()[:8]

    def __repr__(self) -> str:
        return f"SecretValue(fingerprint={self.fingerprint}, len=hidden)"

    __str__ = __repr__

    def __eq__(self, other: object) -> bool:
        if not isinstance(other, SecretValue):
            return NotImplemented
        return hmac.compare_digest(self._value, other._value)

    def __hash__(self) -> int:
        return hash(self.fingerprint)

    def __bool__(self) -> bool:
        return bool(self._value)

    # Сериализаторы (json, dataclasses.asdict, pprint) не должны получать
    # значение даже случайно.
    def __getstate__(self) -> dict[str, Any]:
        raise VaultError("SecretValue не сериализуется: наружу идёт ссылка, не значение")

    def __format__(self, spec: str) -> str:
        return repr(self)


@dataclass(frozen=True, slots=True)
class SecretMetadata:
    """Всё, что о секрете можно рассказать наружу. Значения здесь нет."""

    ref: str
    kind: str
    owner_account_id: str
    created_at: str
    rotated_at: str | None = None
    revoked_at: str | None = None
    fingerprint: str = ""

    @property
    def revoked(self) -> bool:
        return bool(self.revoked_at)

    def to_dict(self) -> dict[str, Any]:
        return {"ref": self.ref, "kind": self.kind,
                "owner_account_id": self.owner_account_id,
                "created_at": self.created_at, "rotated_at": self.rotated_at,
                "revoked_at": self.revoked_at, "fingerprint": self.fingerprint}


@dataclass(frozen=True, slots=True)
class SecretHealth:
    """Ответ на `health(secret_ref)`: жив ли секрет и почему нет."""

    ref: str
    present: bool
    revoked: bool
    readable: bool
    reason: str = ""

    @property
    def usable(self) -> bool:
        return self.present and self.readable and not self.revoked

    def to_dict(self) -> dict[str, Any]:
        return {"ref": self.ref, "present": self.present, "revoked": self.revoked,
                "readable": self.readable, "usable": self.usable, "reason": self.reason}


@runtime_checkable
class SecretVault(Protocol):
    """Разрешённые операции — ровно четыре плюс запись.

    Метода вида `get_plaintext_for_model()` в протоколе нет намеренно
    (`04_AUTH_AND_SECRET_VAULT`, раздел Secret API). Модель и мост не получают
    значение ни под каким названием.
    """

    def store(self, value: str, *, kind: str, owner_account_id: str,
              ref: str | None = None) -> SecretMetadata: ...

    def resolve_for_adapter(self, ref: str, *,
                            owner_account_id: str) -> SecretValue: ...

    def rotate(self, ref: str, new_value: str, *,
               owner_account_id: str) -> SecretMetadata: ...

    def revoke(self, ref: str) -> SecretMetadata: ...

    def health(self, ref: str) -> SecretHealth: ...

    def metadata(self, ref: str) -> SecretMetadata: ...


# ------------------------------------------------------------------ крипто

def _hkdf(master: bytes, salt: bytes, info: bytes, length: int = _KEY_BYTES) -> bytes:
    """HKDF-SHA256. Мастер-ключ не используется напрямую ни для чего."""
    prk = hmac.new(salt, master, hashlib.sha256).digest()
    out, block, counter = b"", b"", 1
    while len(out) < length:
        block = hmac.new(prk, block + info + bytes([counter]), hashlib.sha256).digest()
        out += block
        counter += 1
    return out[:length]


def _keystream(key: bytes, nonce: bytes, length: int) -> bytes:
    out, counter = bytearray(), 0
    while len(out) < length:
        out += hmac.new(key, nonce + counter.to_bytes(8, "big"), hashlib.sha256).digest()
        counter += 1
    return bytes(out[:length])


def _b64(raw: bytes) -> str:
    return base64.b64encode(raw).decode("ascii")


def _unb64(text: str) -> bytes:
    return base64.b64decode(text.encode("ascii"))


def _seal(master: bytes, plaintext: str, aad: bytes) -> dict[str, Any]:
    nonce = _secrets.token_bytes(_NONCE_BYTES)
    enc_key = _hkdf(master, nonce, _ENC_INFO)
    mac_key = _hkdf(master, nonce, _MAC_INFO)
    body = plaintext.encode("utf-8")
    cipher = bytes(a ^ b for a, b in zip(body, _keystream(enc_key, nonce, len(body))))
    tag = hmac.new(mac_key, nonce + b"|" + aad + b"|" + cipher, hashlib.sha256).digest()
    return {"v": _RECORD_VERSION, "nonce": _b64(nonce), "cipher": _b64(cipher),
            "tag": _b64(tag)}


def _open(master: bytes, sealed: dict[str, Any], aad: bytes) -> str:
    if int(sealed.get("v") or 0) != _RECORD_VERSION:
        raise VaultError(f"неизвестная версия записи хранилища: {sealed.get('v')!r}")
    nonce, cipher = _unb64(sealed["nonce"]), _unb64(sealed["cipher"])
    mac_key = _hkdf(master, nonce, _MAC_INFO)
    expected = hmac.new(mac_key, nonce + b"|" + aad + b"|" + cipher, hashlib.sha256)
    if not hmac.compare_digest(expected.digest(), _unb64(sealed["tag"])):
        # Расхождение MAC — это либо другой ключ, либо правка файла, либо
        # попытка расшифровать чужую запись. Ни один из случаев не тот, где
        # стоит вернуть «что получилось».
        raise VaultError("запись хранилища не проходит проверку целостности")
    enc_key = _hkdf(master, nonce, _ENC_INFO)
    return bytes(a ^ b for a, b in zip(
        cipher, _keystream(enc_key, nonce, len(cipher)))).decode("utf-8")


# ------------------------------------------------------------------ ключ

def load_master_key(data_dir: Path | str, *, env: dict[str, str] | None = None) -> bytes:
    """Мастер-ключ: из `SF_VAULT_KEY` либо из файла с правами 0600.

    Порядок именно такой. В бою ключ приходит окружением и на диск не ложится;
    файл — это удобство разработки, и он создаётся при первом запуске, чтобы
    приложение не требовало ритуала до первого полезного действия.
    """
    env = os.environ if env is None else env
    supplied = (env.get("SF_VAULT_KEY") or "").strip()
    if supplied:
        return _decode_key(supplied)

    directory = Path(data_dir)
    directory.mkdir(parents=True, exist_ok=True)
    os.chmod(directory, stat.S_IRWXU)
    key_path = directory / "vault.key"
    if key_path.exists():
        os.chmod(key_path, stat.S_IRUSR | stat.S_IWUSR)
        return _decode_key(key_path.read_text(encoding="ascii").strip())

    generated = _secrets.token_bytes(_KEY_BYTES)
    # Права выставляются ДО записи: файл не должен ни одного мгновения
    # существовать с правами по умолчанию.
    handle = os.open(key_path, os.O_WRONLY | os.O_CREAT | os.O_EXCL,
                     stat.S_IRUSR | stat.S_IWUSR)
    with os.fdopen(handle, "w", encoding="ascii") as stream:
        stream.write(_b64(generated))
    return generated


def _decode_key(text: str) -> bytes:
    for decoder in (base64.b64decode, bytes.fromhex):
        try:
            raw = decoder(text)  # type: ignore[operator]
        except Exception:       # noqa: BLE001 — перебор кодировок, а не глушение
            continue
        if len(raw) >= _KEY_BYTES:
            return raw[:_KEY_BYTES]
    if len(text) >= _KEY_BYTES:
        return hashlib.sha256(text.encode("utf-8")).digest()
    raise VaultError(
        "SF_VAULT_KEY слишком короткий: нужно не меньше 32 байт "
        "(base64 или hex). Короткий ключ хуже отсутствующего — он создаёт "
        "видимость защиты.")


# ------------------------------------------------------------------ хранилище

class LocalEncryptedVault:
    """Локальное шифрованное хранилище для разработки.

    Файл `secrets.json` в каталоге данных, права 0600, каталог 0700. Значение
    ни при каких условиях не пишется в открытом виде — даже во временный файл:
    запись идёт через `os.open` с явными правами и `os.replace`.
    """

    def __init__(self, data_dir: Path | str, *, master_key: bytes | None = None,
                 env: dict[str, str] | None = None) -> None:
        self._dir = Path(data_dir)
        self._dir.mkdir(parents=True, exist_ok=True)
        os.chmod(self._dir, stat.S_IRWXU)
        self._path = self._dir / "secrets.json"
        self._master = master_key or load_master_key(self._dir, env=env)
        self._records: dict[str, dict[str, Any]] = self._read()

    # -- файл -------------------------------------------------------------
    def _read(self) -> dict[str, dict[str, Any]]:
        if not self._path.exists():
            return {}
        try:
            raw = json.loads(self._path.read_text(encoding="utf-8"))
        except (OSError, ValueError) as exc:
            raise VaultError(f"файл хранилища секретов нечитаем: {exc}") from exc
        return dict(raw.get("records") or {})

    def _write(self) -> None:
        payload = json.dumps({"version": _RECORD_VERSION, "records": self._records},
                             ensure_ascii=False, indent=2, sort_keys=True)
        temp = self._path.with_suffix(".tmp")
        handle = os.open(temp, os.O_WRONLY | os.O_CREAT | os.O_TRUNC,
                         stat.S_IRUSR | stat.S_IWUSR)
        with os.fdopen(handle, "w", encoding="utf-8") as stream:
            stream.write(payload)
        os.replace(temp, self._path)
        os.chmod(self._path, stat.S_IRUSR | stat.S_IWUSR)

    # -- операции ---------------------------------------------------------
    @staticmethod
    def _aad(ref: str, owner: str, kind: str) -> bytes:
        return f"{ref}|{owner}|{kind}".encode("utf-8")

    def store(self, value: str, *, kind: str, owner_account_id: str,
              ref: str | None = None) -> SecretMetadata:
        if not owner_account_id:
            raise VaultError("секрет без владельца хранить нельзя: "
                             "без владельца не работает изоляция аккаунтов")
        reference = ref or f"secret:{kind}:{owner_account_id}:{_secrets.token_hex(8)}"
        sealed = _seal(self._master, value, self._aad(reference, owner_account_id, kind))
        self._records[reference] = {
            "kind": kind, "owner_account_id": owner_account_id,
            "created_at": _utc(), "rotated_at": None, "revoked_at": None,
            "fingerprint": SecretValue(value).fingerprint, "sealed": sealed}
        self._write()
        return self.metadata(reference)

    def _record(self, ref: str) -> dict[str, Any]:
        record = self._records.get(ref)
        if record is None:
            raise SecretNotFound(f"секрета по ссылке {ref} нет в хранилище")
        return record

    def resolve_for_adapter(self, ref: str, *, owner_account_id: str) -> SecretValue:
        """Единственная дверь к значению. Открывается только владельцу."""
        record = self._record(ref)
        if record["owner_account_id"] != owner_account_id:
            raise SecretOwnershipError(
                f"ссылка {ref} принадлежит другому аккаунту: "
                f"запрос от {owner_account_id} отклонён")
        if record.get("revoked_at"):
            raise SecretRevoked(f"секрет {ref} отозван {record['revoked_at']}")
        plain = _open(self._master, record["sealed"],
                      self._aad(ref, record["owner_account_id"], record["kind"]))
        return SecretValue(plain)

    def rotate(self, ref: str, new_value: str, *,
               owner_account_id: str) -> SecretMetadata:
        record = self._record(ref)
        if record["owner_account_id"] != owner_account_id:
            raise SecretOwnershipError(f"ссылка {ref} принадлежит другому аккаунту")
        record["sealed"] = _seal(self._master, new_value,
                                 self._aad(ref, owner_account_id, record["kind"]))
        record["rotated_at"] = _utc()
        # Ротация возвращает отозванный секрет к жизни только явным
        # переподключением — здесь она этого не делает.
        record["fingerprint"] = SecretValue(new_value).fingerprint
        self._write()
        return self.metadata(ref)

    def revoke(self, ref: str) -> SecretMetadata:
        """Отзыв стирает значение, а не помечает его.

        Помеченный, но сохранённый токен — это токен, который однажды достанут
        «на время расследования». Значение затирается, метаданные остаются:
        аудиту нужна история, а не материал.
        """
        record = self._record(ref)
        record["revoked_at"] = record.get("revoked_at") or _utc()
        record["sealed"] = _seal(self._master, "",
                                 self._aad(ref, record["owner_account_id"],
                                           record["kind"]))
        record["fingerprint"] = ""
        self._write()
        return self.metadata(ref)

    def health(self, ref: str) -> SecretHealth:
        record = self._records.get(ref)
        if record is None:
            return SecretHealth(ref=ref, present=False, revoked=False, readable=False,
                                reason="ссылки нет в хранилище")
        if record.get("revoked_at"):
            return SecretHealth(ref=ref, present=True, revoked=True, readable=False,
                                reason=f"отозван {record['revoked_at']}")
        try:
            _open(self._master, record["sealed"],
                  self._aad(ref, record["owner_account_id"], record["kind"]))
        except VaultError as exc:
            return SecretHealth(ref=ref, present=True, revoked=False, readable=False,
                                reason=str(exc))
        return SecretHealth(ref=ref, present=True, revoked=False, readable=True)

    def metadata(self, ref: str) -> SecretMetadata:
        record = self._record(ref)
        return SecretMetadata(
            ref=ref, kind=record["kind"],
            owner_account_id=record["owner_account_id"],
            created_at=record["created_at"], rotated_at=record.get("rotated_at"),
            revoked_at=record.get("revoked_at"),
            fingerprint=record.get("fingerprint") or "")

    def refs_for(self, owner_account_id: str) -> list[str]:
        return sorted(r for r, v in self._records.items()
                      if v["owner_account_id"] == owner_account_id)


class InMemoryVault(LocalEncryptedVault):
    """То же хранилище без диска — для тестов и для `--dry-run`.

    Наследование, а не копия: если поведение локального хранилища разойдётся с
    тестовым, тесты перестанут что-либо доказывать.
    """

    def __init__(self, *, master_key: bytes | None = None) -> None:
        self._dir = Path(".")
        self._path = Path(os.devnull)
        self._master = master_key or _secrets.token_bytes(_KEY_BYTES)
        self._records = {}

    def _read(self) -> dict[str, dict[str, Any]]:
        return {}

    def _write(self) -> None:
        return None


__all__ = ["InMemoryVault", "LocalEncryptedVault", "SecretHealth", "SecretMetadata",
           "SecretNotFound", "SecretOwnershipError", "SecretRevoked", "SecretValue",
           "SecretVault", "VaultError", "load_master_key"]
