"""Identity drive: create and read the encrypted identity file on a removable drive. Runs fully offline.

    python -m apps.identity_fill.drive new   E:\      # asks every field at the keyboard, then a passphrase; writes identity.enc
    python -m apps.identity_fill.drive check E:\      # asks the passphrase; prints field NAMES only
    python -m apps.identity_fill.drive seal  E:\      # encrypts an existing identity.json and removes the plain file

Format of identity.enc: JSON {v, kdf: scrypt, n, r, p, salt, nonce, ct}; key = scrypt(passphrase), cipher = AES-256-GCM.
The passphrase is typed by the owner each time and is never written anywhere. Values are never printed.
Only the person the data belongs to (or someone they asked) should fill this in: one account per person per service.
"""
from __future__ import annotations

import base64
import getpass
import json
import os
import sys
from pathlib import Path

from cryptography.hazmat.primitives.ciphers.aead import AESGCM
from cryptography.hazmat.primitives.kdf.scrypt import Scrypt

from .vault import FIELDS, Vault, VaultError

PLAIN, SEALED = "identity.json", "identity.enc"
N, R, P = 2 ** 15, 8, 1
PROMPTS = {
    "given_names": "Given names (as in the passport, Latin letters)", "surname": "Surname (Latin letters)",
    "birth_date": "Date of birth YYYY-MM-DD", "sex": "Sex M / F / X", "nationality": "Nationality code (e.g. CZE)",
    "document_number": "Passport number", "expiry_date": "Passport expiry YYYY-MM-DD", "issuing_state": "Issuing state code",
    "email": "Email for registrations", "phone": "Phone with country code", "address": "Street address", "city": "City",
    "postal_code": "Postal code", "country": "Country",
}


def _key(passphrase: str, salt: bytes) -> bytes:
    return Scrypt(salt=salt, length=32, n=N, r=R, p=P).derive(passphrase.encode("utf-8"))


def seal(record: dict, passphrase: str) -> dict:
    if len(passphrase) < 10:
        raise VaultError("passphrase must be at least 10 characters")
    Vault(record)  # validates field names
    salt, nonce = os.urandom(16), os.urandom(12)
    ct = AESGCM(_key(passphrase, salt)).encrypt(nonce, json.dumps(record, ensure_ascii=False).encode("utf-8"), b"bossman-identity-v1")
    b64 = lambda b: base64.b64encode(b).decode()
    return {"v": 1, "kdf": "scrypt", "n": N, "r": R, "p": P, "salt": b64(salt), "nonce": b64(nonce), "ct": b64(ct)}


def unseal(blob: dict, passphrase: str) -> Vault:
    try:
        d = base64.b64decode
        key = Scrypt(salt=d(blob["salt"]), length=32, n=blob["n"], r=blob["r"], p=blob["p"]).derive(passphrase.encode("utf-8"))
        plain = AESGCM(key).decrypt(d(blob["nonce"]), d(blob["ct"]), b"bossman-identity-v1")
    except Exception:
        raise VaultError("wrong passphrase or damaged identity file") from None
    return Vault(json.loads(plain))


def load_sealed(path: str, passphrase: str) -> Vault:
    return unseal(json.loads(Path(path).read_text(encoding="utf-8")), passphrase)


def _ask_passphrase(confirm: bool) -> str:
    a = getpass.getpass("Passphrase for this drive: ")
    if confirm and a != getpass.getpass("Repeat passphrase: "):
        raise VaultError("passphrases differ")
    return a


def main(argv: list[str]) -> int:
    if len(argv) != 3 or argv[1] not in ("new", "check", "seal"):
        print(__doc__)
        return 2
    root = Path(argv[2])
    try:
        if argv[1] == "new":
            print("Type each value and press Enter (empty = skip). Nothing is shown again after you type it.")
            record = {}
            for name in FIELDS:
                value = input(f"  {PROMPTS[name]}: ").strip()
                if value:
                    record[name] = value
            (root / SEALED).write_text(json.dumps(seal(record, _ask_passphrase(True))), encoding="utf-8")
            print(f"written {SEALED} with fields: {sorted(record)}")
        elif argv[1] == "seal":
            record = json.loads((root / PLAIN).read_text(encoding="utf-8"))
            (root / SEALED).write_text(json.dumps(seal(record, _ask_passphrase(True))), encoding="utf-8")
            (root / PLAIN).unlink()
            print(f"written {SEALED}; plain {PLAIN} removed. Fields: {sorted(record)}")
        else:
            print("fields on this drive:", load_sealed(str(root / SEALED), _ask_passphrase(False)).fields())
    except VaultError as e:
        print("error:", e)
        return 1
    return 0


if __name__ == "__main__":
    sys.exit(main(sys.argv))
