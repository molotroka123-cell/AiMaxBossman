```python
import asyncio
import hashlib, hmac, json, os, re, secrets, tempfile, threading
from datetime import UTC, datetime, timedelta
from pathlib import Path
from fastapi import HTTPException, Request

KEY_RE = re.compile(r"[0-9a-fA-F]{64}\Z")

def _valid_key(value: object) -> bool:
    return isinstance(value, str) and KEY_RE.fullmatch(value) is not None

class ClientTokens:
    def __init__(self, data_dir: Path):
        self.path = data_dir / "direct-gen/studio/clients.json"
        self._lock = threading.Lock()

    def _load(self) -> dict:
        if not self.path.exists():
            return {"clients": {}}
        data = json.loads(self.path.read_text(encoding="utf-8"))  # JSON errors propagate
        if not isinstance(data, dict) or not isinstance(data.get("clients"), dict):
            raise ValueError("invalid client token store")
        return data

    def _save(self, data: dict) -> None:
        self.path.parent.mkdir(parents=True, exist_ok=True)
        fd, tmp = tempfile.mkstemp(dir=self.path.parent, prefix=".clients-", text=True)
        try:
            with os.fdopen(fd, "w", encoding="utf-8") as f:
                json.dump(data, f, separators=(",", ":"))
                f.flush()
                os.fsync(f.fileno())
            os.replace(tmp, self.path)
        finally:
            Path(tmp).unlink(missing_ok=True)

    @staticmethod
    def _public(client: str, entry: dict) -> dict:
        fields = ("label", "issued_at", "expires_at", "revoked_at")
        return {"client": client, **{k: entry[k] for k in fields}}

    def issue(self, client: str | None, label: str = "",
              ttl_days: int | None = None) -> dict:
        if client is None:
            client = secrets.token_hex(32)
        if not _valid_key(client) or client == "owner":
            raise ValueError("client must be a 64-hex key")
        if ttl_days is not None and (type(ttl_days) is not int or not 1 <= ttl_days <= 365):
            raise ValueError("ttl_days must be between 1 and 365")
        now = datetime.now(UTC)
        expires = now + timedelta(days=ttl_days) if ttl_days is not None else None
        raw = "stc_" + secrets.token_urlsafe(32)
        entry = {
            "token_sha256": hashlib.sha256(raw.encode()).hexdigest(),
            "label": label,
            "issued_at": now.isoformat(),
            "expires_at": expires.isoformat() if expires else None,
            "revoked_at": None,
        }
        with self._lock:
            data = self._load()
            data["clients"][client] = entry  # Reissue replaces the old hash.
            self._save(data)
        return self._public(client, entry) | {"token": raw}

    def revoke(self, client: str) -> dict:
        with self._lock:
            data = self._load()
            entry = data["clients"].get(client)
            if entry is None:
                raise HTTPException(404, "client_not_found")
            entry["revoked_at"] = datetime.now(UTC).isoformat()
            self._save(data)
            return self._public(client, entry)

    def list(self) -> dict:
        with self._lock:
            clients = self._load()["clients"]
        return {"clients": [self._public(k, v) for k, v in clients.items()]}

    def resolve(self, token: str) -> str | None:
        if (not isinstance(token, str) or len(token) > 200 or len(token) <= 4
                or not token.startswith("stc_")):
            return None
        digest = hashlib.sha256(token.encode()).hexdigest()
        with self._lock:
            clients = self._load()["clients"]
        found = None
        for key, entry in clients.items():  # No early exit on a match.
            stored = entry.get("token_sha256", "")
            if not isinstance(stored, str) or not stored.isascii():
                stored = ""
            if hmac.compare_digest(digest, stored):
                found = key
        if found is None:
            return None
        entry = clients[found]
        if entry.get("revoked_at") is not None:
            return None
        expires = entry.get("expires_at")
        if expires is not None:
            try:
                end = datetime.fromisoformat(expires)
            except (TypeError, ValueError):
                return None
            if end.tzinfo is None or end <= datetime.now(UTC):
                return None
        return found

# x_participant is the decoded X-Participant header; the shared store is
# request.app.state.client_tokens.
async def identity(request: Request, x_participant: str | None = None,
                   client_query: str | None = None) -> tuple[str, str]:
    token = request.headers.get("X-Studio-Client-Token")
    if token is not None:
        key = await asyncio.to_thread(request.app.state.client_tokens.resolve, token)
        if key is None:
            raise HTTPException(401, "invalid_client_token")
        if x_participant is not None and x_participant != key:
            raise HTTPException(403, "participant_mismatch")
        if client_query is not None:
            raise HTTPException(403, "owner_only")
        return key, key
    participant = x_participant if x_participant is not None else "owner"
    if participant != "owner":
        raise HTTPException(403, "client_token_required")
    principal = getattr(request.state, "principal", None)
    if principal is None:
        raise HTTPException(401, "authentication_required")
    if getattr(principal, "role", None) != "owner":
        raise HTTPException(403, "owner_credential_required")
    if client_query is not None:
        if not _valid_key(client_query):
            raise HTTPException(400, "invalid_client_key")
        return "owner", client_query
    return "owner", "owner"
```

1. **Treating identity headers as credentials:** test that `X-Participant` alone returns 403 `client_token_required`, and an agent principal attempting owner access returns 403.
2. **Accepting stale credentials:** issue token A, reissue token B, then assert A no longer resolves; after revocation, assert B also fails and token authentication returns 401.
3. **Disclosing reusable credentials:** inspect `clients.json` and assert the raw token is absent; assert `list()` and `revoke()` expose neither raw tokens nor `token_sha256`.