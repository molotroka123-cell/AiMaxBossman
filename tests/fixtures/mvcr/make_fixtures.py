"""SYNTHETIC HW-10 fixtures: a fake official site, a lookalike and tiny AcroForm PDFs.

Nothing here is a copy of a real MVČR page or form and nothing here is real
personal data. The PDFs are written byte by byte with the stdlib at test time,
so the repository never carries binary government documents.

    python tests/fixtures/mvcr/make_fixtures.py <dest> [--variant happy]

creates ``<dest>/fixtures`` (usable as ``--offline-fixtures``) and
``<dest>/owner`` (a synthetic owner folder usable as ``--owner-folder``).
"""
from __future__ import annotations

import argparse
import shutil
from pathlib import Path

HERE = Path(__file__).resolve().parent

VARIANTS = ("happy", "conflicting_fee", "stale_only", "two_versions", "html_as_pdf")

#: (field name, tooltip, label on the page). ASCII only: the base-14 font has no č/ř.
FORM_FIELDS = (
    ("prijmeni", "Prijmeni / Surname", "Prijmeni"),
    ("jmeno", "Jmeno / Given names", "Jmeno"),
    ("datum_narozeni", "Datum narozeni / Date of birth", "Datum narozeni"),
    ("statni_obcanstvi", "Statni obcanstvi / Nationality", "Statni obcanstvi"),
    ("cislo_pasu", "Cislo cestovniho dokladu / Passport number", "Cislo cestovniho dokladu"),
    ("adresa_pobytu", "Adresa pobytu na uzemi CR / Address", "Adresa pobytu"),
    ("pobyt_na_uzemi_od", "Nepretrzity pobyt na uzemi od / Continuous residence since",
     "Pobyt na uzemi od"),
    ("podpis", "Podpis zadatele / Signature", "Podpis"),
    ("datum_podpisu", "Datum podpisu / Date of signature", "Datum"),
)


def _esc(text: str) -> str:
    return text.replace("\\", "\\\\").replace("(", "\\(").replace(")", "\\)")


def _pdf(objects: list[bytes]) -> bytes:
    """Serialise numbered objects (1-based) with a correct xref table."""
    out = bytearray(b"%PDF-1.7\n%\xe2\xe3\xcf\xd3\n")
    offsets = []
    for number, body in enumerate(objects, start=1):
        offsets.append(len(out))
        out += f"{number} 0 obj\n".encode() + body + b"\nendobj\n"
    xref = len(out)
    out += f"xref\n0 {len(objects) + 1}\n0000000000 65535 f \n".encode()
    for off in offsets:
        out += f"{off:010d} 00000 n \n".encode()
    info = len(objects)          # the last object is always /Info
    out += (f"trailer\n<< /Size {len(objects) + 1} /Root 1 0 R /Info {info} 0 R >>\n"
            f"startxref\n{xref}\n%%EOF\n").encode()
    return bytes(out)


def _stream(lines: list[str], placed: tuple[tuple[int, str], ...] = ()) -> bytes:
    """Header lines from the top, then labels placed at an exact y (next to their field)."""
    ops = ["BT", "/F1 11 Tf", "50 800 Td", "14 TL"]
    for line in lines:
        ops.append(f"({_esc(line)}) Tj T*")
    ops.append("ET")
    for y, label in placed:
        ops.append(f"BT /F1 11 Tf 50 {y} Td ({_esc(label)}) Tj ET")
    data = "\n".join(ops).encode("latin-1")
    return b"<< /Length %d >>\nstream\n" % len(data) + data + b"\nendstream"


def form_pdf(version: str = "01.01.2026", *, fields=FORM_FIELDS) -> bytes:
    """A one-page synthetic 'Zadost o povoleni k trvalemu pobytu' with text fields."""
    n_fields = len(fields)
    first_field = 5                       # 1 catalog, 2 pages, 3 page, 4 font
    content_no = first_field + n_fields
    info_no = content_no + 1
    refs = " ".join(f"{first_field + i} 0 R" for i in range(n_fields))
    objects: list[bytes] = [
        (f"<< /Type /Catalog /Pages 2 0 R /AcroForm << /Fields [{refs}] "
         "/DA (/Helv 0 Tf 0 g) /DR << /Font << /Helv 4 0 R >> >> >> >>").encode(),
        b"<< /Type /Pages /Kids [3 0 R] /Count 1 >>",
        (f"<< /Type /Page /Parent 2 0 R /MediaBox [0 0 595 842] "
         f"/Resources << /Font << /F1 4 0 R >> >> /Contents {content_no} 0 R "
         f"/Annots [{refs}] >>").encode(),
        b"<< /Type /Font /Subtype /Type1 /BaseFont /Helvetica /Encoding /WinAnsiEncoding >>",
    ]
    lines = ["SYNTETICKY TESTOVACI FORMULAR - NENI OFICIALNI",
             "ZADOST O POVOLENI K TRVALEMU POBYTU",
             f"Platnost formulare od {version}", ""]
    y = 700
    placed = []
    for name, tooltip, label in fields:
        objects.append((f"<< /Type /Annot /Subtype /Widget /FT /Tx /T ({_esc(name)}) "
                        f"/TU ({_esc(tooltip)}) /Rect [230 {y} 540 {y + 18}] /P 3 0 R "
                        "/F 4 /DA (/Helv 10 Tf 0 g) /MK << /BC [0 0 0] >> >>").encode())
        placed.append((y + 5, label))
        y -= 30
    objects.append(_stream(lines, tuple(placed)))
    objects.append((f"<< /Title (Zadost o povoleni k trvalemu pobytu - SYNTETICKA) "
                    f"/Subject (Platnost formulare od {version}) /Producer (bossman-test-fixture) >>").encode())
    assert len(objects) == info_no
    return _pdf(objects)


