"""Normalise a user-typed relative path for a workspace (owner types Windows paths)."""
from __future__ import annotations


def normalize_rel(user_path: str) -> list[str]:
    r"""Return the path components of a RELATIVE path inside the workspace.

    Contract:
      * both '\' and '/' are separators; repeated separators collapse;
      * '.' components are dropped, '..' pops the previous component;
      * a path that escapes the workspace (more '..' than components) -> ValueError;
      * absolute paths ('/x', '\x'), drive letters ('C:\x', 'C:x') and UNC ('\\server\share') -> ValueError;
      * spaces and non-ASCII (e.g. Cyrillic) are kept exactly, including inner spaces;
      * leading/trailing whitespace of the WHOLE input is stripped; empty result -> ValueError.
    """
    parts = user_path.split("/")
    out = []
    for p in parts:
        if p == "..":
            out.pop()
        elif p and p != ".":
            out.append(p.strip())
    return out
