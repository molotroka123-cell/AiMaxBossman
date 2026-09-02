"""Bootstrap for the repo-root `bossman_shared` package (shared numeric contracts).
When bossman-core is installed without the repository root, the import fails and
callers must degrade (no observation recorded) — never crash a request."""
from __future__ import annotations

import sys
from pathlib import Path

_ROOT = Path(__file__).resolve().parents[2]
# Installed distribution first (bossman-shared wheel / container); the repository
# checkout path is only a development fallback.
try:
    import bossman_shared as _installed  # noqa: F401
except Exception:  # noqa: BLE001
    if str(_ROOT) not in sys.path and (_ROOT / "bossman_shared").is_dir():
        sys.path.insert(0, str(_ROOT))
try:
    from bossman_shared import cache_observation as cache_observation  # noqa: F401
    AVAILABLE = True
except Exception:  # noqa: BLE001
    cache_observation = None  # type: ignore[assignment]
    AVAILABLE = False
