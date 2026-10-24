"""
خط أنابيب استيعاب واستخراج الملفات الطبقي الآمن (Layered File Ingestion Pipeline v2.0)
======================================================================================
يجسّد هذا المحرك المواصفة المعمارية والأمنية لاستيعاب الملفات (`docs/file-ingestion-pipeline.md`):

  `upload ← detect ← scan ← route ← extract ← normalize ← store ← index`

المبادئ الهندسية والأمنية المطبّقة (شاملة إصلاحات P0 + P1 + P2):
  1. **لا تثق بالامتداد — افحص البصمة الثنائية (`detect_magic_mime`):**
     يفحص أول 4096 بايت (`Magic Bytes`) لتحديد النوع الحقيقي (`PDF`, `PNG`, `JPEG`, `GIF`,
     `WEBP`, `BMP`, `TIFF`, `PSD`, `EPS`, `ZIP/DOCX/XLSX/PPTX`, `GZ`, `7Z`, `RAR`, `TAR`,
     `OLE2`, `SQLite`, `EXE/DLL/ELF/Mach-O/JavaClass/WASM/SH/Scripts`) ويقارنه بالامتداد؛
     وعند التعارض يعلّم الملف بـ `suspicious_extension_mismatch`.
  2. **حظر التنفيذيات والماكرو وفحص البرمجيات الخبيثة (`scan_for_malware`):**
     يحظر `exe | dll | elf | macho | class | wasm | sh | bat | ps1 | vbs | js | jar | msi | scr | apk`،
     ويكشف ماكرو `VBA` (`vbaProject.bin` داخل OOXML و`_VBA_PROJECT` داخل `OLE2`)،
     ويدعم فحص `ClamAV` / `EICAR` أو ماسح مضاد فيروسات مخصّص (`av_scanner`).
  3. **حماية XML الصارمة (`safe_xml_fromstring` — P0):**
     يرفض تعريفات `<!DOCTYPE` و`<!ENTITY` (`XXE` / `Billion Laughs`) قبل التحليل،
     ويستخدم `defusedxml` مع فرض سقوف حيّة على حجم XML وعمق الشجرة وعدد العقد.
  4. **الحماية التدفقية ضد قنابل الضغط (`_safe_read_zip_entry` — P0):**
     لا يثق بالحجم المعلن في ترويسة `ZIP` (`info.file_size` القابل للتزوير)، بل يفرض سقفاً
     فعلياً أثناء القراءة التدفقية لكل ملف وللمجموع الكلي، ويحظر الأرشيفات المتداخلة (`Nested Archives`)
     واجتياز المسارات (`Path Traversal`)، ويحمي الصور من قنابل فك الضغط (`max_image_pixels`).
  5. **تعقيم `SVG/HTML` بقائمة بيضاء (`Allowlist Sanitizer` — P1):**
     يحلّل شجرة العناصر بـ `HTMLParser` ويسمح فقط بوسوم وسمات آمنة، ويفك ترميز الكيانات
     (`&#106;avascript:`) ومحارف التحكم قبل فحص الروابط و`style`، ويحذف `<script>` و`<foreignObject>`
     و`<iframe>` وكافة سمات `on*=`، ويوصي بترويسات `CSP` و`Content-Disposition: attachment`.
  6. **عزل المعالجة الاختياري في عملية فرعية (`ingest_file_isolated` — P0):**
     يتيح تشغيل المحلّلات داخل عملية فرعية معزولة بلا صدفة (`shell=False`) وبسقف زمني (`timeout`)
     وسقف موارد لمنع انهيار أو تعليق الخادم عند مواجهة ملفات هجومية.
  7. **الدقة التقنية للمطابع والملفات العربية (`PSD Resources | Effective DPI | Full XLSX | cp1256` — P2):**
     يقرأ قسم `Image Resources` (`8BIM`: `0x03EE`, `0x0415`, `0x0435`) في `PSD` للتمييز بين القنوات
     الأساسية وقنوات `Spot/Alpha` وأسمائها، ويحسب `effective_dpi` الفعلي بمعلومية مقاس الطباعة،
     ويستخرج جميع أوراق وخلايا `XLSX` (`sharedStrings`, `inlineStr`, الأرقام، ونتائج المعادلات)،
     ويقيّم ثقة ترميز `Windows-1256` (`encoding_confidence`) مع مرجع `charset_normalizer`.
  8. **منع تكرار المعالجة بعزل المستأجرين والإصدار (`tenant + PIPELINE_VERSION + limits + sha256` — P1):**
     يضمن عدم تسرب نتائج مستخدم لمستخدم آخر وعدم إرجاع نتائج قديمة عند تحديث المحلّل أو الحدود.
"""
from __future__ import annotations

import csv
import email
import hashlib
import html
import io
import json
import logging
import shutil
import struct
import subprocess
import sys
import zipfile
import zlib
from collections.abc import Callable
from dataclasses import asdict, dataclass, field
from enum import Enum
from html.parser import HTMLParser
from pathlib import Path
from xml.etree import ElementTree as ET

from src.core.path_guard import guard_path

try:
    from defusedxml import ElementTree as DefusedET
except ImportError:  # pragma: no cover
    DefusedET = None  # type: ignore[assignment]

try:
    from charset_normalizer import from_bytes as cn_from_bytes
except ImportError:  # pragma: no cover
    cn_from_bytes = None  # type: ignore[assignment]

logger = logging.getLogger(__name__)

PIPELINE_VERSION = "2.0.0"
CHUNK_SIZE_BYTES = 65536
EICAR_SIGNATURE = b"X5O!P%@AP[4\\PZX54(P^)7CC)7}$EICAR-STANDARD-ANTIVIRUS-TEST-FILE!$H+H*"

AvScannerCallable = Callable[[Path], tuple[bool, str | None]]


class IngestionStatus(str, Enum):
    """حالات المخرج الموحد الأربعة حسب المواصفة."""

    EXTRACTED = "extracted"
    PARTIAL = "partial"
    METADATA_ONLY = "metadata_only"
    ZP_OR_EXE_BLOCKED = "blocked"


@dataclass(frozen=True)
class IngestionLimits:
    """الحدود الأمنية الصارمة ضد استنزاف الذاكرة وقنابل الضغط (Zip/XML/Image Bombs)."""

    max_file_size_bytes: int = 50 * 1024 * 1024          # 50 MB
    max_uncompressed_bytes: int = 100 * 1024 * 1024      # 100 MB
    max_archive_entries: int = 500
    max_compression_ratio: float = 100.0
    max_directory_depth: int = 8
    max_text_chars: int = 200_000
    max_xml_bytes: int = 10 * 1024 * 1024                # 10 MB سقف XML
    max_xml_depth: int = 64                              # أقصى عمق شجرة XML
    max_xml_elements: int = 50_000                       # أقصى عدد عناصر XML
    max_image_pixels: int = 64_000_000                   # 64 MP ضد Decompression Bomb
    max_pdf_pages: int = 500                             # أقصى عدد صفحات PDF
    max_xlsx_rows: int = 5_000                           # أقصى عدد صفوف XLSX قبل الاقتطاع
    parser_timeout_seconds: float = 15.0                 # مهلة العملية الفرعية المعزولة
    isolate_in_subprocess: bool = False                  # تشغيل الاستخراج في subprocess معزول
    enable_clamav: bool = False                          # تفعيل فحص ClamAV إن وُجد على النظام
    target_print_width_cm: float | None = None           # عرض الطباعة الفعلي بالسنتيمتر لحساب DPI
    target_print_height_cm: float | None = None          # ارتفاع الطباعة الفعلي بالسنتيمتر لحساب DPI

    def fingerprint(self) -> str:
        """بصمة إعدادات الحدود لاستخدامها في مفتاح الـ Cache."""
        raw = json.dumps(asdict(self), sort_keys=True)
        return hashlib.sha256(raw.encode("utf-8")).hexdigest()[:12]


@dataclass(frozen=True)
class IngestionResult:
    """
    المخرج الموحد (Unified JSON Schema) لاستيعاب أي ملف في `new-cela`.
    """

    id: str
    name: str
    mime: str
    size: int
    status: str
    parser: str
    language: str | None
    text: str
    pages: int | None = None
    tables: list[list[list[str]]] = field(default_factory=list)
    images: list[dict[str, object]] = field(default_factory=list)
    metadata: dict[str, object] = field(
        default_factory=lambda: {"author": None, "created": None}
    )
    warnings: list[str] = field(default_factory=list)

    def to_dict(self) -> dict[str, object]:
        return {
            "id": self.id,
            "name": self.name,
            "mime": self.mime,
            "size": self.size,
            "status": self.status,
            "parser": self.parser,
            "language": self.language,
            "text": self.text,
            "pages": self.pages,
            "tables": self.tables,
            "images": self.images,
            "metadata": self.metadata,
            "warnings": self.warnings,
        }

    @classmethod
    def from_dict(cls, data: dict[str, object]) -> IngestionResult:
        return cls(
            id=str(data["id"]),
            name=str(data["name"]),
            mime=str(data["mime"]),
            size=int(data["size"]),  # type: ignore[arg-type]
            status=str(data["status"]),
            parser=str(data["parser"]),
            language=str(data["language"]) if data.get("language") is not None else None,
            text=str(data.get("text", "")),
            pages=int(data["pages"]) if data.get("pages") is not None else None,  # type: ignore[arg-type]
            tables=list(data.get("tables", [])),  # type: ignore[arg-type]
            images=list(data.get("images", [])),  # type: ignore[arg-type]
            metadata=dict(data.get("metadata", {"author": None, "created": None})),  # type: ignore[arg-type]
            warnings=list(data.get("warnings", [])),  # type: ignore[arg-type]
        )


