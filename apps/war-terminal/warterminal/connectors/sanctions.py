"""Official sanctions lists without keys: OFAC SDN (CSV) and UN Security Council consolidated list (XML).
Names (and aliases) only are kept for screening; each import records URL, SHA-256 and date. Screening is a hint for a human,
not a legal determination: an exact match BLOCKS, a partial match goes to REVIEW."""
from __future__ import annotations

import csv
import hashlib
import io
import time
import xml.etree.ElementTree as StdET

try:
    from defusedxml import DefusedXmlException
    from defusedxml import ElementTree as DefusedET
except ImportError:  # pragma: no cover - fallback for bare stdlib environments
    DefusedET = None  # type: ignore[assignment]

    class DefusedXmlException(ValueError):  # type: ignore[no-redef]
        pass

OFAC_URL = "https://www.treasury.gov/ofac/downloads/sdn.csv"
UN_URL = "https://scsanctions.un.org/resources/xml/en/consolidated.xml"
MAX_SANCTIONS_XML_BYTES = 32 * 1024 * 1024
MAX_SANCTIONS_XML_DEPTH = 48


def _hardened_fromstring(data: bytes) -> StdET.Element:
    if DefusedET is not None:
        return DefusedET.fromstring(data, forbid_dtd=True, forbid_entities=True, forbid_external=True)
    parser = StdET.XMLParser()
    expat = getattr(parser, "parser", None) or getattr(parser, "_parser", None)
    if expat is not None:
        def _deny(*_args):
            raise DefusedXmlException("DTD/entity forbidden in UN sanctions XML")

        expat.StartDoctypeDeclHandler = _deny
        expat.EntityDeclHandler = _deny
        expat.UnparsedEntityDeclHandler = _deny
        expat.ExternalEntityRefHandler = _deny
    parser.feed(data)
    return parser.close()


def _clean(v: str) -> str:
    v = (v or "").strip().strip('"')
    return "" if v in ("-0-", "-0- ") else v


def parse_ofac(data: bytes) -> list[dict]:
    """sdn.csv has no header: ent_num, SDN_Name, SDN_Type, Program, Title, ..., Remarks."""
    out = []
    for row in csv.reader(io.StringIO(data.decode("latin-1"))):
        if len(row) < 4 or not _clean(row[1]):
            continue
        out.append({"name": _clean(row[1]), "type": _clean(row[2]) or "entity", "program": _clean(row[3]), "ref": _clean(row[0]), "source": "ofac_sdn"})
    return out


def parse_un(data: bytes) -> list[dict]:
    if len(data) > MAX_SANCTIONS_XML_BYTES:
        raise ValueError(f"UN XML exceeds size limit ({len(data)} > {MAX_SANCTIONS_XML_BYTES})")
    deadline = time.monotonic() + 10.0
    try:
        root = _hardened_fromstring(data)
    except Exception as exc:  # noqa: BLE001
        raise ValueError(f"Unsafe or malformed UN XML rejected: {exc}") from exc
    stack = [(root, 1)]
    while stack:
        if time.monotonic() > deadline:
            raise ValueError("UN XML parse timed out")
        el_chk, depth = stack.pop()
        if depth > MAX_SANCTIONS_XML_DEPTH:
            raise ValueError(f"UN XML exceeds max depth {MAX_SANCTIONS_XML_DEPTH}")
        for ch in el_chk:
            stack.append((ch, depth + 1))
    out = []

    for kind, tag, alias_tag in (("individual", "INDIVIDUAL", "INDIVIDUAL_ALIAS"), ("entity", "ENTITY", "ENTITY_ALIAS")):
        for el in root.iter(tag):
            parts = [(el.findtext(t) or "").strip() for t in ("FIRST_NAME", "SECOND_NAME", "THIRD_NAME", "FOURTH_NAME")]
            name = " ".join(p for p in parts if p)
            ref = (el.findtext("REFERENCE_NUMBER") or "").strip()
            if name:
                out.append({"name": name, "type": kind, "program": "UN", "ref": ref, "source": "un_consolidated"})
            for al in el.iter(alias_tag):
                an = (al.findtext("ALIAS_NAME") or "").strip()
                if an and an != name:
                    out.append({"name": an, "type": kind, "program": "UN", "ref": ref, "source": "un_consolidated", "alias_of": name})
    return out


def snapshot_hash(data: bytes) -> str:
    return hashlib.sha256(data).hexdigest()
