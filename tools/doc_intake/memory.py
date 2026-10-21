"""Library notes -> the Bossman memory vault, then the real memory index.

Contract used (command-center/bcc/features/tools_memory.py):
  GET  /api/memory/config  -> {"configured", "root", "write_folder", ...}
  POST /api/memory/index   -> incremental re-index of the configured vault
Headers: ``X-BCC-Token: <data dir>/token`` and ``X-Participant: owner``.

``POST /api/memory/write`` is NOT used: it writes one flat ``<date>-<slug>.md``
per call into the write folder and cannot place notes under
``BOSSMAN Memory/Library/<category>/<slug>/``. Notes are written straight into
the vault the backend itself reports, through the same secret filter
(``bcc.hybrid.context_filter.scrub_for_memory``) and the same atomic-write
discipline as ``ObsidianVault.write_memory``; then the index is triggered
through the API. Backend down => notes are still written, ``indexed: false``,
and ``reindex`` finishes later.

Only a request body of ``{}`` ever leaves this process, only to a loopback
address: document text never goes to any network service.
"""
from __future__ import annotations

import importlib.util
import ipaddress
import json
import os
import sys
import urllib.error
import urllib.parse
import urllib.request
from dataclasses import dataclass
from pathlib import Path

from .config import (DEFAULT_BACKEND, DEFAULT_WRITE_FOLDER, ENV_VAULT, REPO_ROOT, VAULT_LIBRARY_FOLDER,
                     default_data_dir, ensure_repo_imports)
from .fsutil import atomic_write_text
from .notes import Note, add_front_matter_line


class MemoryUnavailable(RuntimeError):
    pass


@dataclass(frozen=True)
class VaultTarget:
    root: Path
    write_folder: str
    source: str            # flag | env | api | cache

    @property
    def library_base(self) -> Path:
        return (self.root / self.write_folder / VAULT_LIBRARY_FOLDER).resolve()


def _loopback(url: str) -> bool:
    host = (urllib.parse.urlsplit(url).hostname or "").strip("[]")
    if host == "localhost":
        return True
    try:
        return ipaddress.ip_address(host).is_loopback
    except ValueError:
        return False


_FILTER_FILE = REPO_ROOT / "command-center" / "bcc" / "hybrid" / "context_filter.py"


def _scrubber():
    """The memory secret filter used by ObsidianVault.write_memory (same code, same rules).

    ``bcc.hybrid`` as a package imports SQLAlchemy and half the hybrid runtime
    (~0.8 s); ``context_filter.py`` itself is stdlib-only, so it is loaded from
    its file. Fails closed: no filter -> ImportError -> notes are NOT written.
    """
    for name in ("bcc.hybrid.context_filter", "doc_intake_context_filter"):
        loaded = sys.modules.get(name)
        if loaded is not None and hasattr(loaded, "scrub_for_memory"):
            return loaded.scrub_for_memory, loaded.SecretMaterialRefused
    try:
        spec = importlib.util.spec_from_file_location("doc_intake_context_filter", _FILTER_FILE)
        module = importlib.util.module_from_spec(spec)
        sys.modules[spec.name] = module          # dataclasses resolves the module by name
        spec.loader.exec_module(module)
        return module.scrub_for_memory, module.SecretMaterialRefused
    except (OSError, ImportError, AttributeError, SyntaxError):
        sys.modules.pop("doc_intake_context_filter", None)
    ensure_repo_imports()
    from bcc.hybrid.context_filter import SecretMaterialRefused, scrub_for_memory
    return scrub_for_memory, SecretMaterialRefused


