"""doc_intake — owner document intake: accept → parse immediately → library (file manager) → memory notes.

Local only: documents are parsed on this PC with open-source libraries (pypdf,
python-docx, openpyxl, BeautifulSoup; Docling when installed and enabled) and
the repo's own ``bossman.file_intel`` parsers; no document content is sent to
any network service. See README_RU.md.
"""
__all__ = ["library", "parsers", "rules", "memory", "notes", "safety", "watch", "cli"]
