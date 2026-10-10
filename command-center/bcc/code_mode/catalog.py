"""Search index + typed-SDK stub renderer for the code-mode tool facade.

Builds a BM25 index over ToolSpec-like objects (duck-typed) and renders
Python stubs the model can paste into sandboxed code.
"""
from __future__ import annotations

import keyword
import math
import re
from collections import Counter
from dataclasses import dataclass
from typing import Any, Iterable, Mapping

_K1 = 1.5
_B = 0.4
_NAME_BONUS = 2.0   # extra weight (x idf) when a query word is a word of the tool NAME itself
_SPLIT_CAMEL = re.compile(
    r"[A-Z]+(?=[A-Z][a-z])|[A-Z]?[a-z]+|[A-Z]+|\d+|[^\W_]+"
)
_WORD = re.compile(r"[^\W_]+")


@dataclass(frozen=True)
class CatalogEntry:
    py_name: str
    spec: Any
    effect: str = "auto"


def py_identifier(api_name: str) -> str:
    out = re.sub(r"[^0-9A-Za-z_]", "_", api_name)
    if not out or out[0].isdigit():
        out = "t_" + out
    return out


def _split_camel(text: str) -> list[str]:
    parts: list[str] = []
    for m in _SPLIT_CAMEL.finditer(text):
        parts.append(m.group(0))
    return parts if parts else [text]


_STOP = frozenset((
    "the an of to in on at for and or with from by is it my me i you we this that these those "
    "do does can could would should please use using via into as be are was not no "
    "и в во на с со по для из от как что это к у о об не за").split())


def _tokenize(text: str, *, stems: bool = True) -> list[str]:
    """Words plus ``~xxxx`` 4-char stem terms (kept apart from exact words so
    that short exact matches such as "open" are not diluted by "openclaw")."""
    if not text:
        return []
    tokens: list[str] = []
    for raw in _split_camel(text):
        for tok in _WORD.findall(raw.lower()):
            if len(tok) <= 1 or tok in _STOP:
                continue
            tokens.append(tok)
            if stems and len(tok) >= 5:
                tokens.append("~" + tok[:4])
    return tokens


def _collapse(text: str, limit: int = 160) -> str:
    one = " ".join((text or "").split())
    if len(one) > limit:
        one = one[: limit - 1].rstrip() + "…"
    return one


_TYPE_MAP = {
    "string": "str",
    "integer": "int",
    "number": "float",
    "boolean": "bool",
    "array": "list",
    "object": "dict",
}


def _py_type(prop: Mapping[str, Any]) -> str:
    t = prop.get("type")
    if isinstance(t, list):
        for item in t:
            if item in _TYPE_MAP:
                return _TYPE_MAP[item]
        return "Any"
    return _TYPE_MAP.get(t, "Any")