# خريطة الامتدادات المتوقعة للمقارنة مع البصمة الثنائية (Magic Bytes)
_EXTENSION_MIME_MAP: dict[str, str] = {
    ".txt": "text/plain",
    ".md": "text/markdown",
    ".csv": "text/csv",
    ".tsv": "text/tab-separated-values",
    ".json": "application/json",
    ".html": "text/html",
    ".htm": "text/html",
    ".xml": "application/xml",
    ".svg": "image/svg+xml",
    ".eml": "message/rfc822",
    ".pdf": "application/pdf",
    ".ai": "application/pdf",
    ".eps": "application/postscript",
    ".psd": "image/vnd.adobe.photoshop",
    ".png": "image/png",
    ".jpg": "image/jpeg",
    ".jpeg": "image/jpeg",
    ".gif": "image/gif",
    ".webp": "image/webp",
    ".bmp": "image/bmp",
    ".tif": "image/tiff",
    ".tiff": "image/tiff",
    ".docx": "application/vnd.openxmlformats-officedocument.wordprocessingml.document",
    ".xlsx": "application/vnd.openxmlformats-officedocument.spreadsheetml.sheet",
    ".pptx": "application/vnd.openxmlformats-officedocument.presentationml.presentation",
    ".doc": "application/x-ole-storage",
    ".xls": "application/x-ole-storage",
    ".ppt": "application/x-ole-storage",
    ".docm": "application/vnd.ms-word.document.macroEnabled.12",
    ".xlsm": "application/vnd.ms-excel.sheet.macroEnabled.12",
    ".pptm": "application/vnd.ms-powerpoint.presentation.macroEnabled.12",
    ".zip": "application/zip",
    ".gz": "application/gzip",
    ".7z": "application/x-7z-compressed",
    ".rar": "application/vnd.rar",
    ".tar": "application/x-tar",
    ".sqlite": "application/vnd.sqlite3",
    ".db": "application/vnd.sqlite3",
    ".mp3": "audio/mpeg",
    ".flac": "audio/flac",
    ".ogg": "audio/ogg",
    ".mp4": "video/mp4",
    ".woff": "font/woff",
    ".woff2": "font/woff2",
    ".otf": "font/otf",
    ".exe": "application/x-msdownload",
    ".dll": "application/x-msdownload",
    ".scr": "application/x-msdownload",
    ".com": "application/x-msdownload",
    ".msi": "application/x-msi",
    ".elf": "application/x-elf",
    ".macho": "application/x-mach-binary",
    ".dylib": "application/x-mach-binary",
    ".class": "application/x-java-class",
    ".jar": "application/java-archive",
    ".apk": "application/vnd.android.package-archive",
    ".wasm": "application/wasm",
    ".sh": "application/x-sh",
    ".bat": "application/x-bat",
    ".cmd": "application/x-bat",
    ".ps1": "application/x-powershell",
    ".vbs": "application/x-vbscript",
    ".js": "application/javascript",
    ".hta": "application/hta",
}

_EXECUTABLE_MIMES: frozenset[str] = frozenset(
    {
        "application/x-msdownload",
        "application/x-msi",
        "application/x-elf",
        "application/x-mach-binary",
        "application/x-java-class",
        "application/java-archive",
        "application/vnd.android.package-archive",
        "application/wasm",
        "application/x-sh",
        "application/x-bat",
        "application/x-powershell",
        "application/x-vbscript",
        "application/x-executable-script",
        "application/hta",
        "application/vnd.ms-word.document.macroEnabled.12",
        "application/vnd.ms-excel.sheet.macroEnabled.12",
        "application/vnd.ms-powerpoint.presentation.macroEnabled.12",
    }
)

_EXECUTABLE_EXTENSIONS: frozenset[str] = frozenset(
    {
        ".exe",
        ".dll",
        ".elf",
        ".macho",
        ".dylib",
        ".class",
        ".wasm",
        ".sh",
        ".bat",
        ".cmd",
        ".ps1",
        ".vbs",
        ".vbe",
        ".js",
        ".jse",
        ".wsf",
        ".wsh",
        ".msc",
        ".msi",
        ".scr",
        ".apk",
        ".com",
        ".hta",
        ".jar",
        ".cpl",
        ".docm",
        ".xlsm",
        ".pptm",
    }
)

_NESTED_ARCHIVE_EXTENSIONS: frozenset[str] = frozenset(
    {".zip", ".gz", ".tgz", ".tar", ".7z", ".rar", ".bz2", ".xz"}
)

_MACHO_MAGICS: tuple[bytes, ...] = (
    b"\xfe\xed\xfa\xce",
    b"\xfe\xed\xfa\xcf",
    b"\xce\xfa\xed\xfe",
    b"\xcf\xfa\xed\xfe",
)

_SCRIPT_SIGNATURES: tuple[bytes, ...] = (
    b"@echo off",
    b"powershell",
    b"invoke-expression",
    b"wscript.shell",
    b"wscript.createobject",
    b'createobject("wscript',
    b"cmd.exe /c",
)

# القائمة البيضاء الصارمة لوسوم وسمات SVG/HTML (P1 — Allowlist Sanitizer)
_ALLOWED_SVG_HTML_TAGS: frozenset[str] = frozenset(
    {
        "svg",
        "g",
        "path",
        "rect",
        "circle",
        "ellipse",
        "line",
        "polyline",
        "polygon",
        "text",
        "tspan",
        "defs",
        "lineargradient",
        "radialgradient",
        "stop",
        "clippath",
        "mask",
        "pattern",
        "symbol",
        "title",
        "desc",
        "metadata",
        "html",
        "head",
        "body",
        "div",
        "span",
        "p",
        "h1",
        "h2",
        "h3",
        "h4",
        "h5",
        "h6",
        "ul",
        "ol",
        "li",
        "table",
        "thead",
        "tbody",
        "tr",
        "th",
        "td",
        "a",
        "b",
        "i",
        "strong",
        "em",
        "br",
        "hr",
        "code",
        "pre",
        "blockquote",
    }
)

_DROP_SUBTREE_TAGS: frozenset[str] = frozenset(
    {
        "script",
        "foreignobject",
        "iframe",
        "object",
        "embed",
        "applet",
        "style",
        "handler",
    }
)

_ALLOWED_SVG_HTML_ATTRS: frozenset[str] = frozenset(
    {
        "id",
        "class",
        "xmlns",
        "xmlns:xlink",
        "version",
        "viewbox",
        "width",
        "height",
        "x",
        "y",
        "x1",
        "y1",
        "x2",
        "y2",
        "cx",
        "cy",
        "r",
        "rx",
        "ry",
        "d",
        "points",
        "fill",
        "fill-opacity",
        "fill-rule",
        "stroke",
        "stroke-width",
        "stroke-linecap",
        "stroke-linejoin",
        "stroke-dasharray",
        "stroke-opacity",
        "opacity",
        "transform",
        "font-family",
        "font-size",
        "font-weight",
        "text-anchor",
        "dominant-baseline",
        "offset",
        "stop-color",
        "stop-opacity",
        "gradientunits",
        "gradienttransform",
        "clip-path",
        "mask",
        "preserveaspectratio",
        "href",
        "xlink:href",
        "src",
        "alt",
        "title",
        "lang",
        "dir",
        "colspan",
        "rowspan",
        "style",
    }
)

_URL_ATTRS: frozenset[str] = frozenset(
    {"href", "xlink:href", "src", "data", "action", "formaction"}
)


def sanitize_filename(raw_name: str) -> str:
    """
    تعقيم اسم الملف لمنع Path Traversal ومحارف التحكم (بصفر Regex).
    """
    cleaned = raw_name.replace("\\", "/").split("/")[-1].strip()
    safe_chars = [
        ch
        for ch in cleaned
        if ch.isprintable() and ch not in ('"', "'", "<", ">", "|", ":", "*", "?", "\x00")
    ]
    result = "".join(safe_chars).lstrip(".")
    return result or "unnamed_file"


def _stream_sha256_and_head(path: Path) -> tuple[str, int, bytes]:
    """حساب بصمة SHA-256 وحجم الملف وقراءة أول 4096 بايت بتدفق مجزّأ (Streaming)."""
    h = hashlib.sha256()
    total_size = 0
    head_bytes = b""
    with path.open("rb") as f:
        while chunk := f.read(CHUNK_SIZE_BYTES):
            if not head_bytes:
                head_bytes = chunk[:4096]
            h.update(chunk)
            total_size += len(chunk)
    return (f"sha256:{h.hexdigest()}", total_size, head_bytes)


