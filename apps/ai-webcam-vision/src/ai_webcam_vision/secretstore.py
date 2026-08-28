"""Secret handling: opaque values, one URL assembly point, one scrubber.

Rules enforced here (they are the whole point of this module):

* a ``Secret`` never reveals itself through ``repr``/``str``/``format``;
* every secret value created in this process is registered, so the scrubber
  can remove it from arbitrary text (log lines, ffmpeg stderr, tracebacks),
  not only from text that happens to look like a URL;
* RTSP/HTTP URLs carrying credentials are assembled in exactly one place.
"""

from __future__ import annotations

import re
import threading
from dataclasses import dataclass
from urllib.parse import quote

__all__ = [
    "Secret",
    "SecretUrl",
    "register_secret_value",
    "scrub",
    "build_stream_url",
    "MASK",
]

MASK = "***"

# Values shorter than this are not registered for literal scrubbing: masking a
# 2-3 character string would corrupt unrelated text and give false comfort.
MIN_REGISTERED_LENGTH = 4

_lock = threading.Lock()
_registered: set[str] = set()

# userinfo of any URL scheme, including the empty-username form "://:pass@host"
_URL_USERINFO = re.compile(r"(?i)\b([a-z][a-z0-9+.\-]*://)([^/\s@]*)@")


def register_secret_value(value: str) -> None:
    """Register a literal that must never appear in emitted text."""
    if not value or len(value) < MIN_REGISTERED_LENGTH:
        return
    variants = {value, quote(value, safe="")}
    with _lock:
        _registered.update(v for v in variants if len(v) >= MIN_REGISTERED_LENGTH)


def scrub(text: object) -> str:
    """Return ``text`` with URL credentials and known secret literals masked."""
    out = text if isinstance(text, str) else str(text)
    out = _URL_USERINFO.sub(lambda m: f"{m.group(1)}{MASK}:{MASK}@", out)
    with _lock:
        values = sorted(_registered, key=len, reverse=True)
    for value in values:
        if value in out:
            out = out.replace(value, MASK)
    return out


class Secret:
    """An opaque string. The value is only obtainable via :meth:`reveal`."""

    __slots__ = ("_value", "_label")

    def __init__(self, value: str | None, label: str = "secret") -> None:
        self._value = value or ""
        self._label = label
        register_secret_value(self._value)

    @property
    def label(self) -> str:
        return self._label

    def reveal(self) -> str:
        return self._value

    def __bool__(self) -> bool:
        return bool(self._value)

    def __repr__(self) -> str:
        return f"<Secret {self._label}={MASK}>"

    __str__ = __repr__

    def __format__(self, _spec: str) -> str:
        return repr(self)

    def __eq__(self, other: object) -> bool:
        if isinstance(other, Secret):
            return self._value == other._value
        return NotImplemented

    def __hash__(self) -> int:
        return hash((self.__class__, self._value))

    def __reduce__(self):  # pragma: no cover - defensive
        raise TypeError("Secret objects are not serialisable")


class SecretUrl(Secret):
    """A URL that may embed credentials. ``public`` is always safe to emit."""

    __slots__ = ("_public",)

    def __init__(self, url: str, public: str, label: str = "stream_url") -> None:
        super().__init__(url, label)
        self._public = public

    @property
    def public(self) -> str:
        """Credential-free rendering, safe for logs, API responses and errors."""
        return scrub(self._public)

    def __repr__(self) -> str:
        return f"<SecretUrl {self.public}>"

    __str__ = __repr__


@dataclass(frozen=True)
class StreamTarget:
    """Everything needed to address one video source, credentials excluded."""

    scheme: str
    host: str
    port: int | None
    path: str


def build_stream_url(
    target: StreamTarget,
    username: str = "",
    password: Secret | None = None,
) -> SecretUrl:
    """The single place in this application where a stream URL is assembled.

    Nothing else may concatenate credentials into a URL. Tests assert that.
    """
    if not target.host:
        raise ValueError("stream target requires a host")
    path = target.path if target.path.startswith("/") else f"/{target.path}"
    authority = target.host if target.port is None else f"{target.host}:{target.port}"

    secret_value = password.reveal() if password is not None else ""
    if username or secret_value:
        userinfo = f"{quote(username, safe='')}:{quote(secret_value, safe='')}@"
        public_userinfo = f"{MASK}:{MASK}@"
    else:
        userinfo = ""
        public_userinfo = ""

    url = f"{target.scheme}://{userinfo}{authority}{path}"
    public = f"{target.scheme}://{public_userinfo}{authority}{path}"
    return SecretUrl(url, public)