class ToolCatalog:
    def __init__(
        self,
        specs: Iterable[Any],
        *,
        effects: Mapping[str, str] | None = None,
    ) -> None:
        eff = effects or {}
        kept = [s for s in specs if eff.get(s.name, "auto") != "deny"]
        kept.sort(key=lambda s: s.name)
        self.entries: list[CatalogEntry] = []
        used: set[str] = set()
        for spec in kept:
            base = py_identifier(spec.api_name)
            py_name = base
            n = 1
            while py_name in used:
                n += 1
                py_name = f"{base}_{n}"
            used.add(py_name)
            self.entries.append(
                CatalogEntry(py_name=py_name, spec=spec, effect=eff.get(spec.name, "auto"))
            )
        # Precompute BM25.
        self._docs: list[Counter[str]] = []
        self._doc_names: list[str] = []
        self._name_terms: list[frozenset[str]] = []
        for e in self.entries:
            s = e.spec
            bag: list[str] = []
            name_toks = _tokenize(s.name)
            bag.extend(name_toks * 3)
            bag.extend(_tokenize(e.py_name) * 2)
            bag.extend(_tokenize(getattr(s, "description", "") or ""))
            props = getattr(s, "input_schema", None) or {}
            for pname, prop in props.items():
                bag.extend(_tokenize(pname))
                if isinstance(prop, Mapping):
                    bag.extend(_tokenize(prop.get("description") or ""))
            self._docs.append(Counter(bag))
            self._doc_names.append(s.name)
            self._name_terms.append(frozenset(_tokenize(s.name, stems=False)))
        self._lengths = [sum(c.values()) for c in self._docs]
        self._avgdl = (sum(self._lengths) / len(self._lengths)) if self._lengths else 0.0
        df: Counter[str] = Counter()
        for c in self._docs:
            df.update(c.keys())
        self._df = df
        n_docs = len(self._docs)
        self._idf = {
            term: math.log(1.0 + (n_docs - d + 0.5) / (d + 0.5))
            for term, d in df.items()
        }

    def __len__(self) -> int:
        return len(self.entries)

    def py_names(self) -> dict[str, str]:
        return {e.py_name: e.spec.api_name for e in self.entries}

    def entry_for_py(self, py_name: str) -> CatalogEntry | None:
        for e in self.entries:
            if e.py_name == py_name:
                return e
        return None

    def search(self, query: str, k: int = 5) -> list[CatalogEntry]:
        k = max(1, min(10, k))
        q_tokens = _tokenize(query)
        if not q_tokens or not self._docs:
            return []
        scores: list[tuple[float, str, int]] = []
        for i, (doc, length) in enumerate(zip(self._docs, self._lengths)):
            dl = length or 1
            score = 0.0
            for term in q_tokens:
                tf = doc.get(term, 0)
                if not tf:
                    continue
                idf = self._idf.get(term, 0.0)
                score += idf * (tf * (_K1 + 1.0)) / (tf + _K1 * (1.0 - _B + _B * dl / (self._avgdl or 1.0)))
            for term in set(q_tokens) & self._name_terms[i]:
                score += _NAME_BONUS * self._idf.get(term, 0.0)
            if score > 0.0:
                scores.append((score, self._doc_names[i], i))
        scores.sort(key=lambda t: (-t[0], t[1]))
        return [self.entries[i] for _, _, i in scores[:k]]

    def families(self) -> list[tuple[str, int]]:
        counts: Counter[str] = Counter()
        for e in self.entries:
            name = e.spec.name
            sep = min(
                (i for i in (name.find("."), name.find(":")) if i != -1),
                default=-1,
            )
            prefix = name[:sep] if sep != -1 else name
            counts[prefix] += 1
        return sorted(counts.items(), key=lambda kv: (-kv[1], kv[0]))

    def stub(self, entry: CatalogEntry) -> str:
        spec = entry.spec
        props = getattr(spec, "input_schema", None) or {}
        required = list(getattr(spec, "required", None) or [])
        params: list[str] = []
        doc_extras: list[str] = []
        seen: set[str] = set()

        def handle(name: str, prop: Any, is_required: bool) -> None:
            if name in seen:
                return
            seen.add(name)
            if not name.isidentifier() or keyword.iskeyword(name):
                doc_extras.append(f'"{name}": ...')
                return
            if is_required:
                params.append(f"{name}: {_py_type(prop)}")
            else:
                params.append(f"{name}: {_py_type(prop)} | None = None")

        for name in required:
            handle(name, props.get(name, {}), True)
        for name, prop in props.items():
            if name not in seen:
                handle(name, prop, False)

        sig = f"def {entry.py_name}({', '.join(params)}) -> dict:"
        doc = _collapse(getattr(spec, "description", "") or "").replace('"""', "'''")
        if entry.effect == "ask":
            doc = (doc + " [needs owner approval]").strip()
        if doc_extras:
            doc = (doc + " kwargs: {" + ", ".join(doc_extras) + "}").strip()
        return f'{sig}\n    """{doc}"""'

    def render_search(self, query: str, k: int = 5) -> str:
        results = self.search(query, k) if query.strip() else []
        if not results:
            fams = ", ".join(f"{name}({n})" for name, n in self.families()[:25])
            return (
                f'no tools match "{query}" (of {len(self)} tools). '
                f"Tool families: {fams}. Try other words."
            )
        header = (
            f'{len(results)} match(es) for "{query}" (of {len(self)} tools). '
            f'Call as tools.<name>(...) inside bossman_run; every call returns '
            f'{{"ok": bool, "content": str}}.'
        )
        body = "\n\n".join(self.stub(e) for e in results)
        return f"{header}\n\n{body}"


def estimate_tokens(text: str) -> int:
    return max(1, round(len(text) / 4))