def text_pdf(lines: list[str]) -> bytes:
    objects = [
        b"<< /Type /Catalog /Pages 2 0 R >>",
        b"<< /Type /Pages /Kids [3 0 R] /Count 1 >>",
        b"<< /Type /Page /Parent 2 0 R /MediaBox [0 0 595 842] "
        b"/Resources << /Font << /F1 4 0 R >> >> /Contents 5 0 R >>",
        b"<< /Type /Font /Subtype /Type1 /BaseFont /Helvetica /Encoding /WinAnsiEncoding >>",
        _stream(lines),
        b"<< /Title (Pokyny - SYNTETICKE) /Producer (bossman-test-fixture) >>",
    ]
    return _pdf(objects)


HTML_ERROR_PAGE = (b"<!DOCTYPE html>\n<html><head><title>Chyba 404</title></head>"
                   b"<body><h1>Stranka nenalezena</h1><p>Pozadovany soubor neexistuje.</p>"
                   b"</body></html>\n")


def build(dest: Path, variant: str = "happy") -> Path:
    """Materialise one fixture variant under ``dest`` and return it."""
    if variant not in VARIANTS:
        raise ValueError(f"unknown variant {variant!r}; choose from {VARIANTS}")
    dest = Path(dest)
    if dest.exists():
        shutil.rmtree(dest)
    shutil.copytree(HERE / "site", dest / "site")
    shutil.copyfile(HERE / "sources.json", dest / "sources.json")
    files = dest / "site" / "www.mvcr.cz" / "soubor"
    files.mkdir(parents=True, exist_ok=True)
    (files / "zadost-trvaly-pobyt-2026.pdf").write_bytes(form_pdf("01.01.2026"))
    (files / "zadost-trvaly-pobyt-2019.pdf").write_bytes(form_pdf("01.03.2019"))
    (files / "pokyny-poplatek.pdf").write_bytes(text_pdf([
        "POKYNY K UHRADE SPRAVNIHO POPLATKU (SYNTETICKE)",
        "Spravni poplatek za zadost o povoleni k trvalemu pobytu cini 2 500 Kc.",
        "Poplatek se hradi kolkovou znamkou.",
    ]))
    # The lookalike serves a real-looking PDF so that only the domain policy stops it.
    look = dest / "site" / "www.mvcr-cz.info" / "soubor"
    look.mkdir(parents=True, exist_ok=True)
    (look / "zadost-trvaly-pobyt.pdf").write_bytes(form_pdf("01.01.2030"))

    page = dest / "site" / "www.mvcr.cz" / "clanek" / "trvaly-pobyt.html"
    fees = dest / "site" / "www.mvcr.cz" / "clanek" / "poplatky.html"
    html = page.read_text(encoding="utf-8")
    if variant == "conflicting_fee":
        fees.write_text(fees.read_text(encoding="utf-8").replace("2 500 Kč", "5 000 Kč"),
                        encoding="utf-8")
    elif variant == "stale_only":
        html = html.replace(
            '<li><a href="/soubor/zadost-trvaly-pobyt-2026.pdf">Žádost o povolení k trvalému '
            'pobytu (formulář)</a></li>', "")
        (files / "zadost-trvaly-pobyt-2026.pdf").unlink()
    elif variant == "two_versions":
        html = html.replace("Žádost o povolení k trvalému pobytu – archiv, neplatný formulář",
                            "Žádost o povolení k trvalému pobytu (formulář, PDF)")
    elif variant == "html_as_pdf":
        (files / "zadost-trvaly-pobyt-2026.pdf").write_bytes(HTML_ERROR_PAGE)
    page.write_text(html, encoding="utf-8")
    return dest


def build_owner_folder(dest: Path) -> Path:
    """A synthetic owner folder: the documents the synthetic facts point at."""
    dest = Path(dest)
    dest.mkdir(parents=True, exist_ok=True)
    (dest / "pas_SYNTETICKY.txt").write_text(
        "SYNTETICKY DOKLAD - NENI SKUTECNY\nSYNTETICKA-TESTOVA Fiktivni Osoba\n", encoding="utf-8")
    (dest / "povoleni_SYNTETICKE.txt").write_text(
        "SYNTETICKE POVOLENI K POBYTU - NENI SKUTECNE\n2019-03-01 ...\n", encoding="utf-8")
    return dest


def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    ap.add_argument("dest")
    ap.add_argument("--variant", default="happy", choices=VARIANTS)
    args = ap.parse_args()
    root = Path(args.dest)
    build(root / "fixtures", args.variant)
    build_owner_folder(root / "owner")
    shutil.copyfile(HERE / "facts_synthetic.json", root / "facts.json")
    print(root)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
