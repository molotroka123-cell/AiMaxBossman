"""What the library accepts. Decided BEFORE anything is copied, hashed in full or parsed.

* Executables, scripts and archives are refused by extension AND by content
  (a renamed ``.exe`` is still an executable: the PE header is checked exactly).
* Office/EPUB containers are ZIPs: they are accepted only when they look like
  the format they claim (content-types part present) and stay inside bounded
  expansion; any other ZIP is an archive and is refused.
* Unknown types are refused instead of being stored as opaque blobs.
"""
from __future__ import annotations

import zipfile
from dataclasses import dataclass
from pathlib import Path

PDF = {".pdf": "pdf"}
OFFICE = {".docx": "docx", ".xlsx": "xlsx", ".xlsm": "xlsx", ".pptx": "pptx", ".epub": "epub"}
TEXT = {".txt": "txt", ".text": "txt", ".md": "md", ".markdown": "md"}
TABLE = {".csv": "csv", ".tsv": "csv"}
HTML = {".html": "html", ".htm": "html", ".xhtml": "html"}
IMAGE = {e: "image" for e in (".png", ".jpg", ".jpeg", ".webp", ".gif", ".bmp", ".tif", ".tiff", ".heic")}
#: Real documents with no local parser here: stored honestly as UNSUPPORTED.
LEGACY = {e: "legacy" for e in (".doc", ".xls", ".ppt", ".rtf", ".odt", ".ods", ".odp")}
KINDS: dict[str, str] = {**PDF, **OFFICE, **TEXT, **TABLE, **HTML, **IMAGE, **LEGACY}

BLOCKED_EXT = frozenset({
    ".exe", ".dll", ".sys", ".scr", ".com", ".msi", ".msp", ".msix", ".appx", ".appxbundle", ".bat", ".cmd",
    ".ps1", ".psm1", ".psd1", ".vbs", ".vbe", ".js", ".jse", ".wsf", ".wsh", ".hta", ".cpl", ".jar", ".apk",
    ".lnk", ".url", ".reg", ".sh", ".py", ".pyw", ".so", ".dylib", ".bin", ".iso", ".img", ".vhd", ".vhdx",
    ".zip", ".rar", ".7z", ".tar", ".gz", ".tgz", ".bz2", ".xz", ".zst", ".cab", ".arj", ".lz", ".lzh",
    ".dmg", ".pkg", ".deb", ".rpm",
})

OLE_MAGIC = b"\xd0\xcf\x11\xe0\xa1\xb1\x1a\xe1"
ZIP_MAGIC = b"PK\x03\x04"
HEAD_BYTES = 4096
MAX_ZIP_ENTRIES = 10_000
MAX_ZIP_EXPANDED = 1024 * 1024 * 1024
MAX_ZIP_RATIO = 200


@dataclass(frozen=True)
class Refusal:
    code: str
    reason: str


def kind_of(ext: str) -> str:
    return KINDS.get((ext or "").lower(), "")


def _is_pe(head: bytes) -> bool:
    if not head.startswith(b"MZ") or len(head) < 0x40:
        return False
    offset = int.from_bytes(head[0x3C:0x40], "little")
    return 0 < offset <= len(head) - 4 and head[offset:offset + 4] == b"PE\x00\x00"


def _binary_kind(head: bytes) -> str:
    """Name of an executable/archive format found in the first bytes, or ''."""
    if _is_pe(head):
        return "исполняемый файл Windows (PE)"
    checks = (
        (b"\x7fELF", "исполняемый файл ELF"),
        (b"\xcf\xfa\xed\xfe", "исполняемый файл Mach-O"),
        (b"\xce\xfa\xed\xfe", "исполняемый файл Mach-O"),
        (b"Rar!\x1a\x07", "архив RAR"),
        (b"7z\xbc\xaf\x27\x1c", "архив 7z"),
        (b"\x1f\x8b", "архив gzip"),
        (b"\xfd7zXZ\x00", "архив xz"),
        (b"MSCF\x00\x00\x00\x00", "архив CAB"),
        (b"\x28\xb5\x2f\xfd", "архив zstd"),
    )
    for magic, name in checks:
        if head.startswith(magic):
            return name
    if head.startswith(b"BZh") and len(head) >= 10 and head[3:4].isdigit() and head[4:10] == b"1AY&SY":
        return "архив bzip2"
    if len(head) > 262 and head[257:262] == b"ustar":
        return "архив tar"
    return ""