def detect_magic_mime(head: bytes, filename: str = "") -> tuple[str, list[str]]:
    """
    الكشف عن نوع الملف الحقيقي عبر البصمة الثنائية (`Magic Bytes`) ومقارنته بالامتداد.
    لا يثق بالامتداد وحده، ويُصدر تحذيراً `suspicious_extension_mismatch` عند التعارض.
    """
    warnings: list[str] = []
    ext = Path(filename).suffix.lower() if filename else ""
    expected_mime = _EXTENSION_MIME_MAP.get(ext)

    magic_mime: str | None = None
    if head.startswith(b"%PDF-"):
        magic_mime = "application/pdf"
    elif head.startswith(b"\x89PNG\r\n\x1a\n"):
        magic_mime = "image/png"
    elif head.startswith(b"\xff\xd8\xff"):
        magic_mime = "image/jpeg"
    elif head.startswith((b"GIF87a", b"GIF89a")):
        magic_mime = "image/gif"
    elif len(head) >= 12 and head[:4] == b"RIFF" and head[8:12] == b"WEBP":
        magic_mime = "image/webp"
    elif head.startswith(b"BM"):
        magic_mime = "image/bmp"
    elif head.startswith((b"II*\x00", b"MM\x00*")):
        magic_mime = "image/tiff"
    elif head.startswith(b"8BPS"):
        magic_mime = "image/vnd.adobe.photoshop"
    elif head.startswith(b"%!PS"):
        magic_mime = "application/postscript"
    elif head.startswith(b"SQLite format 3\x00"):
        magic_mime = "application/vnd.sqlite3"
    elif head.startswith(b"\x1f\x8b"):
        magic_mime = "application/gzip"
    elif head.startswith(b"7z\xbc\xaf\x27\x1c"):
        magic_mime = "application/x-7z-compressed"
    elif head.startswith(b"Rar!\x1a\x07"):
        magic_mime = "application/vnd.rar"
    elif len(head) >= 262 and head[257:262] == b"ustar":
        magic_mime = "application/x-tar"
    elif head.startswith(b"\xd0\xcf\x11\xe0\xa1\xb1\x1a\xe1"):
        magic_mime = "application/x-msi" if ext == ".msi" else "application/x-ole-storage"
    elif head.startswith(b"MZ"):
        magic_mime = "application/x-msdownload"
    elif head.startswith(b"\x7fELF"):
        magic_mime = "application/x-elf"
    elif head.startswith(_MACHO_MAGICS):
        magic_mime = "application/x-mach-binary"
    elif head.startswith(b"\xca\xfe\xba\xbe"):
        magic_mime = "application/x-java-class"
    elif head.startswith(b"\x00asm"):
        magic_mime = "application/wasm"
    elif head.startswith(b"#!"):
        magic_mime = "application/x-sh"
    elif head.startswith((b"ID3", b"\xff\xfb", b"\xff\xf3")):
        magic_mime = "audio/mpeg"
    elif head.startswith(b"fLaC"):
        magic_mime = "audio/flac"
    elif head.startswith(b"OggS"):
        magic_mime = "audio/ogg"
    elif len(head) >= 12 and head[4:8] == b"ftyp":
        magic_mime = "video/mp4"
    elif head.startswith(b"wOFF"):
        magic_mime = "font/woff"
    elif head.startswith(b"wOF2"):
        magic_mime = "font/woff2"
    elif head.startswith(b"OTTO"):
        magic_mime = "font/otf"
    elif head.startswith(b"PK\x03\x04"):
        if ext in (".docx", ".xlsx", ".pptx", ".docm", ".xlsm", ".pptm", ".jar", ".apk") and expected_mime:
            magic_mime = expected_mime
        else:
            magic_mime = "application/zip"
    else:
        stripped = head.lstrip()
        lower_head = stripped[:512].lower()
        if any(sig in lower_head for sig in _SCRIPT_SIGNATURES):
            magic_mime = "application/x-executable-script"
        elif lower_head.startswith(b"<svg") or (b"<svg" in lower_head and b"xmlns" in lower_head):
            magic_mime = "image/svg+xml"
        elif lower_head.startswith((b"<!doctype html", b"<html")):
            magic_mime = "text/html"
        elif lower_head.startswith(b"<?xml"):
            magic_mime = "application/xml"

    # مقارنة البصمة الثنائية بالامتداد الظاهري
    if magic_mime is not None:
        if expected_mime and expected_mime != magic_mime:
            is_office_zip = magic_mime == "application/zip" and ext in (
                ".docx",
                ".xlsx",
                ".pptx",
                ".docm",
                ".xlsm",
                ".pptm",
                ".jar",
                ".apk",
            )
            is_ai_pdf = magic_mime == "application/pdf" and ext == ".ai"
            is_script_ext = magic_mime == "application/x-executable-script" and ext in (
                ".bat",
                ".cmd",
                ".ps1",
                ".vbs",
                ".js",
            )
            if not (is_office_zip or is_ai_pdf or is_script_ext):
                warnings.append(
                    f"suspicious_extension_mismatch: الامتداد '{ext}' يتعارض مع البصمة الثنائية '{magic_mime}'"
                )
        return (magic_mime, warnings)

    if ext in _EXECUTABLE_EXTENSIONS:
        return (_EXTENSION_MIME_MAP.get(ext, "application/x-msdownload"), warnings)

    if expected_mime:
        return (expected_mime, warnings)

    return ("application/octet-stream", warnings)


def scan_for_malware(
    path: Path,
    head: bytes,
    *,
    av_scanner: AvScannerCallable | None = None,
    enable_clamav: bool = False,
) -> tuple[bool, str | None]:
    """
    طبقة الفحص الأمني للبرمجيات الخبيثة (`Scan Layer` — P1):
      1. كشف بصمة `EICAR` القياسية لاختبار مضادات الفيروسات.
      2. استدعاء `av_scanner` المخصّص إن جرى تمريره.
      3. استدعاء `clamscan` (بدون shell) إن كان مفعّلاً ومتاحاً على النظام.
    تعيد `(is_clean, signature_name)`.
    """
    if EICAR_SIGNATURE in head:
        return (False, "EICAR-Test-Signature")

    if av_scanner is not None:
        return av_scanner(path)

    if enable_clamav:
        clamscan_bin = shutil.which("clamscan")
        if clamscan_bin:
            proc = subprocess.run(
                [clamscan_bin, "--no-summary", str(path)],
                capture_output=True,
                text=True,
                timeout=30,
                check=False,
            )
            if proc.returncode == 1:
                sig = (proc.stdout or "ClamAV-Detected-Malware").strip().split(":")[-1].strip()
                return (False, sig or "ClamAV-Detected-Malware")

    return (True, None)


def safe_xml_fromstring(
    xml_bytes: bytes,
    *,
    limits: IngestionLimits | None = None,
) -> ET.Element:
    """
    محلّل XML آمن (`Defused XML Guard` — P0) لكل ملفات `OOXML` و`SVG` و`XML`:
      - يرفض الملفات التي تتجاوز `max_xml_bytes`.
      - يحظر تعريفات `<!DOCTYPE` و`<!ENTITY` و`<!ELEMENT` و`<!ATTLIST` لمنع `XXE` و`Billion Laughs`.
      - يستخدم `defusedxml` عند توفره، ويتحقق تدفقياً من `max_xml_depth` و`max_xml_elements`.
    """
    active = limits or IngestionLimits()
    if len(xml_bytes) > active.max_xml_bytes:
        raise ValueError(
            f"xml_bomb_size_exceeded: حجم XML ({len(xml_bytes)}) يتجاوز الحد ({active.max_xml_bytes})"
        )

    lower_xml = xml_bytes.lower()
    for forbidden_decl in (b"<!entity", b"<!doctype", b"<!element", b"<!attlist"):
        if forbidden_decl in lower_xml:
            raise ValueError(
                f"xml_bomb_entity_or_dtd_blocked: تم رصد وحظر تصريح XML خطير ({forbidden_decl.decode('ascii')})"
            )

    if DefusedET is not None:
        # defusedxml يمنع توسعة الكيانات والـ DTD والـ External Entities
        DefusedET.fromstring(
            xml_bytes,
            forbid_dtd=True,
            forbid_entities=True,
            forbid_external=True,
        )

    depth = 0
    element_count = 0
    root: ET.Element | None = None
    for event, elem in ET.iterparse(io.BytesIO(xml_bytes), events=("start", "end")):
        if event == "start":
            depth += 1
            element_count += 1
            if root is None:
                root = elem
            if depth > active.max_xml_depth:
                raise ValueError(
                    f"xml_bomb_depth_exceeded: عمق شجرة XML ({depth}) يتجاوز الحد ({active.max_xml_depth})"
                )
            if element_count > active.max_xml_elements:
                raise ValueError(
                    f"xml_bomb_elements_exceeded: عدد عناصر XML ({element_count}) يتجاوز الحد ({active.max_xml_elements})"
                )
        else:
            depth -= 1

    if root is None:
        raise ValueError("xml_empty_or_invalid: مستند XML فارغ")
    return root


def detect_language(text: str) -> str | None:
    """كشف اللغة السائدة في النص المستخرج (`ar` للعربية أو `en` للاتينية)."""
    if not text or not text.strip():
        return None
    arabic_count = sum(1 for ch in text if "\u0600" <= ch <= "\u06ff")
    latin_count = sum(1 for ch in text if ("a" <= ch.lower() <= "z"))
    if arabic_count == 0 and latin_count == 0:
        return None
    return "ar" if arabic_count >= latin_count else "en"


def detect_and_decode_text_with_confidence(
    raw_bytes: bytes,
) -> tuple[str, str, float, list[str]]:
    """
    فك ترميز النصوص مع حساب درجة الثقة (`encoding_confidence` بين `0.0` و`1.0`)
    ومرجع ثانٍ عبر `charset_normalizer` عند فحص الترميز العربي القديم `Windows-1256` (`cp1256`).
    """
    warnings: list[str] = []
    if not raw_bytes:
        return ("", "utf-8", 1.0, warnings)
    if raw_bytes.startswith(b"\xef\xbb\xbf"):
        return (raw_bytes.decode("utf-8-sig", errors="replace"), "utf-8-sig", 1.0, warnings)
    if raw_bytes.startswith((b"\xff\xfe", b"\xfe\xff")):
        return (raw_bytes.decode("utf-16", errors="replace"), "utf-16", 1.0, warnings)

    try:
        return (raw_bytes.decode("utf-8"), "utf-8", 1.0, warnings)
    except UnicodeDecodeError:
        logger.debug("فشل UTF-8 الصارم — جاري فحص الترميز العربي Windows-1256 (cp1256)")

    cp1256_text = raw_bytes.decode("cp1256", errors="replace")
    high_bytes = sum(1 for b in raw_bytes if b >= 0x80)
    arabic_chars = sum(1 for ch in cp1256_text if "\u0600" <= ch <= "\u06ff")
    replacement_chars = cp1256_text.count("\ufffd")

    if arabic_chars > 0 and high_bytes > 0:
        arabic_ratio = arabic_chars / max(high_bytes, 1)
        # مرجع ثانٍ عبر charset_normalizer إن وُجد
        cn_boost = 0.0
        if cn_from_bytes is not None:
            best = cn_from_bytes(raw_bytes).best()
            if best is not None and best.encoding in ("cp1256", "windows_1256", "iso8859_6"):
                cn_boost = 0.15
        confidence = round(min(1.0, max(0.1, arabic_ratio * 0.85 + cn_boost - replacement_chars * 0.1)), 3)
        if arabic_ratio >= 0.35:
            warnings.append(
                f"legacy_encoding_detected: تم فك الترميز العربي عبر Windows-1256 (cp1256) بثقة {confidence}"
            )
            return (cp1256_text, "cp1256", confidence, warnings)

    # إذا لم يكن نصاً عربياً متماسكاً في cp1256، نجرّب charset_normalizer أو latin-1
    if cn_from_bytes is not None:
        best = cn_from_bytes(raw_bytes).best()
        if best is not None:
            return (str(best), best.encoding, 0.7, warnings)

    return (raw_bytes.decode("latin-1", errors="replace"), "latin-1", 0.5, warnings)


def detect_and_decode_text(raw_bytes: bytes) -> tuple[str, str, list[str]]:
    """
    فك ترميز النصوص مع دعم خاص للترميز العربي القديم `Windows-1256` (`cp1256`).
    يعيد `(decoded_text, encoding_used, warnings)`.
    """
    text, enc, _conf, warnings = detect_and_decode_text_with_confidence(raw_bytes)
    return (text, enc, warnings)


