"""V2.6 модуль P — Evidence Graph: улики поверх разнородных файлов.

Без Postgres: граф чисто in-proc. Сценарий — тот самый «сравни цифры в
таблице с пунктами договора»: csv с цифрами + docx с пунктами, запросы
находят правильные секции по ключевым словам, claims/unsupported_claims
дают карту «утверждение → улики».
"""
from __future__ import annotations

import zipfile

from bossman.evidence_graph import EvidenceGraph, EvidenceRef
from bossman.file_intel import parse_file

W_NS = "http://schemas.openxmlformats.org/wordprocessingml/2006/main"


def _make_files(tmp_path):
    csv_p = tmp_path / "figures.csv"
    csv_p.write_text("статья,сумма\nаренда,1200\nэлектричество,300\n",
                     encoding="utf-8")
    doc = f"""<?xml version="1.0" encoding="UTF-8"?>
<w:document xmlns:w="{W_NS}"><w:body>
 <w:p><w:r><w:t>Пункт 4.2: арендная плата составляет 1200 в месяц</w:t></w:r></w:p>
 <w:p><w:r><w:t>Пункт 7.1: расторжение договора за 30 дней</w:t></w:r></w:p>
</w:body></w:document>"""
    docx_p = tmp_path / "contract.docx"
    with zipfile.ZipFile(docx_p, "w") as zf:
        zf.writestr("word/document.xml", doc)
    return csv_p, docx_p


def test_query_finds_right_ref_in_each_artifact(tmp_path):
    csv_p, docx_p = _make_files(tmp_path)
    g = EvidenceGraph()
    assert g.add_artifact(parse_file(csv_p)) >= 1
    assert g.add_artifact(parse_file(docx_p)) >= 1

    # цифры — в таблице csv (поиск по тексту ячеек)
    hits = g.query("сумма аренда электричество")
    assert hits and hits[0].file.endswith("figures.csv")
    assert hits[0].ref.startswith("rows=")

    # пункт договора — в docx-абзаце
    hits = g.query("расторжение договора")
    assert hits and hits[0].file.endswith("contract.docx")
    assert hits[0].ref.startswith("paragraph=")
    assert "расторжение" in hits[0].excerpt

    # запрос с общим термином видит улики из ОБОИХ файлов
    files = {h.file for h in g.query("аренда 1200", limit=5)}
    assert any(f.endswith("figures.csv") for f in files)
    assert any(f.endswith("contract.docx") for f in files)


def test_query_deterministic_and_limited(tmp_path):
    csv_p, docx_p = _make_files(tmp_path)
    g = EvidenceGraph()
    g.add_artifact(parse_file(csv_p))
    g.add_artifact(parse_file(docx_p))
    a = g.query("аренда", limit=1)
    b = g.query("аренда", limit=1)
    assert a == b and len(a) == 1
    assert g.query("нет_такого_слова_вообще") == []


def test_support_and_unsupported_claims(tmp_path):
    csv_p, docx_p = _make_files(tmp_path)
    g = EvidenceGraph()
    g.add_artifact(parse_file(csv_p))
    g.add_artifact(parse_file(docx_p))

    refs = g.query("арендная плата 1200")
    g.support("цифра аренды в таблице совпадает с пунктом 4.2", refs)
    g.support("страховка не упомянута ни в одном документе", [])

    claims = g.claims()
    assert len(claims) == 2
    assert claims[0][0].startswith("цифра аренды")
    assert all(isinstance(r, EvidenceRef) for r in claims[0][1])
    assert claims[0][1], "подкреплённое утверждение должно нести улики"
    assert g.unsupported_claims() == ["страховка не упомянута ни в одном документе"]
