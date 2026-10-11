"""Vibe Studio: ask in plain language, get a small static web app, previewed in a sandbox.

Pattern adopted from caffeinum/vibeOS (MIT, audited in docs/owner/VIBE_OS_AUDIT_20261011.md):
natural-language request -> agent writes a small app -> every step visible -> owner decides.
No VM, no browser-in-WASM; generation goes through Bossman's own provider governance.
"""
from .service import VibeError, VibeService  # noqa: F401
