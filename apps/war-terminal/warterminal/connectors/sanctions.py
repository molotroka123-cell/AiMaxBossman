"""Official sanctions lists without keys: OFAC SDN (CSV) and UN Security Council consolidated list (XML).
Names (and aliases) only are kept for screening; each import records URL, SHA-256 and date. Screening is a hint for a human,
not a legal determination: an exact match BLOCKS, a partial match goes to REVIEW."""
from __future__ import annotations

import csv
import hashlib
import io
import xml.etree.ElementTree as ET

OFAC_URL = "https://www.treasury.gov/ofac/downloads/sdn.csv"
UN_URL = "https://scsanctions.un.org/resources/xml/en/consolidated.xml"


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
    root = ET.fromstring(data)
    out = []
    for kind, tag, alias_tag in (("individual", "INDIVIDUAL", "INDIVIDUAL_ALIAS"), ("entity", "ENTITY", "ENTITY_ALIAS")):
        for el in root.iter(tag):
            parts = [(el.findtext(t) or "").strip() for t in ("FIRST_NAME", "SECOND_NAME", "THIRD_NAME", "FOURTH_NAME")]
            name = " ".join(p for p in parts if p)
            ref = (el.findtext("REFERENCE_NUMBER") or "").strip()
            if name:
                out.append({"name": name, "type": kind, "program": "UN", "ref": ref, "source": "un_consolidated"})
            for al in el.iter(alias_tag):
                a = (al.findtext("ALIAS_NAME") or "").strip()
                if a:
                    out.append({"name": a, "type": kind, "program": "UN", "ref": ref, "source": "un_consolidated", "alias": True})
    return out


def digest(data: bytes) -> str:
    return hashlib.sha256(data).hexdigest()