def _check_container(path: Path, kind: str) -> Refusal | None:
    try:
        with zipfile.ZipFile(path) as archive:
            members = archive.infolist()
            names = {m.filename for m in members}
            if len(members) > MAX_ZIP_ENTRIES:
                return Refusal("CONTAINER_TOO_BIG", "слишком много частей внутри документа")
            total = 0
            for m in members:
                name = m.filename.replace("\\", "/")
                if name.startswith("/") or ".." in name.split("/"):
                    return Refusal("CONTAINER_UNSAFE", "небезопасный путь внутри документа")
                if m.flag_bits & 0x1:
                    return Refusal("CONTAINER_ENCRYPTED", "документ зашифрован")
                total += m.file_size
                if m.compress_size and m.file_size / max(m.compress_size, 1) > MAX_ZIP_RATIO and m.file_size > 10_000_000:
                    return Refusal("CONTAINER_BOMB", "подозрительно сильное сжатие (zip-бомба)")
            if total > MAX_ZIP_EXPANDED:
                return Refusal("CONTAINER_TOO_BIG", "документ распаковывается больше чем в 1 ГБ")
    except (zipfile.BadZipFile, OSError):
        return Refusal("CONTAINER_INVALID", "повреждённый Office/EPUB-файл")
    marker = "mimetype" if kind == "epub" else "[Content_Types].xml"
    if marker not in names:
        return Refusal("ARCHIVE", f"это ZIP-архив, а не настоящий {kind.upper()}: архивы не принимаются")
    return None


def check(path: Path, size: int, max_bytes: int) -> Refusal | None:
    """None when the file may enter the library; otherwise the reason it may not."""
    ext = path.suffix.lower()
    if ext in BLOCKED_EXT:
        return Refusal("BLOCKED_TYPE", f"исполняемые файлы, скрипты и архивы не принимаются ({ext})")
    kind = kind_of(ext)
    if not kind:
        return Refusal("UNKNOWN_TYPE", f"неизвестный тип файла ({ext or 'без расширения'}): принимаются PDF, DOCX, "
                                       "XLSX/CSV, TXT/MD, HTML, PPTX/EPUB и изображения")
    if size <= 0:
        return Refusal("EMPTY", "пустой файл")
    if size > max_bytes:
        return Refusal("TOO_LARGE", f"файл {size / 1048576:.1f} МБ больше лимита {max_bytes / 1048576:.0f} МБ")
    try:
        with open(path, "rb") as handle:
            head = handle.read(HEAD_BYTES)
    except OSError as exc:
        return Refusal("UNREADABLE", f"файл не читается: {exc.strerror or type(exc).__name__}")
    binary = _binary_kind(head)
    if binary:
        return Refusal("BLOCKED_CONTENT", f"по содержимому это {binary}, хотя расширение {ext}: не принимается")
    if head.startswith(OLE_MAGIC) and kind != "legacy":
        return Refusal("BLOCKED_CONTENT", f"по содержимому это OLE-контейнер (как .msi/.doc), а не {ext}")
    if head.startswith(ZIP_MAGIC):
        if kind not in ("docx", "xlsx", "pptx", "epub"):
            return Refusal("ARCHIVE", f"по содержимому это ZIP-архив, а не {ext}: архивы не принимаются")
        return _check_container(path, kind)
    if kind in ("docx", "xlsx", "pptx", "epub"):
        return Refusal("CONTAINER_INVALID", f"файл {ext} не является документом Office/EPUB")
    return None