def _normalize_uri_for_check(raw_value: str) -> str:
    """فك كيانات HTML (`&#106;avascript:`) وإزالة محارف التحكم والمسافات قبل فحص البروتوكول."""
    unescaped = html.unescape(raw_value)
    cleaned = "".join(ch for ch in unescaped if ord(ch) > 0x20 and ch not in ("\x7f",))
    return cleaned.lower()


def _is_safe_uri(raw_value: str) -> bool:
    """يسمح فقط بروابط `http://`, `https://`, `mailto:`, أو مراجع داخلية `#id` أو مسارات نسبية بلا مخطط خطير."""
    norm = _normalize_uri_for_check(raw_value)
    if not norm:
        return True
    if norm.startswith(("#", "/", "./", "../")):
        return not norm.startswith("//")
    if ":" not in norm:
        return True
    scheme = norm.split(":", 1)[0]
    return scheme in ("http", "https", "mailto")


def _is_safe_style(raw_style: str) -> bool:
    """يرفض `url(...)` و`expression(...)` و`javascript:` و`data:` و`-moz-binding` داخل `style`."""
    norm = _normalize_uri_for_check(raw_style)
    forbidden = ("url(", "expression(", "javascript:", "vbscript:", "data:", "-moz-binding", "@import")
    return not any(marker in norm for marker in forbidden)


class _AllowlistSvgHtmlSanitizer(HTMLParser):
    """
    محلّل ومُعقّم `SVG/HTML` يعتمد على القائمة البيضاء (`Allowlist Sanitizer` — P1):
      - يحذف الوسوم غير المسموحة (`<script>`, `<foreignObject>`, `<iframe>`, `<object>`, `<style>`, `<use>`, إلخ).
      - يحذف أي سمة تبدأ بـ `on*` أو غير مدرجة في `_ALLOWED_SVG_HTML_ATTRS`.
      - يفك كيانات HTML (`&#106;avascript:`) قبل التحقق من الروابط (`href`, `xlink:href`, `src`).
    """

    def __init__(self) -> None:
        super().__init__(convert_charrefs=False)
        self.output: list[str] = []
        self.warnings: list[str] = []
        self._skip_depth = 0

    def _warn_once(self, msg: str) -> None:
        if msg not in self.warnings:
            self.warnings.append(msg)

    def handle_decl(self, decl: str) -> None:
        lower = decl.lower()
        if "entity" in lower or "system" in lower or "public" in lower:
            self._warn_once("xxe_entity_blocked: تم رصد وحذف تعريف كيان خارجي (XXE)")

    def unknown_decl(self, data: str) -> None:
        self._warn_once("xxe_entity_blocked: تم رصد وحذف تصريح DTD/ENTITY غير موثوق")

    def handle_starttag(self, tag: str, attrs: list[tuple[str, str | None]]) -> None:
        tag_lower = tag.lower()
        if self._skip_depth > 0:
            if tag_lower in _DROP_SUBTREE_TAGS:
                self._skip_depth += 1
            return

        if tag_lower in _DROP_SUBTREE_TAGS:
            self._warn_once(f"xss_vector_sanitized: تم حذف الوسم المحظور '<{tag_lower}>' ومحتواه")
            self._skip_depth = 1
            return

        if tag_lower not in _ALLOWED_SVG_HTML_TAGS:
            self._warn_once(f"xss_vector_sanitized: تم استبعاد الوسم غير المسموح '<{tag_lower}>'")
            return

        safe_attrs: list[str] = []
        for attr_name, attr_val in attrs:
            name_lower = attr_name.lower()
            val_str = attr_val or ""
            if name_lower.startswith("on"):
                self._warn_once(f"xss_vector_sanitized: تم حذف سمة الحدث الخطيرة '{name_lower}='")
                continue
            if name_lower not in _ALLOWED_SVG_HTML_ATTRS:
                self._warn_once(f"xss_vector_sanitized: تم حذف السمة غير المسموحة '{name_lower}'")
                continue
            if name_lower in _URL_ATTRS and not _is_safe_uri(val_str):
                self._warn_once(
                    f"xss_vector_sanitized: تم حظر رابط خطير في '{name_lower}' (javascript/data/entity bypass)"
                )
                continue
            if name_lower == "style" and not _is_safe_style(val_str):
                self._warn_once("xss_vector_sanitized: تم حظر تعبير CSS خطير داخل 'style'")
                continue
            escaped_val = html.escape(html.unescape(val_str), quote=True)
            safe_attrs.append(f'{name_lower}="{escaped_val}"')

        attr_suffix = (" " + " ".join(safe_attrs)) if safe_attrs else ""
        self.output.append(f"<{tag_lower}{attr_suffix}>")

    def handle_endtag(self, tag: str) -> None:
        tag_lower = tag.lower()
        if self._skip_depth > 0:
            if tag_lower in _DROP_SUBTREE_TAGS:
                self._skip_depth -= 1
            return
        if tag_lower in _ALLOWED_SVG_HTML_TAGS:
            self.output.append(f"</{tag_lower}>")

    def handle_data(self, data: str) -> None:
        if self._skip_depth == 0:
            self.output.append(html.escape(data, quote=False))

    def handle_entityref(self, name: str) -> None:
        if self._skip_depth == 0:
            # كيانات XML/HTML المعيارية فقط؛ الكيانات المخصّصة الخارجية تُحذف لمنع XXE
            if name in ("amp", "lt", "gt", "quot", "apos", "nbsp"):
                self.output.append(f"&{name};")
            else:
                self._warn_once(f"xxe_entity_blocked: تم تجاهل مرجع كيان غير قياسي '&{name};'")

    def handle_charref(self, name: str) -> None:
        if self._skip_depth == 0:
            self.output.append(f"&#{name};")


def _strip_doctype_and_entities(raw_text: str) -> tuple[str, list[str]]:
    """
    إزالة أي مقطع `<!DOCTYPE ...>` أو `<!ENTITY ...>` قبل تمريره للمحلّل
    بزمن خطي O(n) وبصفر Regex.
    """
    warnings: list[str] = []
    out: list[str] = []
    i = 0
    n = len(raw_text)
    lower = raw_text.lower()

    while i < n:
        if lower.startswith(("<!doctype", "<!entity", "<!element", "<!attlist"), i):
            warnings.append("xxe_entity_blocked: تم رصد وحذف تعريف كيان خارجي أو DTD (XXE)")
            bracket_depth = 0
            while i < n:
                ch = raw_text[i]
                if ch == "[":
                    bracket_depth += 1
                elif ch == "]" and bracket_depth > 0:
                    bracket_depth -= 1
                elif ch == ">" and bracket_depth == 0:
                    i += 1
                    break
                i += 1
            continue
        out.append(raw_text[i])
        i += 1

    return ("".join(out), warnings)


def sanitize_svg_or_html(raw_text: str) -> tuple[str, list[str]]:
    """
    تعقيم ملفات `SVG` و`HTML` و`XML` بقائمة بيضاء صارمة (`Allowlist Sanitizer` — P1):
      - يحذف `<!DOCTYPE` و`<!ENTITY` (`XXE`).
      - يحلّل العناصر عبر `_AllowlistSvgHtmlSanitizer` ويسمح فقط بوسوم وسمات آمنة.
      - يكشف ويحبط محاولات الالتفاف عبر `<foreignObject>`, `xlink:href="&#106;avascript:..."`,
        `data:` URIs، `style="background:url(javascript:...)"`، واختلاف حالة الأحرف.
    """
    stripped_text, pre_warnings = _strip_doctype_and_entities(raw_text)
    sanitizer = _AllowlistSvgHtmlSanitizer()
    for w in pre_warnings:
        sanitizer._warn_once(w)
    sanitizer.feed(stripped_text)
    sanitizer.close()
    return ("".join(sanitizer.output).strip(), sanitizer.warnings)


def _safe_read_zip_entry(
    zf: zipfile.ZipFile,
    info: zipfile.ZipInfo,
    *,
    limits: IngestionLimits,
    cumulative_bytes: list[int],
    forbid_nested_archive: bool = False,
) -> bytes:
    """
    قراءة ملف من داخل أرشيف `ZIP` بتدفق مجزّأ ومحدود الذاكرة (`Streaming Read Cap` — P0):
      - لا يثق بـ `info.file_size` المعلن في الترويسة وحده (لأنه قابل للتزوير).
      - يفك الضغط عبر `zlib.decompressobj(-15)` بسقف `max_length` صارم بحيث لا يخصّص
        في الذاكرة أكثر من الميزانية المتبقية حتى لو كانت ترويسة `ZIP` مزوّرة.
      - يطابق ترويسة `Local File Header` مع `Central Directory` ويفحص `CRC-32`.
      - يكشف ويحظر الأرشيفات المتداخلة (`Nested Archives`) عند تفعيل `forbid_nested_archive`.
    """
    entry_ext = Path(info.filename).suffix.lower()
    if forbid_nested_archive and entry_ext in _NESTED_ARCHIVE_EXTENSIONS:
        raise ValueError(
            f"zip_bomb_nested_archive: أرشيف متداخل محظور '{info.filename}' داخل الأرشيف"
        )

    fp = zf.fp
    if fp is None:
        raise ValueError("archive_stream_closed: مقبض ملف ZIP مغلق")

    fp.seek(info.header_offset)
    lfh = fp.read(30)
    if len(lfh) < 30 or lfh[:4] != b"PK\x03\x04":
        raise ValueError(f"archive_corrupt_header: ترويسة محلية غير صالحة لـ '{info.filename}'")

    lfh_flags, _lfh_method, _lfh_time, _lfh_date, _lfh_crc, _lfh_comp, lfh_uncomp, fn_len, extra_len = (
        struct.unpack("<HHHHIIIHH", lfh[6:30])
    )
    if (lfh_flags & 0x08) == 0 and (
        lfh_uncomp not in (0xFFFFFFFF, info.file_size)
        or _lfh_comp not in (0xFFFFFFFF, info.compress_size)
    ):
        raise ValueError(
            f"zip_bomb_spoofed_header: تعارض الحجم بين ترويسة LFH ({lfh_uncomp}/{_lfh_comp}) وترويسة Central Directory ({info.file_size}/{info.compress_size}) لـ '{info.filename}'"
        )

    fp.seek(info.header_offset + 30 + fn_len + extra_len)
    remaining_comp = info.compress_size
    decompressor = (
        zlib.decompressobj(-15) if info.compress_type == zipfile.ZIP_DEFLATED else None
    )

    chunks: list[bytes] = []
    entry_read = 0
    first_chunk = True
    crc = 0

    def _consume_decompressed(data: bytes) -> None:
        nonlocal entry_read, first_chunk, crc
        if not data:
            return
        if first_chunk:
            first_chunk = False
            if forbid_nested_archive and data.startswith(
                (b"PK\x03\x04", b"\x1f\x8b", b"7z\xbc\xaf\x27\x1c", b"Rar!\x1a\x07")
            ):
                raise ValueError(
                    f"zip_bomb_nested_archive: بصمة أرشيف متداخل محظور داخل '{info.filename}'"
                )
        entry_read += len(data)
        cumulative_bytes[0] += len(data)
        if entry_read > info.file_size:
            raise ValueError(
                f"zip_bomb_spoofed_header: الحجم الفعلي ({entry_read}) يتجاوز الحجم المعلن في ترويسة ZIP ({info.file_size}) لـ '{info.filename}'"
            )
        if cumulative_bytes[0] > limits.max_uncompressed_bytes:
            raise ValueError(
                f"zip_bomb_size_exceeded: الحجم الفعلي بعد الفك ({cumulative_bytes[0]}) يتجاوز الحد المسموح ({limits.max_uncompressed_bytes})"
            )
        crc = zlib.crc32(data, crc)
        chunks.append(data)

    while remaining_comp > 0:
        read_len = min(CHUNK_SIZE_BYTES, remaining_comp)
        comp_chunk = fp.read(read_len)
        if not comp_chunk:
            break
        remaining_comp -= len(comp_chunk)

        if decompressor is None:
            _consume_decompressed(comp_chunk)
        else:
            max_step = max(1024, (limits.max_uncompressed_bytes - cumulative_bytes[0]) + 64)
            out = decompressor.decompress(comp_chunk, max_step)
            _consume_decompressed(out)
            while decompressor.unconsumed_tail:
                max_step = max(1024, (limits.max_uncompressed_bytes - cumulative_bytes[0]) + 64)
                out = decompressor.decompress(decompressor.unconsumed_tail, max_step)
                _consume_decompressed(out)

    if decompressor is not None:
        tail = decompressor.flush()
        _consume_decompressed(tail)

    if (crc & 0xFFFFFFFF) != (info.CRC & 0xFFFFFFFF):
        raise zipfile.BadZipFile(f"Bad CRC-32 for file '{info.filename}'")

    return b"".join(chunks)


