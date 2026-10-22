"""Safe XML parsing wrapper around `defusedxml.ElementTree` (with an Expat-hardened
stdlib fallback for bare CI environments) enforcing strict size, depth, element-count,
and wall-clock time limits (P2.13).

Forbids DTDs, external entities (XXE), entity expansion (Billion Laughs), and deep nesting bombs.
"""
from __future__ import annotations

import time
import xml.etree.ElementTree as StdET

try:
    from defusedxml import DefusedXmlException
    from defusedxml import ElementTree as DefusedET
except ImportError:  # pragma: no cover - fallback for bare stdlib CI environments
    DefusedET = None  # type: ignore[assignment]

    class DefusedXmlException(ValueError):  # type: ignore[no-redef]
        """Raised when DTD, entity declaration, or external entity reference is encountered."""


DEFAULT_MAX_XML_BYTES = 8 * 1024 * 1024  # 8 MiB
DEFAULT_MAX_XML_DEPTH = 64
DEFAULT_MAX_XML_ELEMENTS = 100_000
DEFAULT_MAX_PARSE_SECONDS = 5.0

Element = StdET.Element
ParseError = (ValueError, StdET.ParseError, DefusedXmlException)


def _hardened_stdlib_fromstring(raw_bytes: bytes) -> StdET.Element:
    """Parse XML with stdlib Expat hooks that forbid DOCTYPE, ENTITY, and external references."""
    parser = StdET.XMLParser()
    expat = getattr(parser, "parser", None) or getattr(parser, "_parser", None)
    if expat is not None:
        def _forbid_dtd(*_args):
            raise DefusedXmlException("DOCTYPE declaration is forbidden")

        def _forbid_entity(*_args):
            raise DefusedXmlException("Entity declaration is forbidden")

        def _forbid_unparsed(*_args):
            raise DefusedXmlException("Unparsed entity declaration is forbidden")

        def _forbid_external(*_args):
            raise DefusedXmlException("External entity reference is forbidden")

        expat.StartDoctypeDeclHandler = _forbid_dtd
        expat.EntityDeclHandler = _forbid_entity
        expat.UnparsedEntityDeclHandler = _forbid_unparsed
        expat.ExternalEntityRefHandler = _forbid_external
    parser.feed(raw_bytes)
    return parser.close()


def _verify_tree_bounds(
    root: StdET.Element,
    *,
    max_depth: int,
    max_elements: int,
    deadline: float,
) -> None:
    count = 0
    stack: list[tuple[StdET.Element, int]] = [(root, 1)]
    while stack:
        if time.monotonic() > deadline:
            raise ValueError("XML parse exceeded wall-clock time limit")
        elem, depth = stack.pop()
        if depth > max_depth:
            raise ValueError(f"XML depth {depth} exceeds max_depth={max_depth}")
        count += 1
        if count > max_elements:
            raise ValueError(f"XML element count {count} exceeds max_elements={max_elements}")
        for child in elem:
            stack.append((child, depth + 1))


def safe_fromstring(
    data: str | bytes,
    *,
    max_bytes: int = DEFAULT_MAX_XML_BYTES,
    max_depth: int = DEFAULT_MAX_XML_DEPTH,
    max_elements: int = DEFAULT_MAX_XML_ELEMENTS,
    max_seconds: float = DEFAULT_MAX_PARSE_SECONDS,
) -> StdET.Element:
    """Parse XML string/bytes safely via defusedxml (or Expat-hardened parser) with DTD/entity bans and resource caps."""
    raw_bytes = data.encode("utf-8", errors="replace") if isinstance(data, str) else bytes(data)
    if not raw_bytes:
        raise ValueError("Empty XML payload")
    if len(raw_bytes) > max_bytes:
        raise ValueError(f"XML size {len(raw_bytes)} bytes exceeds max_bytes={max_bytes}")
    deadline = time.monotonic() + max(0.1, float(max_seconds))
    try:
        if DefusedET is not None:
            root = DefusedET.fromstring(
                raw_bytes,
                forbid_dtd=True,
                forbid_entities=True,
                forbid_external=True,
            )
        else:
            root = _hardened_stdlib_fromstring(raw_bytes)
    except (DefusedXmlException, StdET.ParseError, SyntaxError) as exc:
        raise ValueError(f"Unsafe or malformed XML rejected: {exc}") from exc
    _verify_tree_bounds(root, max_depth=max_depth, max_elements=max_elements, deadline=deadline)
    return root


fromstring = safe_fromstring
