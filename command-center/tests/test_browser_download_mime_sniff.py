"""A browser download's type comes from its BYTES, not its name.

HW-10 risk found by the MVČR lane: an HTML error page saved as `form.pdf` was
recorded as application/pdf because `_sniff_mime` fell back to the extension.
The owner (and any later step) would then treat an error page as the official
form. Pair: a real PDF is still application/pdf; a text type without a magic
signature (.csv) still uses the extension."""
from __future__ import annotations

from bcc.v2 import browser_control as bc


def test_html_error_page_named_pdf_is_not_a_pdf(tmp_path):
    fake = tmp_path / "zadost.pdf"
    fake.write_bytes(b"\xef\xbb\xbf  <!DOCTYPE html><html><body>502 Bad Gateway</body></html>")
    assert bc._sniff_mime(fake) == "text/html"
    assert bc._mime_mismatch(fake) is True


def test_bytes_without_the_promised_signature_are_not_labelled_by_extension(tmp_path):
    junk = tmp_path / "photo.png"
    junk.write_bytes(b"not an image at all")
    assert bc._sniff_mime(junk) == "application/octet-stream"
    assert bc._mime_mismatch(junk) is True


def test_a_real_pdf_and_a_plain_csv_keep_their_types(tmp_path):
    pdf = tmp_path / "form.pdf"
    pdf.write_bytes(b"%PDF-1.7\n%\xe2\xe3\xcf\xd3\n1 0 obj\n")
    csv = tmp_path / "data.csv"
    csv.write_text("a,b\n1,2\n", encoding="utf-8")
    assert bc._sniff_mime(pdf) == "application/pdf" and bc._mime_mismatch(pdf) is False
    assert bc._sniff_mime(csv) == "text/csv" and bc._mime_mismatch(csv) is False