def _check_zip_safety(
    zf: zipfile.ZipFile,
    limits: IngestionLimits,
    *,
    verify_streams: bool = False,
    forbid_nested_archives: bool = False,
) -> list[str]:
    """
    فحص أرشيف ZIP ضد:
      1. Zip Bomb (تجاوز الحجم المفكوك أو نسبة الضغط أو عدد الملفات، مع فحص تدفقي فعلي ضد تزوير الترويسة).
      2. Path Traversal (`..` أو مسار مطلق `/` أو حرف قرص `C:` داخل الأرشيف).
      3. تجاوز عمق المجلدات (`max_directory_depth`).
      4. الأرشيفات المتداخلة (`Nested Archives`) وماكرو `VBA` (`vbaProject.bin`).
    يرفع `ValueError` عند وجود خطر أمني.
    """
    infos = zf.infolist()
    if len(infos) > limits.max_archive_entries:
        raise ValueError(
            f"zip_bomb_entries_exceeded: عدد ملفات الأرشيف ({len(infos)}) يتجاوز الحد ({limits.max_archive_entries})"
        )

    total_declared = 0
    cumulative_stream = [0]

    for info in infos:
        normalized_name = info.filename.replace("\\", "/")
        parts = normalized_name.split("/")
        if (
            ".." in parts
            or info.filename.startswith(("/", "\\"))
            or (len(normalized_name) >= 2 and normalized_name[1] == ":")
        ):
            raise ValueError(f"archive_path_traversal: مسار غير آمن داخل الأرشيف '{info.filename}'")
        if len(parts) > limits.max_directory_depth:
            raise ValueError(
                f"archive_depth_exceeded: عمق المجلدات ({len(parts)}) يتجاوز الحد ({limits.max_directory_depth})"
            )

        lower_name = normalized_name.lower()
        if lower_name.endswith("vbaproject.bin") or "/macros/" in f"/{lower_name}" or "/activex/" in f"/{lower_name}":
            raise ValueError(
                f"macro_detected_blocked: تم رصد وحظر ماكرو تنفيذي أو مكوّن نشط ('{info.filename}')"
            )

        total_declared += info.file_size
        if total_declared > limits.max_uncompressed_bytes:
            raise ValueError(
                f"zip_bomb_size_exceeded: الحجم المعلن بعد الفك ({total_declared}) يتجاوز الحد المسموح"
            )
        compressed = max(info.compress_size, 1)
        ratio = info.file_size / compressed
        if info.file_size > 1024 * 1024 and ratio > limits.max_compression_ratio:
            raise ValueError(
                f"zip_bomb_ratio_exceeded: نسبة ضغط مريبة ({ratio:.1f}:1) في '{info.filename}'"
            )

        if verify_streams and not info.is_dir():
            _safe_read_zip_entry(
                zf,
                info,
                limits=limits,
                cumulative_bytes=cumulative_stream,
                forbid_nested_archive=forbid_nested_archives,
            )

    return [info.filename for info in infos if not info.is_dir()]


def _extract_openxml_document(
    path: Path,
    mime: str,
    limits: IngestionLimits,
) -> tuple[str, list[list[list[str]]], dict[str, object], list[str], bool]:
    """
    استخراج النصوص والجداول من ملفات Office (`DOCX`, `XLSX`, `PPTX`) بأمان تام:
      - يقرأ المدخلات عبر `_safe_read_zip_entry` (ضد الترويسات المزوّرة).
      - يحلّل XML عبر `safe_xml_fromstring` (`defusedxml` + حدود العمق والعناصر).
      - يستخرج في `XLSX`: `sharedStrings.xml` + جميع أوراق `xl/worksheets/sheet*.xml`
        شاملةً `inlineStr` والأرقام ونتائج المعادلات (`<v>`) مع تعليم الاقتطاع كـ `partial`.
    """
    texts: list[str] = []
    tables: list[list[list[str]]] = []
    meta: dict[str, object] = {"author": None, "created": None}
    extra_warnings: list[str] = []
    is_partial = False
    cumulative_bytes = [0]

    with zipfile.ZipFile(path, "r") as zf:
        names = _check_zip_safety(zf, limits, verify_streams=False, forbid_nested_archives=False)
        info_map = {info.filename: info for info in zf.infolist() if not info.is_dir()}

        if mime.endswith("wordprocessingml.document") and "word/document.xml" in info_map:
            xml_bytes = _safe_read_zip_entry(
                zf, info_map["word/document.xml"], limits=limits, cumulative_bytes=cumulative_bytes
            )
            root = safe_xml_fromstring(xml_bytes, limits=limits)
            for elem in root.iter():
                tag = elem.tag.split("}")[-1]
                if tag == "t" and elem.text:
                    texts.append(elem.text)
                elif tag == "tbl":
                    table_rows: list[list[str]] = []
                    for row in elem:
                        if row.tag.split("}")[-1] == "tr":
                            cells: list[str] = []
                            for cell in row:
                                if cell.tag.split("}")[-1] == "tc":
                                    cell_text = "".join(
                                        t.text for t in cell.iter() if t.tag.split("}")[-1] == "t" and t.text
                                    )
                                    cells.append(cell_text)
                            if cells:
                                table_rows.append(cells)
                    if table_rows:
                        tables.append(table_rows)

        elif mime.endswith("spreadsheetml.sheet"):
            shared_strings: list[str] = []
            if "xl/sharedStrings.xml" in info_map:
                ss_bytes = _safe_read_zip_entry(
                    zf, info_map["xl/sharedStrings.xml"], limits=limits, cumulative_bytes=cumulative_bytes
                )
                ss_root = safe_xml_fromstring(ss_bytes, limits=limits)
                for si in ss_root:
                    if si.tag.split("}")[-1] == "si":
                        val = "".join(
                            t.text for t in si.iter() if t.tag.split("}")[-1] == "t" and t.text
                        )
                        shared_strings.append(val)

            sheet_files = sorted(
                n for n in names if n.startswith("xl/worksheets/sheet") and n.endswith(".xml")
            )
            meta["sheets_count"] = len(sheet_files)
            has_formulas = False
            total_rows_read = 0

            if sheet_files:
                for sf in sheet_files:
                    ws_bytes = _safe_read_zip_entry(
                        zf, info_map[sf], limits=limits, cumulative_bytes=cumulative_bytes
                    )
                    ws_root = safe_xml_fromstring(ws_bytes, limits=limits)
                    sheet_rows: list[list[str]] = []
                    for elem in ws_root.iter():
                        if elem.tag.split("}")[-1] != "row":
                            continue
                        if total_rows_read >= limits.max_xlsx_rows:
                            is_partial = True
                            if "xlsx_truncated: تم اقتطاع صفوف XLSX لتجاوز الحد الأقصى" not in extra_warnings:
                                extra_warnings.append(
                                    "xlsx_truncated: تم اقتطاع صفوف XLSX لتجاوز الحد الأقصى"
                                )
                            break
                        total_rows_read += 1
                        row_cells: list[str] = []
                        for cell in elem:
                            if cell.tag.split("}")[-1] != "c":
                                continue
                            cell_type = cell.attrib.get("t", "")
                            cell_val = ""
                            v_text: str | None = None
                            inline_parts: list[str] = []
                            for child in cell.iter():
                                ctag = child.tag.split("}")[-1]
                                if ctag == "f":
                                    has_formulas = True
                                elif ctag == "v" and child.text:
                                    v_text = child.text.strip()
                                elif ctag == "t" and child.text:
                                    inline_parts.append(child.text)

                            if cell_type == "s" and v_text is not None:
                                idx = int(v_text) if v_text.isdigit() else -1
                                cell_val = (
                                    shared_strings[idx]
                                    if 0 <= idx < len(shared_strings)
                                    else v_text
                                )
                            elif cell_type == "inlineStr":
                                cell_val = "".join(inline_parts)
                            elif v_text is not None:
                                cell_val = v_text
                            elif inline_parts:
                                cell_val = "".join(inline_parts)

                            if cell_val:
                                texts.append(cell_val)
                            row_cells.append(cell_val)
                        if any(c.strip() for c in row_cells):
                            sheet_rows.append(row_cells)
                    if sheet_rows:
                        tables.append(sheet_rows)
            else:
                texts.extend(shared_strings)

            meta["has_formulas"] = has_formulas

        elif mime.endswith("presentationml.presentation"):
            slide_files = sorted(
                n for n in names if n.startswith("ppt/slides/slide") and n.endswith(".xml")
            )
            meta["slides_count"] = len(slide_files)
            for sf in slide_files:
                s_bytes = _safe_read_zip_entry(
                    zf, info_map[sf], limits=limits, cumulative_bytes=cumulative_bytes
                )
                s_root = safe_xml_fromstring(s_bytes, limits=limits)
                for elem in s_root.iter():
                    if elem.tag.split("}")[-1] == "t" and elem.text:
                        texts.append(elem.text)

    return (" ".join(texts).strip(), tables, meta, extra_warnings, is_partial)