class MemorySink:
    def __init__(self, *, enabled: bool = True, vault_root: str | Path | None = None,
                 write_folder: str | None = None, backend: str = DEFAULT_BACKEND,
                 data_dir: str | Path | None = None, token_file: str | Path | None = None,
                 timeout: float = 10.0, index_timeout: float = 600.0):
        if not _loopback(backend):
            raise ValueError("backend must be a loopback address: the owner token never leaves this PC")
        self.enabled = enabled
        self.vault_root = Path(vault_root).expanduser() if vault_root else None
        self.write_folder = write_folder
        self.backend = backend.rstrip("/")
        self.data_dir = Path(data_dir) if data_dir else default_data_dir()
        self.token_file = Path(token_file) if token_file else self.data_dir / "token"
        self.timeout = timeout
        self.index_timeout = index_timeout
        self._opener = urllib.request.build_opener(urllib.request.ProxyHandler({}))   # never via a proxy

    # ------------------------------------------------------------------ HTTP
    def _request(self, method: str, path: str, *, timeout: float, body: dict | None = None) -> dict:
        try:
            token = self.token_file.read_text(encoding="utf-8").strip()
        except OSError as exc:
            raise MemoryUnavailable(f"нет файла токена {self.token_file} ({type(exc).__name__})") from None
        data = json.dumps(body).encode() if body is not None else None
        request = urllib.request.Request(self.backend + path, data=data, method=method, headers={
            "X-BCC-Token": token, "X-Participant": "owner", "Content-Type": "application/json"})
        try:
            with self._opener.open(request, timeout=timeout) as response:
                return json.loads(response.read() or b"{}")
        except urllib.error.HTTPError as exc:
            try:
                detail = json.loads(exc.read() or b"{}")
            except ValueError:
                detail = {}
            message = detail.get("detail", detail) if isinstance(detail, dict) else detail
            raise MemoryUnavailable(f"backend ответил {exc.code}: {str(message)[:200]}") from None
        except (urllib.error.URLError, OSError, ValueError) as exc:
            reason = getattr(exc, "reason", exc)
            raise MemoryUnavailable(f"backend {self.backend} недоступен: {reason}") from None

    def api_config(self) -> dict:
        cfg = self._request("GET", "/api/memory/config", timeout=self.timeout)
        if not cfg.get("configured") or not cfg.get("root"):
            raise MemoryUnavailable("память в Bossman не настроена (POST /api/memory/config)")
        return cfg

    # ------------------------------------------------------------------ vault
    def resolve(self, cached: dict | None = None) -> VaultTarget:
        folder = self.write_folder
        if self.vault_root:
            return self._target(self.vault_root, folder or (cached or {}).get("write_folder"), "flag")
        env_root = (os.environ.get(ENV_VAULT) or "").strip()
        if env_root:
            return self._target(Path(env_root), folder, "env")
        try:
            cfg = self.api_config()
            return self._target(Path(cfg["root"]), folder or cfg.get("write_folder"), "api")
        except MemoryUnavailable as exc:
            if cached and cached.get("vault_root"):
                return self._target(Path(cached["vault_root"]), folder or cached.get("write_folder"), "cache")
            raise MemoryUnavailable(f"vault памяти неизвестен: {exc}") from None

    @staticmethod
    def _target(root: Path, folder: str | None, source: str) -> VaultTarget:
        root = Path(root).expanduser().resolve()
        if not root.is_dir():
            raise MemoryUnavailable(f"vault не найден: {root}")
        folder = folder or DEFAULT_WRITE_FOLDER
        base = (root / folder).resolve()
        if root != base and root not in base.parents:
            raise MemoryUnavailable("папка записи выходит за пределы vault")
        return VaultTarget(root, folder, source)

    def _inside(self, target: VaultTarget, path: Path) -> Path:
        resolved = path.resolve()
        base = target.library_base
        if resolved != base and base not in resolved.parents:
            raise PermissionError(f"запись вне {target.write_folder}/{VAULT_LIBRARY_FOLDER}: {path}")
        return resolved

    def write_notes(self, target: VaultTarget, rel_dir: str, notes: list[Note], *,
                    source_name: str) -> tuple[list[str], list[str], list[str]]:
        """Write notes into <write folder>/Library/<rel_dir>/. Returns (vault-relative paths,
        skipped filenames, redaction kinds). Secrets are redacted or the note is skipped."""
        scrub, refused = _scrubber()
        dest_dir = self._inside(target, target.library_base / rel_dir)
        dest_dir.mkdir(parents=True, exist_ok=True)
        written, skipped, kinds = [], [], set()
        for note in notes:
            try:
                cleaned = scrub(note.text, source=source_name)
            except refused:
                skipped.append(note.filename)
                continue
            text = cleaned.body
            if cleaned.redactions:
                kinds.update(cleaned.redactions)
                text = add_front_matter_line(text, "redactions: [" + ", ".join(cleaned.redactions) + "]")
            dest = self._inside(target, dest_dir / note.filename)
            atomic_write_text(dest, text)
            written.append(dest.relative_to(target.root).as_posix())
        return written, skipped, sorted(kinds)

    def remove(self, target: VaultTarget, rel_paths: list[str]) -> int:
        """Delete notes this tool wrote earlier (and only inside Library/); prune empty folders."""
        removed = 0
        for rel in rel_paths:
            try:
                path = self._inside(target, target.root / rel)
            except PermissionError:
                continue
            if path.suffix == ".md" and path.is_file():
                path.unlink()
                removed += 1
                parent = path.parent
                while parent != target.library_base and target.library_base in parent.parents:
                    try:
                        parent.rmdir()
                    except OSError:
                        break
                    parent = parent.parent
        return removed

    # ------------------------------------------------------------------ index
    def index(self, target: VaultTarget) -> tuple[bool, str]:
        """Trigger the live memory index. (True, summary) or (False, reason)."""
        try:
            cfg = self.api_config()
            api_root = Path(cfg["root"]).expanduser().resolve()
            if api_root != target.root:
                return False, f"память Bossman индексирует {api_root}, а заметки записаны в {target.root}"
            result = self._request("POST", "/api/memory/index", timeout=self.index_timeout, body={})
        except MemoryUnavailable as exc:
            return False, str(exc)
        summary = result.get("result", result)
        return True, json.dumps(summary, ensure_ascii=False)[:300]