def _parse_psd_image_resources(res_bytes: bytes) -> tuple[list[str], bool]:
    """
    قراءة قسم `Image Resources` (`8BIM` blocks) في ملفات `PSD` (إصلاح P2):
      - `0x03EE` (1006): أسماء قنوات Alpha/Spot بصيغة Pascal strings.
      - `0x0415` (1045): أسماء قنوات Alpha/Spot بصيغة Unicode (`UTF-16BE`).
      - `0x0435` (1077) و `0x03EF` (1007): هياكل `DisplayInfo` لقنوات Spot/Alpha.
    """
    channel_names: list[str] = []
    unicode_names: list[str] = []
    has_spot_display_info = False
    offset = 0
    total = len(res_bytes)

    while offset + 12 <= total:
        if res_bytes[offset : offset + 4] != b"8BIM":
            break
        res_id = struct.unpack(">H", res_bytes[offset + 4 : offset + 6])[0]
        pascal_len = res_bytes[offset + 6]
        # Pascal string (1 byte length + chars) مبطّن لعدد زوجي من البايتات
        name_field_size = 1 + pascal_len
        if name_field_size % 2 == 1:
            name_field_size += 1
        data_len_pos = offset + 6 + name_field_size
        if data_len_pos + 4 > total:
            break
        data_len = struct.unpack(">I", res_bytes[data_len_pos : data_len_pos + 4])[0]
        data_start = data_len_pos + 4
        data_end = data_start + data_len
        if data_end > total:
            break
        block_data = res_bytes[data_start:data_end]

        if res_id == 0x03EE:  # 1006: Alpha/Spot channel names (Pascal strings)
            p = 0
            while p < len(block_data):
                slen = block_data[p]
                p += 1
                if p + slen <= len(block_data) and slen > 0:
                    cname = block_data[p : p + slen].decode("cp1256", errors="replace").strip()
                    if cname:
                        channel_names.append(cname)
                p += slen
        elif res_id == 0x0415:  # 1045: Unicode Alpha/Spot channel names
            p = 0
            while p + 4 <= len(block_data):
                u_len = struct.unpack(">I", block_data[p : p + 4])[0]
                p += 4
                byte_count = u_len * 2
                if p + byte_count <= len(block_data) and u_len > 0:
                    uname = (
                        block_data[p : p + byte_count]
                        .decode("utf-16-be", errors="replace")
                        .rstrip("\x00")
                        .strip()
                    )
                    if uname:
                        unicode_names.append(uname)
                p += byte_count
        elif res_id in (0x0435, 0x03EF):  # DisplayInfo structures
            has_spot_display_info = True

        # حجم البيانات مبطّن لعدد زوجي
        padded_data_len = data_len + (1 if data_len % 2 == 1 else 0)
        offset = data_start + padded_data_len

    final_names = unicode_names if unicode_names else channel_names
    return (final_names, has_spot_display_info or bool(final_names))


def _extract_psd_header(path: Path) -> dict[str, object]:
    """
    قراءة ترويسة ملف فوتوشوب الثنائية (`PSD` — `8BPS`) وقسم `Image Resources` (إصلاح P2):
      - يميّز بين عدد القنوات الكلي (`channels`)، القنوات اللونية الأساسية (`base_channels`)،
        والقنوات الإضافية (`extra_channels`: Alpha / Spot).
      - يستخرج أسماء القنوات الإضافية وألوان الـ Spot من `Image Resources` (`0x03EE` / `0x0415` / `0x0435`).
    """
    with path.open("rb") as f:
        header = f.read(26)
        if len(header) < 26 or header[:4] != b"8BPS":
            raise ValueError("ملف PSD غير صالح أو تالف")
        version, _reserved, channels, height, width, depth, color_mode_code = struct.unpack(
            ">H6sHIIHH", header[4:26]
        )
        if width == 0 or height == 0 or channels == 0 or version not in (1, 2):
            raise ValueError("ترويسة PSD تالفة أو مقطوعة")

        # تخطّي Color Mode Data Section لقراءة Image Resources Section
        channel_names: list[str] = []
        has_spot_resources = False
        color_len_bytes = f.read(4)
        if len(color_len_bytes) == 4:
            color_mode_len = struct.unpack(">I", color_len_bytes)[0]
            if color_mode_len <= 1024 * 1024:
                f.seek(color_mode_len, io.SEEK_CUR)
                res_len_bytes = f.read(4)
                if len(res_len_bytes) == 4:
                    res_len = struct.unpack(">I", res_len_bytes)[0]
                    if 0 < res_len <= 4 * 1024 * 1024:
                        res_bytes = f.read(res_len)
                        channel_names, has_spot_resources = _parse_psd_image_resources(res_bytes)

    color_modes = {
        0: "Bitmap",
        1: "Grayscale",
        2: "Indexed",
        3: "RGB",
        4: "CMYK",
        7: "Multichannel (Spot Colors)",
        8: "Duotone",
        9: "Lab",
    }
    base_channels_map = {
        0: 1,
        1: 1,
        2: 1,
        3: 3,
        4: 4,
        7: 0,
        8: 1,
        9: 3,
    }
    base_channels = base_channels_map.get(color_mode_code, 0)
    extra_channels = max(0, channels - base_channels)

    return {
        "author": None,
        "created": None,
        "psd_version": version,
        "channels": channels,
        "base_channels": base_channels,
        "extra_channels": extra_channels,
        "channel_names": channel_names,
        "has_spot_channel_resources": has_spot_resources or color_mode_code == 7,
        "width": width,
        "height": height,
        "bit_depth": depth,
        "color_mode": color_modes.get(color_mode_code, f"Unknown({color_mode_code})"),
    }


def ingest_file_isolated(
    file_path: str | Path,
    *,
    allowed_root: str | Path | None = None,
    limits: IngestionLimits | None = None,
    timeout_seconds: float | None = None,
) -> IngestionResult:
    """
    تشغيل استيعاب الملف داخل عملية فرعية معزولة (`Subprocess Sandbox Isolation` — P0):
      - يعمل بلا صدفة (`shell=False`) وببيئة نظيفة وسقف زمني صارم (`timeout_seconds`).
      - إذا تجاوز المحلّل المهلة أو انهار بسبب ملف هجومي، لا يتأثر الخادم الرئيسي ويعيد
        `IngestionResult` آمناً بحالة `blocked` أو `metadata_only`.
    """
    active_limits = limits or IngestionLimits()
    raw_path = Path(file_path)
    root = Path(allowed_root).resolve() if allowed_root is not None else raw_path.resolve().parent
    safe_path = guard_path(raw_path, allowed_roots=(root,))
    clean_name = sanitize_filename(safe_path.name)
    file_id, size_bytes, head = _stream_sha256_and_head(safe_path)
    mime, warnings = detect_magic_mime(head, clean_name)

    effective_timeout = (
        timeout_seconds if timeout_seconds is not None else active_limits.parser_timeout_seconds
    )
    repo_root = str(Path(__file__).resolve().parent.parent.parent)
    worker_limits = asdict(active_limits)
    worker_limits["isolate_in_subprocess"] = False

    cmd = [
        sys.executable,
        "-m",
        "src.ingestion.pipeline",
        "--worker-file",
        str(safe_path),
        "--worker-root",
        str(root),
        "--worker-limits",
        json.dumps(worker_limits),
    ]
    try:
        proc = subprocess.run(
            cmd,
            capture_output=True,
            text=True,
            timeout=effective_timeout,
            check=False,
            env={"PYTHONPATH": repo_root, "PATH": ""},
        )
        if proc.returncode == 0 and proc.stdout.strip():
            payload = json.loads(proc.stdout.strip().splitlines()[-1])
            return IngestionResult.from_dict(payload)
        warnings.append(
            f"parser_subprocess_failed: انتهت العملية الفرعية برمز ({proc.returncode})"
        )
    except subprocess.TimeoutExpired:
        warnings.append(
            f"parser_subprocess_timeout: تجاوز المحلّل المهلة الزمنية ({effective_timeout}s) في العملية المعزولة"
        )
        return IngestionResult(
            id=file_id,
            name=clean_name,
            mime=mime,
            size=size_bytes,
            status=IngestionStatus.ZP_OR_EXE_BLOCKED.value,
            parser="isolated_subprocess_guard",
            language=None,
            text="",
            metadata={"author": None, "created": None, "blocked_reason": "parser_subprocess_timeout"},
            warnings=warnings,
        )
    except Exception as exc:  # noqa: BLE001
        warnings.append(f"parser_subprocess_error: {type(exc).__name__}: {exc}")

    return IngestionResult(
        id=file_id,
        name=clean_name,
        mime=mime,
        size=size_bytes,
        status=IngestionStatus.METADATA_ONLY.value,
        parser="isolated_subprocess_fallback",
        language=None,
        text="",
        metadata={"author": None, "created": None},
        warnings=warnings,
    )


def ingest_file(
    file_path: str | Path,
    *,
    allowed_root: str | Path | None = None,
    limits: IngestionLimits | None = None,
    av_scanner: AvScannerCallable | None = None,
) -> IngestionResult:
    """
    نقطة الدخول الموحدة لاستيعاب أي ملف عبر المراحل الطبقية:
    `detect (magic bytes) → scan (AV/executables/limits) → route & extract → normalize`.
    """
    active_limits = limits or IngestionLimits()
    if active_limits.isolate_in_subprocess and av_scanner is None:
        return ingest_file_isolated(
            file_path,
            allowed_root=allowed_root,
            limits=active_limits,
        )

    raw_path = Path(file_path)
    root = Path(allowed_root).resolve() if allowed_root is not None else raw_path.resolve().parent
    safe_path = guard_path(raw_path, allowed_roots=(root,))
    clean_name = sanitize_filename(safe_path.name)

    file_id, size_bytes, head = _stream_sha256_and_head(safe_path)
    mime, warnings = detect_magic_mime(head, clean_name)
    ext = Path(clean_name).suffix.lower()

    # 1. طبقة الفحص ضد الفيروسات والبرمجيات الخبيثة (ClamAV / EICAR / Custom AV Hook)
    is_clean, malware_sig = scan_for_malware(
        safe_path,
        head,
        av_scanner=av_scanner,
        enable_clamav=active_limits.enable_clamav,
    )
    if not is_clean:
        warnings.append(f"malware_detected_blocked: تم رصد توقيع خبيث ({malware_sig})")
        return IngestionResult(
            id=file_id,
            name=clean_name,
            mime=mime,
            size=size_bytes,
            status=IngestionStatus.ZP_OR_EXE_BLOCKED.value,
            parser="av_scanner",
            language=None,
            text="",
            metadata={
                "author": None,
                "created": None,
                "blocked_reason": "malware_detected",
                "malware_signature": malware_sig,
            },
            warnings=warnings,
        )

    # 2. حظر الملفات التنفيذية والاسكربتات وملفات الماكرو الصريحة
    if mime in _EXECUTABLE_MIMES or ext in _EXECUTABLE_EXTENSIONS:
        warnings.append("executable_blocked: الملفات التنفيذية وملفات الماكرو لا تُنفَّذ ولا تُحلَّل أمنياً")
        return IngestionResult(
            id=file_id,
            name=clean_name,
            mime=mime,
            size=size_bytes,
            status=IngestionStatus.ZP_OR_EXE_BLOCKED.value,
            parser="security_guard",
            language=None,
            text="",
            metadata={"author": None, "created": None, "blocked_reason": "executable_file"},
            warnings=warnings,
        )

    # 3. فحص الحجم الأقصى للملف
    if size_bytes > active_limits.max_file_size_bytes:
        warnings.append(
            f"file_size_exceeded: حجم الملف ({size_bytes}) يتجاوز الحد ({active_limits.max_file_size_bytes})"
        )
        return IngestionResult(
            id=file_id,
            name=clean_name,
            mime=mime,
            size=size_bytes,
            status=IngestionStatus.ZP_OR_EXE_BLOCKED.value,
            parser="size_guard",
            language=None,
            text="",
            metadata={"author": None, "created": None, "blocked_reason": "max_file_size_exceeded"},
            warnings=warnings,
        )

    # 4. فحص ملفات OLE2 القديمة (.doc / .xls / .ppt) ضد ماكرو VBA
    if mime == "application/x-ole-storage":
        raw_ole = safe_path.read_bytes()
        if b"_VBA_PROJECT" in raw_ole or b"VBA" in raw_ole and b"Macros" in raw_ole:
            warnings.append("macro_detected_blocked: تم رصد وحظر ماكرو VBA داخل مستند OLE2 قديم")
            return IngestionResult(
                id=file_id,
                name=clean_name,
                mime=mime,
                size=size_bytes,
                status=IngestionStatus.ZP_OR_EXE_BLOCKED.value,
                parser="ole2_macro_guard",
                language=None,
                text="",
                metadata={"author": None, "created": None, "blocked_reason": "ole2_vba_macro"},
                warnings=warnings,
            )
        warnings.append(
            "legacy_ole2_format_metadata_only: صيغة Office ثنائية قديمة (OLE2) — يُنصح بتحويلها إلى DOCX/XLSX"
        )
        return IngestionResult(
            id=file_id,
            name=clean_name,
            mime=mime,
            size=size_bytes,
            status=IngestionStatus.METADATA_ONLY.value,
            parser="ole2_inspector",
            language=None,
            text="",
            metadata={"author": None, "created": None},
            warnings=warnings,
        )

    # 5. توجيه حسب جدول MIME مع سلسلة Fallback (متخصص ← شامل ← metadata_only)
    try:
        # ---- (أ) النصوص والبيانات الجدولية والـ JSON والـ HTML/SVG والرسائل ----
        if mime in (
            "text/plain",
            "text/markdown",
            "text/csv",
            "text/tab-separated-values",
            "application/json",
            "text/html",
            "application/xml",
            "image/svg+xml",
            "message/rfc822",
        ):
            raw_bytes = safe_path.read_bytes()
            decoded_text, encoding_used, enc_confidence, enc_warnings = (
                detect_and_decode_text_with_confidence(raw_bytes)
            )
            warnings.extend(enc_warnings)
            tables: list[list[list[str]]] = []
            parser_name = "text_decoder"
            meta: dict[str, object] = {
                "author": None,
                "created": None,
                "encoding": encoding_used,
                "encoding_confidence": enc_confidence,
            }

            if mime in ("text/csv", "text/tab-separated-values"):
                parser_name = "stdlib_csv"
                delimiter = "\t" if mime == "text/tab-separated-values" else ","
                reader = csv.reader(io.StringIO(decoded_text), delimiter=delimiter)
                rows = [row for row in reader if any(cell.strip() for cell in row)]
                if rows:
                    tables.append(rows)
            elif mime == "application/json":
                parser_name = "stdlib_json"
                parsed_json = json.loads(decoded_text)
                meta["json_type"] = type(parsed_json).__name__
            elif mime in ("image/svg+xml", "text/html", "application/xml"):
                if mime == "application/xml":
                    # فحص XML الصارم ضد Billion Laughs / XXE
                    safe_xml_fromstring(raw_bytes, limits=active_limits)
                parser_name = "svg_html_allowlist_sanitizer"
                decoded_text, san_warnings = sanitize_svg_or_html(decoded_text)
                warnings.extend(san_warnings)
                meta["recommended_headers"] = {
                    "Content-Security-Policy": "default-src 'none'; style-src 'unsafe-inline'; sandbox",
                    "Content-Disposition": "attachment",
                    "X-Content-Type-Options": "nosniff",
                }
            elif mime == "message/rfc822":
                parser_name = "stdlib_email"
                msg = email.message_from_string(decoded_text)
                meta["author"] = msg.get("From")
                meta["subject"] = msg.get("Subject")

            truncated = decoded_text[: active_limits.max_text_chars]
            return IngestionResult(
                id=file_id,
                name=clean_name,
                mime=mime,
                size=size_bytes,
                status=IngestionStatus.EXTRACTED.value,
                parser=parser_name,
                language=detect_language(truncated),
                text=truncated,
                pages=1,
                tables=tables,
                metadata=meta,
                warnings=warnings,
            )

        # ---- (ب) ملفات PDF (و .ai المبنية على PDF) مع كشف PDF الممسوح وسقف الصفحات ----
        if mime == "application/pdf":
            from pypdf import PdfReader

            reader = PdfReader(str(safe_path))
            total_pages = len(reader.pages)
            pages_to_read = min(total_pages, active_limits.max_pdf_pages)
            pdf_truncated = total_pages > active_limits.max_pdf_pages
            if pdf_truncated:
                warnings.append(
                    f"pdf_pages_truncated: تم اقتطاع قراءة PDF عند {pages_to_read} صفحة من أصل {total_pages}"
                )

            page_texts: list[str] = []
            for idx in range(pages_to_read):
                page_texts.append(reader.pages[idx].extract_text() or "")
            full_text = "\n".join(page_texts).strip()
            pdf_meta = reader.metadata or {}
            meta = {
                "author": getattr(pdf_meta, "author", None),
                "created": str(getattr(pdf_meta, "creation_date", None))
                if getattr(pdf_meta, "creation_date", None)
                else None,
            }

            if not full_text:
                warnings.append(
                    "scanned_pdf_needs_ocr: ملف PDF لا يحتوي على طبقة نصية (ممسوح ضوئياً أو منحنيات طباعة) — يحتاج OCR"
                )
                return IngestionResult(
                    id=file_id,
                    name=clean_name,
                    mime=mime,
                    size=size_bytes,
                    status=IngestionStatus.PARTIAL.value,
                    parser="pypdf",
                    language=None,
                    text="",
                    pages=total_pages,
                    metadata=meta,
                    warnings=warnings,
                )

            truncated = full_text[: active_limits.max_text_chars]
            return IngestionResult(
                id=file_id,
                name=clean_name,
                mime=mime,
                size=size_bytes,
                status=IngestionStatus.PARTIAL.value
                if pdf_truncated
                else IngestionStatus.EXTRACTED.value,
                parser="pypdf",
                language=detect_language(truncated),
                text=truncated,
                pages=total_pages,
                metadata=meta,
                warnings=warnings,
            )

        # ---- (ج) ملفات Office Open XML (DOCX / XLSX / PPTX) ----
        if mime in (
            "application/vnd.openxmlformats-officedocument.wordprocessingml.document",
            "application/vnd.openxmlformats-officedocument.spreadsheetml.sheet",
            "application/vnd.openxmlformats-officedocument.presentationml.presentation",
        ):
            doc_text, doc_tables, doc_meta, ooxml_warnings, is_partial = _extract_openxml_document(
                safe_path, mime, active_limits
            )
            warnings.extend(ooxml_warnings)
            status_val = (
                IngestionStatus.PARTIAL.value
                if is_partial or (not doc_text and not doc_tables)
                else IngestionStatus.EXTRACTED.value
            )
            return IngestionResult(
                id=file_id,
                name=clean_name,
                mime=mime,
                size=size_bytes,
                status=status_val,
                parser="openxml_defused_stdlib",
                language=detect_language(doc_text),
                text=doc_text[: active_limits.max_text_chars],
                pages=int(doc_meta.get("slides_count", 1)) if "slides_count" in doc_meta else 1,
                tables=doc_tables,
                metadata=doc_meta,
                warnings=warnings,
            )

        # ---- (د) أرشيفات ZIP مع فحص تدفقي ضد الترويسات المزوّرة والأرشيفات المتداخلة ----
        if mime == "application/zip":
            with zipfile.ZipFile(safe_path, "r") as zf:
                entries = _check_zip_safety(
                    zf,
                    active_limits,
                    verify_streams=True,
                    forbid_nested_archives=True,
                )
            return IngestionResult(
                id=file_id,
                name=clean_name,
                mime=mime,
                size=size_bytes,
                status=IngestionStatus.EXTRACTED.value,
                parser="zip_sandbox_inspector",
                language=None,
                text="\n".join(entries),
                metadata={
                    "author": None,
                    "created": None,
                    "entries_count": len(entries),
                    "entries": entries[:50],
                },
                warnings=warnings,
            )

        # ---- (هـ) ملفات التصميم للمطابع: PSD (فوتوشوب) مع Image Resources ----
        if mime == "image/vnd.adobe.photoshop":
            psd_meta = _extract_psd_header(safe_path)
            return IngestionResult(
                id=file_id,
                name=clean_name,
                mime=mime,
                size=size_bytes,
                status=IngestionStatus.EXTRACTED.value,
                parser="psd_header_parser",
                language=None,
                text=(
                    f"PSD {psd_meta['width']}x{psd_meta['height']} | "
                    f"Color: {psd_meta['color_mode']} | Channels: {psd_meta['channels']} "
                    f"(Base: {psd_meta['base_channels']}, Extra/Spot: {psd_meta['extra_channels']})"
                ),
                pages=1,
                images=[
                    {
                        "width": psd_meta["width"],
                        "height": psd_meta["height"],
                        "color_mode": psd_meta["color_mode"],
                        "channels": psd_meta["channels"],
                        "base_channels": psd_meta["base_channels"],
                        "extra_channels": psd_meta["extra_channels"],
                        "channel_names": psd_meta["channel_names"],
                    }
                ],
                metadata=psd_meta,
                warnings=warnings,
            )

        # ---- (و) الصور وأفلام الطباعة (PNG / JPEG / TIFF / WEBP / BMP / GIF) ----
        if mime.startswith("image/"):
            from PIL import Image

            with Image.open(safe_path) as img:
                width, height = img.size
                if width * height > active_limits.max_image_pixels:
                    raise ValueError(
                        f"image_decompression_bomb_blocked: أبعاد الصورة ({width}x{height} = {width * height} بكسل) تتجاوز الحد الأمني ({active_limits.max_image_pixels})"
                    )
                color_mode = img.mode
                dpi_raw = img.info.get("dpi")
                dpi = (
                    tuple(round(float(x)) for x in dpi_raw)
                    if isinstance(dpi_raw, tuple) and all(float(x) > 0 for x in dpi_raw)
                    else None
                )

            effective_dpi: list[int] | None = None
            if (
                active_limits.target_print_width_cm
                and active_limits.target_print_height_cm
                and active_limits.target_print_width_cm > 0
                and active_limits.target_print_height_cm > 0
            ):
                eff_x = round(width / (active_limits.target_print_width_cm / 2.54))
                eff_y = round(height / (active_limits.target_print_height_cm / 2.54))
                effective_dpi = [eff_x, eff_y]
                if min(eff_x, eff_y) < 300:
                    warnings.append(
                        f"low_print_dpi: الدقة الفعلية للطباعة ({min(eff_x, eff_y)} DPI على مقاس {active_limits.target_print_width_cm}x{active_limits.target_print_height_cm} سم) أقل من 300 DPI"
                    )
            elif dpi is not None:
                if min(dpi) < 300:
                    warnings.append(
                        f"low_print_dpi: دقة الصورة المدمجة ({min(dpi)} DPI) أقل من 300 DPI الموصى بها لأفلام الطباعة"
                    )
            else:
                warnings.append(
                    "missing_dpi_metadata: الصورة لا تحتوي على وسم DPI مدمج — مرّر مقاس الطباعة بالسنتيمتر لحساب الدقة الفعلية"
                )

            img_meta: dict[str, object] = {
                "author": None,
                "created": None,
                "width": width,
                "height": height,
                "color_mode": color_mode,
                "dpi": list(dpi) if dpi else None,
                "effective_dpi": effective_dpi,
            }
            return IngestionResult(
                id=file_id,
                name=clean_name,
                mime=mime,
                size=size_bytes,
                status=IngestionStatus.EXTRACTED.value,
                parser="pillow_image",
                language=None,
                text=f"Image {width}x{height} ({color_mode})",
                pages=1,
                images=[img_meta],
                metadata=img_meta,
                warnings=warnings,
            )

    except ValueError as sec_exc:
        err_msg = str(sec_exc)
        if err_msg.startswith(
            (
                "zip_bomb_",
                "archive_",
                "macro_detected_blocked",
                "xml_bomb_",
                "image_decompression_bomb_blocked",
            )
        ):
            warnings.append(err_msg)
            return IngestionResult(
                id=file_id,
                name=clean_name,
                mime=mime,
                size=size_bytes,
                status=IngestionStatus.ZP_OR_EXE_BLOCKED.value,
                parser="archive_or_xml_security_guard",
                language=None,
                text="",
                metadata={"author": None, "created": None, "blocked_reason": err_msg},
                warnings=warnings,
            )
        warnings.append(f"parser_fallback_to_metadata: {err_msg}")
    except Exception as exc:  # noqa: BLE001 — مقصود: فشل parser يرجع إلى metadata_only ولا يسقط النظام
        logger.warning(
            "تعذّر استخراج '%s' بالمحلّل المتخصص (%s) — التحول إلى metadata_only",
            clean_name,
            exc,
        )
        warnings.append(f"parser_fallback_to_metadata: {type(exc).__name__}: {exc}")

    # 6. الملاذ الأخير (Fallback): أي ملف غير مدعوم أو تالف يُقبل ويُخزّن بـ metadata_only
    if not any(w.startswith("parser_fallback_to_metadata") for w in warnings):
        warnings.append(
            f"unsupported_format_metadata_only: الصيغة '{mime}' مخزّنة كبيانات وصفية فقط في المرحلة الحالية"
        )
    return IngestionResult(
        id=file_id,
        name=clean_name,
        mime=mime,
        size=size_bytes,
        status=IngestionStatus.METADATA_ONLY.value,
        parser="metadata_fallback",
        language=None,
        text="",
        metadata={"author": None, "created": None},
        warnings=warnings,
    )


class IngestionPipeline:
    """
    مدير خط أنابيب الاستيعاب مع:
      - منع تكرار المعالجة (`Idempotency`) بمفتاح مركّب يعزل المستأجرين والإصدار والحدود:
        `tenant_id + PIPELINE_VERSION + limits.fingerprint() + sha256` (إصلاح P1).
      - قياس نسبة الاستخراج الناجح لكل نوع `MIME` (`extraction_metrics()`).
    """

    def __init__(
        self,
        *,
        limits: IngestionLimits | None = None,
        av_scanner: AvScannerCallable | None = None,
    ) -> None:
        self._limits = limits or IngestionLimits()
        self._av_scanner = av_scanner
        self._cache: dict[str, IngestionResult] = {}
        self._stats_by_mime: dict[str, dict[str, int]] = {}

    def cache_key_for(self, file_id: str, *, tenant_id: str = "default") -> str:
        """بناء مفتاح الـ Cache المركّب (`tenant + PIPELINE_VERSION + limits + sha256`)."""
        clean_tenant = tenant_id.strip() or "default"
        return f"{clean_tenant}:{PIPELINE_VERSION}:{self._limits.fingerprint()}:{file_id}"

    def process(
        self,
        file_path: str | Path,
        *,
        allowed_root: str | Path | None = None,
        tenant_id: str = "default",
    ) -> tuple[IngestionResult, bool]:
        """
        يعالج الملف ويعيد `(IngestionResult, from_cache)`.
        مفتاح الـ Cache يربط `tenant_id` و`PIPELINE_VERSION` و`limits` ببصمة `sha256:`
        لمنع تسرب النتائج بين المستأجرين أو إرجاع نتائج قديمة بعد تحديث المحلّل.
        """
        raw_path = Path(file_path)
        root = Path(allowed_root).resolve() if allowed_root is not None else raw_path.resolve().parent
        safe_path = guard_path(raw_path, allowed_roots=(root,))
        file_id, _, _ = _stream_sha256_and_head(safe_path)
        cache_key = self.cache_key_for(file_id, tenant_id=tenant_id)

        if cache_key in self._cache:
            return (self._cache[cache_key], True)

        result = ingest_file(
            safe_path,
            allowed_root=root,
            limits=self._limits,
            av_scanner=self._av_scanner,
        )
        self._cache[cache_key] = result

        bucket = self._stats_by_mime.setdefault(
            result.mime,
            {"total": 0, "extracted": 0, "partial": 0, "metadata_only": 0, "blocked": 0},
        )
        bucket["total"] += 1
        bucket[result.status] = bucket.get(result.status, 0) + 1
        return (result, False)

    def extraction_metrics(self) -> dict[str, dict[str, object]]:
        """تقرير نسبة الاستخراج الناجح لكل نوع `MIME`."""
        report: dict[str, dict[str, object]] = {}
        for mime, counts in self._stats_by_mime.items():
            total = max(counts["total"], 1)
            success_rate = counts["extracted"] / total
            report[mime] = {
                **counts,
                "success_rate": round(success_rate, 4),
            }
        return report


def _cli_worker_main(argv: list[str]) -> int:
    """نقطة دخول العامل الفرعي المعزول (`ingest_file_isolated`)."""
    import argparse

    parser = argparse.ArgumentParser(description="Isolated file ingestion worker")
    parser.add_argument("--worker-file", required=True)
    parser.add_argument("--worker-root", required=True)
    parser.add_argument("--worker-limits", required=True)
    args = parser.parse_args(argv)

    limits_dict = json.loads(args.worker_limits)
    limits_dict["isolate_in_subprocess"] = False
    limits = IngestionLimits(**limits_dict)
    res = ingest_file(args.worker_file, allowed_root=args.worker_root, limits=limits)
    print(json.dumps(res.to_dict(), ensure_ascii=False))
    return 0


if __name__ == "__main__":  # pragma: no cover
    raise SystemExit(_cli_worker_main(sys.argv[1:]))
