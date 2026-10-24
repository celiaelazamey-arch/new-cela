"""
خط أنابيب استيعاب واستخراج الملفات الطبقي الآمن (Layered File Ingestion Pipeline v2.1)
======================================================================================
يجسّد هذا المحرك المواصفة المعمارية والأمنية لاستيعاب الملفات (`docs/file-ingestion-pipeline.md`):

  `upload ← detect ← scan ← route ← extract ← normalize ← store ← index`

الضوابط الأمنية والهندسية المطبّقة (شاملة مراجعات P0 + P1 + P2 + منع الإيجابيات الكاذبة):
  1. **لا تثق بالامتداد — افحص البصمة الثنائية (`detect_magic_mime`):**
     يفحص أول 4096 بايت (`Magic Bytes`) لتحديد النوع الحقيقي، مع التمييز الذكي بين الاسكربتات
     التنفيذية (`bat/ps1/vbs/sh`) وبين الملفات النصية التقنية (`.txt/.md`) التي تذكر أوامر مثل
     `powershell` (تحذير `script_keyword_warning` بدلاً من الحظر الخاطئ).
  2. **حماية XML الصارمة (`safe_xml_fromstring` + `defusedxml` — P0):**
     يرفض `<!DOCTYPE` و`<!ENTITY` (`XXE` / `Billion Laughs`) ويفرض سقوفاً حيّة على الحجم والعمق والعناصر.
  3. **فك الضغط التدفقي المحدود (`_safe_read_zip_entry` — P0 + دعم Data Descriptors و OOXML Embeddings):**
     - يرفض صراحةً طرق الضغط غير القياسية (`bzip2`, `lzma`, `deflate64`) وحجوم `ZIP64` المفرطة.
     - يدعم `Data Descriptors` الشرعية في ملفات `Word / Excel / LibreOffice / Google Docs` (`lfh_uncomp == 0`).
     - يسمح بالمضمّنات الشرعية داخل `word/embeddings/*.xlsx` و`ppt/embeddings/*.xlsx` دون فكّها مع احتسابها في السقف،
       بينما يحظر الأرشيفات المتداخلة (`Nested Archives`) داخل ملفات `.zip` العادية.
     - يفك الضغط عبر `zlib.decompressobj(-15)` بسقف `max_length` صارم أثناء القراءة الفعلية لكشف الترويسات المزوّرة.
  4. **عزل العملية الفرعية المشدّد (`ingest_file_isolated` — P0):**
     - مفعّل افتراضياً في `IngestionPipeline` (`isolate_in_subprocess=True`).
     - يطبّق حدود موارد النظام (`resource.setrlimit`: `RLIMIT_AS`, `RLIMIT_CPU`, `RLIMIT_FSIZE`).
     - يعطّل الوصول إلى الشبكة داخل العامل المعزول (`_disable_worker_network` يغلق `socket.socket`).
     - يشغّل العامل في مجموعة عمليات مستقلة (`start_new_session=True`) وفي مجلد مؤقت معزول،
       ويقتل مجموعة العمليات بالكامل (`os.killpg SIGKILL`) عند انتهاء المهلة، ويتواصل بـ **JSON فقط (صفر pickle)**.
  5. **معقّم القائمة البيضاء المشدّد لـ `SVG/HTML` (`_AllowlistSvgHtmlSanitizer` — P1):**
     - يحذف `<script>`, `<foreignObject>`, `<use>`, `<image>`, `<animate>`, `<set>`, `<iframe>`, `<object>`, `<style>`.
     - يفك كيانات HTML (`html.unescape`) ومحارف التحكم قبل فحص الروابط، ويطبّق قائمة بيضاء لخصائص CSS في `style`.
  6. **مفتاح Cache معزول بالمستأجر والإصدار مع TTL وسقف حجم وحذف حسب المستأجر (`IngestionPipeline`).**
"""
from __future__ import annotations

import csv
import email
import errno
import hashlib
import html
import io
import json
import logging
import math
import os
import shutil
import signal
import socket
import stat
import struct
import subprocess
import sys
import tempfile
import time
import zipfile
import zlib
from collections.abc import Callable, Sequence
from contextlib import nullcontext
from dataclasses import asdict, dataclass, field
from enum import Enum
from html.parser import HTMLParser
from pathlib import Path
from xml.etree import ElementTree as ET

from src.core.path_guard import guard_path, sanitize_for_log

try:
    from defusedxml import ElementTree as DefusedET
except ImportError:  # pragma: no cover
    DefusedET = None  # type: ignore[assignment]

try:
    from charset_normalizer import from_bytes as cn_from_bytes
except ImportError:  # pragma: no cover
    cn_from_bytes = None  # type: ignore[assignment]

logger = logging.getLogger(__name__)

PIPELINE_VERSION = "2.1.0"
CHUNK_SIZE_BYTES = 65536
MAX_WORKER_RESULT_BYTES = 5 * 1024 * 1024
WORKER_OOM_EXIT = 75
WORKER_OUTPUT_EFBIG_EXIT = 76
MAX_WORKER_STDOUT_BYTES = MAX_WORKER_RESULT_BYTES  # compatibility alias
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
    """الحدود الأمنية الصارمة ضد استنزاف الذاكرة وقنابل الضغط (Zip/XML/Image/PSD Bombs)."""

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
    max_psd_resource_bytes: int = 4 * 1024 * 1024        # 4 MB سقف قسم موارد PSD
    max_psd_resource_blocks: int = 256                   # أقصى عدد بلوكات 8BIM في PSD
    worker_max_memory_mb: int = 512                      # سقف ذاكرة العملية الفرعية بالميجابايت
    parser_timeout_seconds: float = 15.0                 # مهلة العملية الفرعية المعزولة
    isolate_in_subprocess: bool = False                  # عزل اختياري على مستوى الدالة (مفعّل افتراضياً في IngestionPipeline)
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
        if not isinstance(data, dict):
            raise TypeError("worker_result_schema_invalid: expected object")
        required = {"id", "name", "mime", "size", "status", "parser", "text"}
        if not required.issubset(data):
            raise ValueError("worker_result_schema_invalid: missing required fields")
        if data.get("status") not in {status.value for status in IngestionStatus}:
            raise ValueError("worker_result_schema_invalid: unknown status")
        if type(data.get("size")) is not int or data["size"] < 0:
            raise ValueError("worker_result_schema_invalid: invalid size")
        if not all(isinstance(data.get(key), str) for key in ("id", "name", "mime", "parser", "text")):
            raise ValueError("worker_result_schema_invalid: invalid string field")
        if data.get("language") is not None and not isinstance(data.get("language"), str):
            raise ValueError("worker_result_schema_invalid: invalid language")
        if data.get("pages") is not None and (type(data.get("pages")) is not int or data["pages"] < 0):
            raise ValueError("worker_result_schema_invalid: invalid pages")
        if not isinstance(data.get("tables", []), list) or not isinstance(data.get("images", []), list):
            raise TypeError("worker_result_schema_invalid: invalid extraction arrays")
        if not isinstance(data.get("metadata", {"author": None, "created": None}), dict):
            raise TypeError("worker_result_schema_invalid: invalid metadata")
        if not isinstance(data.get("warnings", []), list) or not all(
            isinstance(warning, str) for warning in data.get("warnings", [])
        ):
            raise ValueError("worker_result_schema_invalid: invalid warnings")
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

_SAFE_DOCUMENT_TEXT_EXTENSIONS: frozenset[str] = frozenset(
    {".txt", ".md", ".csv", ".tsv", ".json", ".html", ".htm", ".xml", ".eml"}
)

_NESTED_ARCHIVE_EXTENSIONS: frozenset[str] = frozenset(
    {".zip", ".gz", ".tgz", ".tar", ".7z", ".rar", ".bz2", ".xz"}
)

_OOXML_EMBEDDING_PREFIXES: tuple[str, ...] = (
    "word/embeddings/",
    "ppt/embeddings/",
    "xl/embeddings/",
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
# ملاحظة: <use> و <image> و <animate> و <set> مستبعدة عمداً لمنع تحميل موارد خارجية أو حقن سمات متحرك
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
        "use",
        "image",
        "animate",
        "animatemotion",
        "animatetransform",
        "set",
        "mpath",
        "feimage",
        "audio",
        "video",
        "canvas",
        "form",
        "input",
        "button",
        "select",
        "textarea",
        "meta",
        "link",
        "base",
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
        "rel",
    }
)

_ALLOWED_CSS_PROPERTIES: frozenset[str] = frozenset(
    {
        "fill",
        "fill-opacity",
        "stroke",
        "stroke-width",
        "stroke-opacity",
        "opacity",
        "color",
        "font-size",
        "font-weight",
        "font-family",
        "text-anchor",
        "display",
        "visibility",
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


def _resolve_allowed_root(
    allowed_root: str | Path | None, *, allow_private_worker_root: bool = False
) -> Path:
    """Resolve a selected root and reject symlinks or roots broad enough to expose secrets."""
    if allowed_root is None:
        raise ValueError("allowed_root_required: explicit user-selected root is required")
    supplied_root = Path(allowed_root).expanduser()
    if supplied_root.is_symlink():
        raise ValueError("allowed_root_symlink_rejected")
    root = supplied_root.resolve(strict=True)
    if not root.is_dir():
        raise ValueError("allowed_root_not_directory")
    home = Path.home().resolve()
    system_tmp = Path(tempfile.gettempdir()).resolve()
    project_root = Path(__file__).resolve().parents[2]
    sensitive_roots = {
        Path(root.anchor), home, system_tmp, project_root,
        Path("/etc"), Path("/var"), Path("/proc"), Path("/sys"), Path("/dev"),
        home / ".ssh", home / ".gnupg", home / ".aws", project_root / ".git",
        project_root / "secrets",
    }
    trusted_worker_tmp = (
        allow_private_worker_root
        and root.name.startswith("cela_ingest_sbx_")
        and (os.name != "posix" or stat.S_IMODE(root.stat().st_mode) & 0o077 == 0)
    )
    if not trusted_worker_tmp and any(sensitive == root or sensitive.is_relative_to(root) for sensitive in sensitive_roots):
        raise ValueError("allowed_root_too_broad")
    return root


def _open_regular_no_follow(path: Path, flags: int, mode: int | None = None) -> int:
    """Open a regular file without following symlinks (with identity check on Windows)."""
    nofollow = getattr(os, "O_NOFOLLOW", 0)
    binary = getattr(os, "O_BINARY", 0)
    before = path.lstat() if not nofollow and not (flags & os.O_CREAT) else None
    if before is not None and (stat.S_ISLNK(before.st_mode) or not stat.S_ISREG(before.st_mode)):
        raise ValueError("path_not_regular_or_symlink")
    open_flags = flags | nofollow | binary
    fd = os.open(path, open_flags) if mode is None else os.open(path, open_flags, mode)
    try:
        after = os.fstat(fd)
        if not stat.S_ISREG(after.st_mode):
            raise ValueError("opened_path_not_regular")
        if before is not None and (before.st_dev, before.st_ino) != (after.st_dev, after.st_ino):
            raise ValueError("path_changed_during_open")
        return fd
    except Exception:
        os.close(fd)
        raise


class _StagedFileTooLarge(ValueError):
    """Raised as soon as streaming bytes exceed the configured file-size cap."""

    def __init__(self, file_id: str, size: int, head: bytes, limit: int) -> None:
        super().__init__("file_size_limit_exceeded")
        self.file_id = file_id
        self.size = size
        self.head = head
        self.limit = limit


def _copy_to_private_readonly(
    source: Path, destination: Path, *, max_bytes: int | None = None
) -> tuple[str, int, bytes]:
    """Copy/hash a no-follow regular file once, enforcing the cap on streamed bytes."""
    src_fd = _open_regular_no_follow(source, os.O_RDONLY)
    dst_fd: int | None = None
    hasher = hashlib.sha256()
    total_size = 0
    head = bytearray()
    try:
        source_stat = os.fstat(src_fd)
        if not stat.S_ISREG(source_stat.st_mode):
            raise ValueError("staged_source_not_regular_file")
        dst_fd = os.open(
            destination,
            os.O_WRONLY | os.O_CREAT | os.O_EXCL | getattr(os, "O_NOFOLLOW", 0) | getattr(os, "O_BINARY", 0),
            0o400,
        )
        while chunk := os.read(src_fd, CHUNK_SIZE_BYTES):
            exceeded_limit = max_bytes is not None and total_size + len(chunk) > max_bytes
            staged_chunk = chunk[: max(0, max_bytes - total_size)] if exceeded_limit else chunk
            if len(head) < 4096:
                head.extend(staged_chunk[: 4096 - len(head)])
            hasher.update(staged_chunk)
            total_size += len(staged_chunk)
            view = memoryview(staged_chunk)
            try:
                while view:
                    written = os.write(dst_fd, view)
                    view = view[written:]
            finally:
                view.release()
            if exceeded_limit:
                raise _StagedFileTooLarge(
                    f"sha256:{hasher.hexdigest()}", total_size, bytes(head), max_bytes
                )
        if hasattr(os, "fchmod"):
            os.fchmod(dst_fd, 0o400)
        else:  # pragma: no cover - platform fallback
            os.chmod(destination, 0o400)
        final_stat = os.fstat(src_fd)
        if (source_stat.st_size, source_stat.st_mtime_ns, source_stat.st_ctime_ns) != (
            final_stat.st_size, final_stat.st_mtime_ns, final_stat.st_ctime_ns
        ):
            raise ValueError("staged_source_changed_during_copy")
        return (f"sha256:{hasher.hexdigest()}", total_size, bytes(head))
    finally:
        os.close(src_fd)
        if dst_fd is not None:
            os.close(dst_fd)


def _read_worker_result(path: Path) -> bytes:
    """Open worker output without following symlinks and read at most cap + 1 bytes."""
    fd = _open_regular_no_follow(path, os.O_RDONLY)
    try:
        info = os.fstat(fd)
        if not stat.S_ISREG(info.st_mode) or info.st_size > MAX_WORKER_RESULT_BYTES:
            raise ValueError("worker_output_not_regular_or_too_large")
        chunks: list[bytes] = []
        remaining = MAX_WORKER_RESULT_BYTES + 1
        while remaining > 0:
            chunk = os.read(fd, min(CHUNK_SIZE_BYTES, remaining))
            if not chunk:
                break
            chunks.append(chunk)
            remaining -= len(chunk)
        payload = b"".join(chunks)
        if len(payload) > MAX_WORKER_RESULT_BYTES:
            raise ValueError("worker_output_too_large")
        return payload
    finally:
        os.close(fd)


def _validate_worker_json(payload: object) -> None:
    """Bound untrusted JSON nesting, key count, and string lengths after decoding."""
    if not isinstance(payload, dict):
        raise TypeError("worker_result_schema_invalid: top-level result must be an object")
    stack: list[tuple[object, int]] = [(payload, 0)]
    nodes = 0
    keys_seen = 0
    while stack:
        item, depth = stack.pop()
        nodes += 1
        if nodes > 50_000 or depth > 32:
            raise ValueError("worker_result_schema_invalid: JSON complexity limit exceeded")
        if isinstance(item, dict):
            keys_seen += len(item)
            if keys_seen > 10_000:
                raise ValueError("worker_result_schema_invalid: too many JSON keys")
            for key, value in item.items():
                if not isinstance(key, str) or len(key) > 256:
                    raise ValueError("worker_result_schema_invalid: invalid JSON key")
                stack.append((value, depth + 1))
        elif isinstance(item, list):
            stack.extend((value, depth + 1) for value in item)
        elif isinstance(item, str) and len(item) > 1_000_000:
            raise ValueError("worker_result_schema_invalid: JSON string too long")
        elif isinstance(item, float) and not math.isfinite(item):
            raise ValueError("worker_result_schema_invalid: non-finite JSON number")
        elif item is not None and not isinstance(item, (str, int, float, bool)):
            raise ValueError("worker_result_schema_invalid: unsupported JSON value")
    warnings = payload.get("warnings", [])
    if not isinstance(warnings, list) or len(warnings) > 1024 or any(
        not isinstance(warning, str) or len(warning) > 4096 for warning in warnings
    ):
        raise ValueError("worker_result_schema_invalid: warning limit exceeded")


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
    للنصوص العادية (`.txt/.md`) التي تذكر كلمات مثل `powershell`، يُصدر تحذير `script_keyword_warning`
    بدلاً من حظرها بالخطأ (منع False Positives).
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
        if lower_head.startswith(b"<svg") or (b"<svg" in lower_head and b"xmlns" in lower_head):
            magic_mime = "image/svg+xml"
        elif lower_head.startswith((b"<!doctype html", b"<html")):
            magic_mime = "text/html"
        elif lower_head.startswith(b"<?xml"):
            magic_mime = "application/xml"
        elif any(sig in lower_head for sig in _SCRIPT_SIGNATURES):
            if ext in _SAFE_DOCUMENT_TEXT_EXTENSIONS and expected_mime:
                warnings.append(
                    "script_keyword_warning: يحتوي الملف النصي على أوامر اسكربت/صدفة ولكن يُعالج كنص غير تنفيذي"
                )
                return (expected_mime, warnings)
            magic_mime = "application/x-executable-script"

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
      1. كشف بصمة `EICAR` القياسية.
      2. استدعاء `av_scanner` المخصّص إن جرى تمريره.
      3. استدعاء `clamdscan` (عبر daemon سريع إن وُجد) أو `clamscan` (بدون shell).
    """
    if EICAR_SIGNATURE in head:
        return (False, "EICAR-Test-Signature")

    if av_scanner is not None:
        return av_scanner(path)

    if enable_clamav:
        clam_bin = shutil.which("clamdscan") or shutil.which("clamscan")
        if clam_bin:
            proc = subprocess.run(
                [clam_bin, "--no-summary", str(path)],
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
    xml_bytes: bytes | memoryview,
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
    if isinstance(xml_bytes, memoryview):
        view = xml_bytes
        try:
            xml_bytes = bytes(view)
        finally:
            view.release()
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
        cn_boost = 0.0
        if cn_from_bytes is not None:
            best = cn_from_bytes(raw_bytes).best()
            if best is not None and best.encoding in ("cp1256", "windows_1256", "iso8859_6"):
                cn_boost = 0.15
        confidence = round(
            min(1.0, max(0.1, arabic_ratio * 0.85 + cn_boost - replacement_chars * 0.1)), 3
        )
        if arabic_ratio >= 0.35:
            warnings.append(
                f"legacy_encoding_detected: تم فك الترميز العربي عبر Windows-1256 (cp1256) بثقة {confidence}"
            )
            return (cp1256_text, "cp1256", confidence, warnings)

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
    return cleaned.replace("\\", "/").lower()


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


def _sanitize_css_style(raw_style: str) -> tuple[str, bool]:
    """
    تصفية خصائص `style` عبر قائمة بيضاء للخصائص (`_ALLOWED_CSS_PROPERTIES`)
    مع حظر `url(...)`, `expression(...)`, `javascript:`, `data:`, `@import`, وأي قوس أو محرف هروب.
    تعيد `(clean_style, had_violation)`.
    """
    unescaped = html.unescape(raw_style)
    norm = _normalize_uri_for_check(unescaped)
    forbidden = (
        "url(",
        "expression(",
        "javascript:",
        "vbscript:",
        "data:",
        "-moz-binding",
        "@import",
        "\\",
    )
    if any(marker in norm for marker in forbidden):
        return ("", True)

    safe_decls: list[str] = []
    had_violation = False
    for decl in unescaped.split(";"):
        decl_stripped = decl.strip()
        if not decl_stripped:
            continue
        if ":" not in decl_stripped:
            had_violation = True
            continue
        prop, val = decl_stripped.split(":", 1)
        prop_clean = prop.strip().lower()
        val_clean = val.strip()
        if prop_clean not in _ALLOWED_CSS_PROPERTIES:
            had_violation = True
            continue
        if any(bad in val_clean.lower() for bad in ("(", ")", "url", "expression", "javascript", "data", "@", "\\")):
            had_violation = True
            continue
        safe_decls.append(f"{prop_clean}: {val_clean}")

    return ("; ".join(safe_decls), had_violation)


class _AllowlistSvgHtmlSanitizer(HTMLParser):
    """
    محلّل ومُعقّم `SVG/HTML` يعتمد على القائمة البيضاء الصارمة (`Allowlist Sanitizer` — P1):
      - يحذف الوسوم غير المسموحة ومحتواها (`<script>`, `<foreignObject>`, `<use>`, `<image>`, `<animate>`, `<set>`, `<iframe>`, `<object>`, `<style>`, إلخ).
      - يحذف أي سمة تبدأ بـ `on*` أو غير مدرجة في `_ALLOWED_SVG_HTML_ATTRS`.
      - يفك كيانات HTML (`&#106;avascript:`) قبل التحقق من الروابط (`href`, `xlink:href`, `src`).
      - يطبّق قائمة بيضاء على خصائص CSS داخل `style`، ويضيف `rel="noopener noreferrer"` لوسوم `<a>`.
    """

    def __init__(self) -> None:
        super().__init__(convert_charrefs=False)
        self.output: list[str] = []
        self.warnings: list[str] = []
        self._skip_depth = 0
        self._open_tags: list[str] = []

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
        has_rel = False
        for attr_name, attr_val in attrs:
            name_lower = attr_name.lower()
            val_str = attr_val or ""
            if name_lower.startswith("on"):
                self._warn_once(f"xss_vector_sanitized: تم حذف سمة الحدث الخطيرة '{name_lower}='")
                continue
            if name_lower not in _ALLOWED_SVG_HTML_ATTRS:
                self._warn_once(f"xss_vector_sanitized: تم حذف السمة غير المسموحة '{name_lower}'")
                continue
            if name_lower in _URL_ATTRS:
                uri = _normalize_uri_for_check(val_str)
                if name_lower == "href" and tag_lower == "a":
                    allowed = _is_safe_uri(val_str) and (uri.startswith(("#", "http://", "https://", "mailto:")))
                else:
                    allowed = uri.startswith("#")
                if not allowed:
                    self._warn_once(f"xss_vector_sanitized: blocked URI in '{name_lower}'")
                    continue
            if name_lower == "id":
                val_str = "cela-" + "".join(ch for ch in val_str if ch.isalnum() or ch in "_-:. ")[:128]
            if name_lower == "style":
                clean_style, style_violation = _sanitize_css_style(val_str)
                if style_violation:
                    self._warn_once("xss_vector_sanitized: تم تحييد خاصية أو تعبير CSS غير آمن داخل 'style'")
                if not clean_style:
                    continue
                val_str = clean_style
            if name_lower == "rel":
                has_rel = True
                val_str = "noopener noreferrer"
            escaped_val = html.escape(html.unescape(val_str), quote=True)
            canonical_attr = {"viewbox": "viewBox", "preserveaspectratio": "preserveAspectRatio",
                              "gradientunits": "gradientUnits", "gradienttransform": "gradientTransform",
                              "text-anchor": "text-anchor", "dominant-baseline": "dominant-baseline"}.get(name_lower, name_lower)
            safe_attrs.append(f'{canonical_attr}="{escaped_val}"')

        if tag_lower == "a" and not has_rel:
            safe_attrs.append('rel="noopener noreferrer"')

        attr_suffix = (" " + " ".join(safe_attrs)) if safe_attrs else ""
        canonical_tag = {"lineargradient": "linearGradient", "radialgradient": "radialGradient", "clippath": "clipPath"}.get(tag_lower, tag_lower)
        self.output.append(f"<{canonical_tag}{attr_suffix}>")
        if tag_lower not in {"br", "hr"}:
            self._open_tags.append(tag_lower)

    def handle_endtag(self, tag: str) -> None:
        tag_lower = tag.lower()
        if self._skip_depth > 0:
            if tag_lower in _DROP_SUBTREE_TAGS:
                self._skip_depth -= 1
            return
        if tag_lower in _ALLOWED_SVG_HTML_TAGS and tag_lower not in {"br", "hr"} and tag_lower in self._open_tags:
            while self._open_tags and self._open_tags[-1] != tag_lower:
                opened = self._open_tags.pop()
                canonical = {"lineargradient": "linearGradient", "radialgradient": "radialGradient", "clippath": "clipPath"}.get(opened, opened)
                self.output.append(f"</{canonical}>")
            opened = self._open_tags.pop()
            canonical = {"lineargradient": "linearGradient", "radialgradient": "radialGradient", "clippath": "clipPath"}.get(opened, opened)
            self.output.append(f"</{canonical}>")

    def handle_data(self, data: str) -> None:
        if self._skip_depth == 0:
            self.output.append(html.escape(data, quote=False))

    def handle_entityref(self, name: str) -> None:
        if self._skip_depth == 0:
            if name in ("amp", "lt", "gt", "quot", "apos", "nbsp"):
                self.output.append(f"&{name};")
            else:
                self._warn_once(f"xxe_entity_blocked: تم تجاهل مرجع كيان غير قياسي '&{name};'")

    def handle_charref(self, name: str) -> None:
        if self._skip_depth == 0:
            self.output.append(f"&#{name};")


def _strip_doctype_and_entities(raw_text: str) -> tuple[str, list[str]]:
    """إزالة أي مقطع `<!DOCTYPE ...>` أو `<!ENTITY ...>` قبل تمريره للمحلّل بزمن خطي O(n) وبصفر Regex."""
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
      - يكشف ويحبط محاولات الالتفاف عبر `<foreignObject>`, `<use href>`, `<image href>`,
        `<animate>`, `<set>`, `xlink:href="&#106;avascript:..."`, `data:` URIs، و`style="background:url(...)"`.
    """
    stripped_text, pre_warnings = _strip_doctype_and_entities(raw_text)
    sanitizer = _AllowlistSvgHtmlSanitizer()
    for w in pre_warnings:
        sanitizer._warn_once(w)
    sanitizer.feed(stripped_text)
    sanitizer.close()
    while sanitizer._open_tags:
        opened = sanitizer._open_tags.pop()
        canonical = {"lineargradient": "linearGradient", "radialgradient": "radialGradient", "clippath": "clipPath"}.get(opened, opened)
        sanitizer.output.append(f"</{canonical}>")
    return ("".join(sanitizer.output).strip(), sanitizer.warnings)


def _safe_read_zip_entry(
    zf: zipfile.ZipFile,
    info: zipfile.ZipInfo,
    *,
    limits: IngestionLimits,
    cumulative_bytes: list[int],
    forbid_nested_archive: bool = False,
) -> memoryview:
    """
    قراءة ملف من داخل أرشيف `ZIP` بتدفق مجزّأ ومحدود الذاكرة (`Streaming Read Cap` — P0):
      1. يرفض طرق الضغط غير القياسية (`bzip2`, `lzma`, `deflate64`) وحجوم `ZIP64` المفرطة.
      2. يدعم `Data Descriptors` الشرعية في ملفات `Office / LibreOffice / Google Docs` (`lfh_uncomp == 0`)،
         مع كشف الترويسات المزوّرة عندما تذكر `LFH` حجماً غير صفري يتعارض مع `Central Directory`.
      3. كل خطوة فك لا تتجاوز `min(CHUNK_SIZE_BYTES, info.file_size - entry_read + 1)`؛ ويُجمع الناتج
         في `BytesIO` ويُعاد كـ `memoryview` لتجنّب نسخة `join/getvalue` إضافية.
      4. يكشف ويحظر الأرشيفات المتداخلة (`Nested Archives`) عند تفعيل `forbid_nested_archive`.
    """
    if info.compress_type not in (zipfile.ZIP_STORED, zipfile.ZIP_DEFLATED):
        raise ValueError(
            f"zip_unsupported_compression_method: طريقة ضغط غير مدعومة ({info.compress_type}) في '{info.filename}' — يُسمح بـ STORED و DEFLATED فقط"
        )
    if info.file_size >= 0xFFFFFFFF or info.compress_size >= 0xFFFFFFFF:
        raise ValueError(
            f"zip_bomb_zip64_blocked: حجم ملف ZIP64 ضخم غير مسموح به في '{info.filename}'"
        )

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

    lfh_flags, lfh_method, _lfh_time, _lfh_date, _lfh_crc, lfh_comp, lfh_uncomp, fn_len, extra_len = (
        struct.unpack("<HHHHIIIHH", lfh[6:30])
    )
    if lfh_method not in (zipfile.ZIP_STORED, zipfile.ZIP_DEFLATED):
        raise ValueError(
            f"zip_unsupported_compression_method: طريقة ضغط LFH غير مدعومة ({lfh_method}) في '{info.filename}'"
        )

    # دعم Data Descriptors الشرعية (Office/LibreOffice/Google Docs قد تكتب 0 في LFH وتضع الحجم في Central Directory)
    has_data_descriptor = bool(lfh_flags & 0x08)
    if not has_data_descriptor and (
        lfh_uncomp not in (0, 0xFFFFFFFF, info.file_size)
        or lfh_comp not in (0, 0xFFFFFFFF, info.compress_size)
    ):
        raise ValueError(
            f"zip_bomb_spoofed_header: تعارض الحجم بين ترويسة LFH ({lfh_uncomp}/{lfh_comp}) وترويسة Central Directory ({info.file_size}/{info.compress_size}) لـ '{info.filename}'"
        )

    fp.seek(info.header_offset + 30 + fn_len + extra_len)
    remaining_comp = info.compress_size
    decompressor = (
        zlib.decompressobj(-15) if info.compress_type == zipfile.ZIP_DEFLATED else None
    )

    output = io.BytesIO()
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
                (b"PK\x03\x04", b"\x1f\x8b", b"7z\xbc\xaf\x27\x1c", b"Rar!\x1a\x07", b"BZh", b"\xfd7zXZ", b"(\xb5/\xfd")
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
        output.write(data)

    while remaining_comp > 0:
        read_len = min(CHUNK_SIZE_BYTES, remaining_comp)
        comp_chunk = fp.read(read_len)
        if not comp_chunk:
            break
        remaining_comp -= len(comp_chunk)

        if decompressor is None:
            _consume_decompressed(comp_chunk)
        else:
            max_step = max(1, min(CHUNK_SIZE_BYTES, info.file_size - entry_read + 1))
            out = decompressor.decompress(comp_chunk, max_step)
            _consume_decompressed(out)
            while decompressor.unconsumed_tail:
                max_step = max(1, min(CHUNK_SIZE_BYTES, info.file_size - entry_read + 1))
                out = decompressor.decompress(decompressor.unconsumed_tail, max_step)
                _consume_decompressed(out)

    if remaining_comp != 0 or entry_read != info.file_size:
        raise zipfile.BadZipFile("archive_entry_size_mismatch")
    if decompressor is not None and not decompressor.eof:
        raise zipfile.BadZipFile("archive_entry_deflate_stream_incomplete")
    if (crc & 0xFFFFFFFF) != (info.CRC & 0xFFFFFFFF):
        raise zipfile.BadZipFile("archive_entry_crc_mismatch")

    return output.getbuffer()


def _check_zip_safety(
    zf: zipfile.ZipFile,
    limits: IngestionLimits,
    *,
    verify_streams: bool = False,
    forbid_nested_archives: bool = False,
    allow_ooxml_embeddings: bool = False,
) -> tuple[list[str], list[str]]:
    """
    فحص أرشيف ZIP ضد:
      1. طرق الضغط غير القياسية (`bzip2`, `lzma`, `deflate64`).
      2. Zip Bomb (تجاوز الحجم المفكوك أو نسبة الضغط أو عدد الملفات، مع فحص تدفقي فعلي ضد تزوير الترويسة).
      3. Path Traversal (`..` أو مسار مطلق `/` أو حرف قرص `C:` داخل الأرشيف).
      4. تجاوز عمق المجلدات (`max_directory_depth`).
      5. الأرشيفات المتداخلة (`Nested Archives`) وماكرو `VBA` (`vbaProject.bin`).
         ملاحظة: عند `allow_ooxml_embeddings=True`، يُسمح بوجود ملفات مضمّنة شرعية داخل
         `word/embeddings/*.xlsx` أو `ppt/embeddings/*.xlsx` دون فكّها مع احتساب حجمها في السقف.
    """
    infos = zf.infolist()
    if len(infos) > limits.max_archive_entries:
        raise ValueError(
            f"zip_bomb_entries_exceeded: عدد ملفات الأرشيف ({len(infos)}) يتجاوز الحد ({limits.max_archive_entries})"
        )

    total_declared = 0
    cumulative_stream = [0]
    archive_warnings: list[str] = []
    seen_names: set[str] = set()

    for info in infos:
        normalized_name = info.filename.replace("\\", "/")
        normalized_key = normalized_name.casefold()
        if normalized_key in seen_names:
            raise ValueError("archive_duplicate_entry: duplicate normalized entry name")
        seen_names.add(normalized_key)
        if info.flag_bits & 0x1:
            raise ValueError("zip_encrypted_entry: encrypted archive entries are not accepted")
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

        if info.compress_type not in (zipfile.ZIP_STORED, zipfile.ZIP_DEFLATED):
            raise ValueError(
                f"zip_unsupported_compression_method: طريقة ضغط غير مدعومة ({info.compress_type}) في '{info.filename}'"
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

        is_ooxml_embedding = allow_ooxml_embeddings and lower_name.startswith(_OOXML_EMBEDDING_PREFIXES)
        if is_ooxml_embedding:
            cumulative_stream[0] += info.file_size
            archive_warnings.append(
                f"ooxml_embedded_object_skipped: تم تخطي فك الكائن المضمّن '{info.filename}' مع احتساب حجمه"
            )
            continue

        if verify_streams and not info.is_dir():
            _safe_read_zip_entry(
                zf,
                info,
                limits=limits,
                cumulative_bytes=cumulative_stream,
                forbid_nested_archive=forbid_nested_archives,
            )

    return ([info.filename for info in infos if not info.is_dir()], archive_warnings)


def _extract_openxml_document(
    path: Path,
    mime: str,
    limits: IngestionLimits,
) -> tuple[str, list[list[list[str]]], dict[str, object], list[str], bool]:
    """
    استخراج النصوص والجداول من ملفات Office (`DOCX`, `XLSX`, `PPTX`) بأمان تام:
      - يدعم `Data Descriptors` الشرعية والمضمّنات (`word/embeddings/*.xlsx`) دون إيجابيات كاذبة.
      - يقرأ المدخلات عبر `_safe_read_zip_entry` ويحلّل XML عبر `safe_xml_fromstring`.
      - يستخرج في `XLSX`: `sharedStrings.xml` + جميع أوراق `xl/worksheets/sheet*.xml`.
    """
    texts: list[str] = []
    tables: list[list[list[str]]] = []
    meta: dict[str, object] = {"author": None, "created": None}
    extra_warnings: list[str] = []
    is_partial = False
    cumulative_bytes = [0]

    with zipfile.ZipFile(path, "r") as zf:
        names, zip_warnings = _check_zip_safety(
            zf,
            limits,
            verify_streams=False,
            forbid_nested_archives=False,
            allow_ooxml_embeddings=True,
        )
        extra_warnings.extend(zip_warnings)
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


def _parse_psd_image_resources(
    res_bytes: bytes,
    *,
    max_blocks: int = 256,
) -> tuple[list[str], bool]:
    """
    قراءة قسم `Image Resources` (`8BIM` blocks) في ملفات `PSD` مع سقف لعدد البنود وفحص صارم للحدود:
      - `0x03EE` (1006): أسماء قنوات Alpha/Spot بصيغة Pascal strings.
      - `0x0415` (1045): أسماء قنوات Alpha/Spot بصيغة Unicode (`UTF-16BE`).
      - `0x0435` (1077) و `0x03EF` (1007): هياكل `DisplayInfo` لقنوات Spot/Alpha.
    """
    channel_names: list[str] = []
    unicode_names: list[str] = []
    has_spot_display_info = False
    offset = 0
    total = len(res_bytes)
    blocks_seen = 0

    while offset + 12 <= total:
        if blocks_seen >= max_blocks:
            raise ValueError(
                f"psd_resources_limit_exceeded: عدد بلوكات 8BIM يتجاوز الحد ({max_blocks})"
            )
        if res_bytes[offset : offset + 4] != b"8BIM":
            break
        blocks_seen += 1
        res_id = struct.unpack(">H", res_bytes[offset + 4 : offset + 6])[0]
        pascal_len = res_bytes[offset + 6]
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

        if res_id == 0x03EE:
            p = 0
            while p < len(block_data) and len(channel_names) < 64:
                slen = block_data[p]
                p += 1
                if p + slen <= len(block_data) and slen > 0:
                    cname = block_data[p : p + slen].decode("cp1256", errors="replace").strip()
                    if cname:
                        channel_names.append(cname)
                p += slen
        elif res_id == 0x0415:
            p = 0
            while p + 4 <= len(block_data) and len(unicode_names) < 64:
                u_len = struct.unpack(">I", block_data[p : p + 4])[0]
                p += 4
                if u_len > 256:
                    break
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
        elif res_id in (0x0435, 0x03EF):
            has_spot_display_info = True

        padded_data_len = data_len + (1 if data_len % 2 == 1 else 0)
        offset = data_start + padded_data_len

    final_names = unicode_names if unicode_names else channel_names
    return (final_names, has_spot_display_info or bool(final_names))


def _extract_psd_header(
    path: Path,
    *,
    limits: IngestionLimits | None = None,
) -> dict[str, object]:
    """
    قراءة ترويسة ملف فوتوشوب الثنائية (`PSD` — `8BPS`) وقسم `Image Resources` بسقوف صارمة.
    """
    active = limits or IngestionLimits()
    with path.open("rb") as f:
        header = f.read(26)
        if len(header) < 26 or header[:4] != b"8BPS":
            raise ValueError("ملف PSD غير صالح أو تالف")
        version, _reserved, channels, height, width, depth, color_mode_code = struct.unpack(
            ">H6sHIIHH", header[4:26]
        )
        if width == 0 or height == 0 or channels == 0 or channels > 56 or version not in (1, 2):
            raise ValueError("ترويسة PSD تالفة أو مقطوعة")

        channel_names: list[str] = []
        has_spot_resources = False
        color_len_bytes = f.read(4)
        if len(color_len_bytes) == 4:
            color_mode_len = struct.unpack(">I", color_len_bytes)[0]
            if color_mode_len <= active.max_psd_resource_bytes:
                f.seek(color_mode_len, io.SEEK_CUR)
                res_len_bytes = f.read(4)
                if len(res_len_bytes) == 4:
                    res_len = struct.unpack(">I", res_len_bytes)[0]
                    if res_len > active.max_psd_resource_bytes:
                        raise ValueError(
                            f"psd_resources_limit_exceeded: حجم قسم موارد PSD ({res_len}) يتجاوز السقف ({active.max_psd_resource_bytes})"
                        )
                    if res_len > 0:
                        res_bytes = f.read(res_len)
                        channel_names, has_spot_resources = _parse_psd_image_resources(
                            res_bytes, max_blocks=active.max_psd_resource_blocks
                        )

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


def _apply_worker_rlimits(max_memory_mb: int, timeout_seconds: float) -> None:
    """
    فرض سقوف الموارد (`RLIMIT_CPU`, `RLIMIT_AS`, `RLIMIT_FSIZE`) داخل العملية الفرعية المعزولة فقط.
    لا يُنفَّذ أبداً في العملية الرئيسية (`CELA_INGEST_WORKER != "1"`) لمنع تقييد الخادم أو مشغّل الاختبارات.
    """
    if os.environ.get("CELA_INGEST_WORKER") != "1":
        return
    try:
        import resource

        cpu_sec = max(1, int(timeout_seconds) + 2)
        resource.setrlimit(resource.RLIMIT_CPU, (cpu_sec, cpu_sec))
        mem_bytes = max(1024, max_memory_mb) * 1024 * 1024
        resource.setrlimit(resource.RLIMIT_AS, (mem_bytes, mem_bytes))
        result_file_limit = MAX_WORKER_RESULT_BYTES + 1024 * 1024
        resource.setrlimit(resource.RLIMIT_FSIZE, (result_file_limit, result_file_limit))
        for limit_name, cap in (("RLIMIT_NPROC", 32), ("RLIMIT_NOFILE", 64), ("RLIMIT_CORE", 0)):
            resource_id = getattr(resource, limit_name, None)
            if resource_id is not None:
                resource.setrlimit(resource_id, (cap, cap))
    except Exception as exc:  # noqa: BLE001
        logger.debug("تعذّر تطبيق بعض حدود rlimit في هذه البيئة: %s", exc)


def _disable_worker_network() -> None:
    """منع فتح أي مقبس شبكة (`socket`) داخل العملية الفرعية المعزولة للمحلّل."""

    def _blocked_socket(*_args: object, **_kwargs: object) -> None:
        raise PermissionError("network_disabled_in_ingestion_sandbox: الشبكة معطّلة داخل صندوق الاستيعاب")

    socket.socket = _blocked_socket  # type: ignore[misc,assignment]
    socket.create_connection = _blocked_socket  # type: ignore[assignment]


def ingest_file_isolated(
    file_path: str | Path,
    *,
    allowed_root: str | Path | None = None,
    limits: IngestionLimits | None = None,
    timeout_seconds: float | None = None,
    _worker_cmd: Sequence[str] | None = None,
    _staged_identity: tuple[str, int, bytes] | None = None,
) -> IngestionResult:
    """Run ingestion in a private staging directory; ``_worker_cmd`` is a test-only seam."""
    active_limits = limits or IngestionLimits()
    root = _resolve_allowed_root(
        allowed_root, allow_private_worker_root=_staged_identity is not None
    )
    raw_path = Path(file_path)
    guarded_path = guard_path(raw_path, allowed_roots=(root,), must_stay_under_root=True)
    safe_path = guarded_path.resolve(strict=True)
    if not safe_path.is_relative_to(root):
        raise ValueError("file_outside_allowed_root_after_realpath")
    clean_name = sanitize_filename(safe_path.name)
    file_id = "sha256:pending"
    size_bytes = 0
    mime = "application/octet-stream"
    warnings: list[str] = []
    effective_timeout = timeout_seconds if timeout_seconds is not None else active_limits.parser_timeout_seconds
    repo_root = str(Path(__file__).resolve().parent.parent.parent)
    worker_limits = asdict(active_limits)
    worker_limits["isolate_in_subprocess"] = False

    def blocked(reason: str, detail: str | None = None) -> IngestionResult:
        logger.warning("ingestion worker blocked id=%s reason=%s", file_id, reason)
        if detail:
            warnings.append(f"{reason}: {detail[:500]}")
        return IngestionResult(
            id=file_id, name=clean_name, mime=mime, size=size_bytes,
            status=IngestionStatus.ZP_OR_EXE_BLOCKED.value,
            parser="isolated_subprocess_guard", language=None, text="",
            metadata={"author": None, "created": None, "blocked_reason": reason},
            warnings=warnings,
        )

    def terminate_worker(proc: subprocess.Popen[bytes]) -> None:
        try:
            if os.name == "posix":
                os.killpg(proc.pid, signal.SIGKILL)
            else:  # pragma: no cover - exercised on Windows CI
                proc.kill()
        except (OSError, AttributeError) as kill_exc:
            logger.debug("process-group kill unavailable: %s", kill_exc)
            proc.kill()
        try:
            proc.wait(timeout=5)
        except subprocess.TimeoutExpired:
            logger.warning("worker process remained alive after bounded kill wait id=%s", file_id)

    if _staged_identity is not None:
        staged_stat_fd = _open_regular_no_follow(safe_path, os.O_RDONLY)
        try:
            staged_stat = os.fstat(staged_stat_fd)
            if (
                not stat.S_ISREG(staged_stat.st_mode)
                or (os.name == "posix" and stat.S_IMODE(staged_stat.st_mode) != 0o400)
                or staged_stat.st_size != _staged_identity[1]
            ):
                raise ValueError("pre_staged_input_invalid")
        finally:
            os.close(staged_stat_fd)
        isolated_dir_context = nullcontext(str(root))
    else:
        isolated_dir_context = tempfile.TemporaryDirectory(prefix="cela_ingest_sbx_")

    with isolated_dir_context as isolated_cwd:
        if _staged_identity is None:
            os.chmod(isolated_cwd, 0o700)
            worker_file = Path(isolated_cwd) / clean_name
        else:
            worker_file = safe_path
        out_path = Path(isolated_cwd) / "result.json"
        try:
            if _staged_identity is None:
                file_id, size_bytes, head = _copy_to_private_readonly(
                    guarded_path, worker_file, max_bytes=active_limits.max_file_size_bytes
                )
            else:
                file_id, size_bytes, head = _staged_identity
            mime, warnings = detect_magic_mime(head, clean_name)
            out_fd = _open_regular_no_follow(
                out_path,
                os.O_CREAT | os.O_EXCL | os.O_WRONLY,
                0o600,
            )
            os.close(out_fd)
            if _worker_cmd is None:
                cmd_clean = [
                    sys.executable, "-m", "src.ingestion.pipeline",
                    "--worker-file", str(worker_file), "--worker-root", isolated_cwd,
                    "--worker-out", str(out_path),
                    "--worker-limits", json.dumps(worker_limits),
                ]
            else:
                substitutions = {
                    "{worker_file}": str(worker_file), "{worker_root}": isolated_cwd,
                    "{worker_out}": str(out_path), "{worker_limits}": json.dumps(worker_limits),
                }
                cmd_clean = []
                for original_arg in _worker_cmd:
                    arg = original_arg
                    for key, value in substitutions.items():
                        arg = arg.replace(key, value)
                    cmd_clean.append(arg)
            proc: subprocess.Popen[bytes] | None = None
            try:
                proc = subprocess.Popen(
                    cmd_clean, cwd=isolated_cwd, stdin=subprocess.DEVNULL,
                    stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL,
                    start_new_session=True,
                    env={"PYTHONPATH": repo_root, "PATH": "", "HOME": isolated_cwd,
                         "TMPDIR": isolated_cwd, "PYTHONDONTWRITEBYTECODE": "1",
                         "CELA_INGEST_WORKER": "1"},
                )
                try:
                    proc.wait(timeout=effective_timeout)
                except subprocess.TimeoutExpired:
                    terminate_worker(proc)
                    return blocked("parser_timeout", f"timeout={effective_timeout}s")
                if proc.returncode != 0:
                    code = proc.returncode
                    if code == WORKER_OOM_EXIT:
                        reason = "parser_oom"
                    elif code == WORKER_OUTPUT_EFBIG_EXIT:
                        reason = "worker_result_efbig"
                    elif code is not None and code < 0:
                        sig_num = -code
                        reason = "parser_oom" if sig_num in (getattr(signal, "SIGKILL", 9), getattr(signal, "SIGXCPU", 24)) else f"signal_{sig_num}"
                    else:
                        reason = "parser_crash"
                    return blocked(reason, f"exit={code}")
                payload_bytes = _read_worker_result(out_path)
                payload = json.loads(
                    payload_bytes,
                    parse_constant=lambda value: (_ for _ in ()).throw(
                        ValueError(f"worker_result_nonstandard_json_constant:{value}")
                    ),
                )
                _validate_worker_json(payload)
                result = IngestionResult.from_dict(payload)
                if result.id != file_id:
                    return blocked("worker_result_identity_mismatch")
                return result
            except (RecursionError, ValueError) as exc:
                if proc is not None and proc.poll() is None:
                    terminate_worker(proc)
                reason = "worker_result_invalid" if proc is not None and proc.returncode == 0 else "parser_crash"
                return blocked(reason, f"{type(exc).__name__}: {exc}")
            except Exception as exc:  # noqa: BLE001
                if proc is not None and proc.poll() is None:
                    terminate_worker(proc)
                reason = "worker_result_invalid" if proc is not None and proc.returncode == 0 else "parser_crash"
                return blocked(reason, f"{type(exc).__name__}: {exc}")
        except _StagedFileTooLarge as exc:
            file_id, size_bytes = exc.file_id, exc.size
            mime, warnings = detect_magic_mime(exc.head, clean_name)
            return blocked("file_size_limit_exceeded", f"max_file_size={exc.limit}")
        except Exception as exc:  # noqa: BLE001
            return blocked("parser_subprocess_staging_error", f"{type(exc).__name__}: {exc}")


def ingest_file(
    file_path: str | Path,
    *,
    allowed_root: str | Path | None = None,
    limits: IngestionLimits | None = None,
    av_scanner: AvScannerCallable | None = None,
    _allow_private_worker_root: bool = False,
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

    root = _resolve_allowed_root(allowed_root, allow_private_worker_root=_allow_private_worker_root)
    raw_path = Path(file_path)
    safe_path = guard_path(raw_path, allowed_roots=(root,), must_stay_under_root=True).resolve(strict=True)
    if not safe_path.is_relative_to(root):
        raise ValueError("file_outside_allowed_root_after_realpath")
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
        if b"_VBA_PROJECT" in raw_ole or (b"VBA" in raw_ole and b"Macros" in raw_ole):
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
                entries, zip_warns = _check_zip_safety(
                    zf,
                    active_limits,
                    verify_streams=True,
                    forbid_nested_archives=True,
                    allow_ooxml_embeddings=False,
                )
                warnings.extend(zip_warns)
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
            psd_meta = _extract_psd_header(safe_path, limits=active_limits)
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
                "zip_unsupported_",
                "archive_",
                "macro_detected_blocked",
                "xml_bomb_",
                "image_decompression_bomb_blocked",
                "psd_resources_limit_exceeded",
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
            sanitize_for_log(clean_name),
            type(exc).__name__,
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
    مدير خط أنابيب الاستيعاب الإنتاجي مع:
      - تفعيل العزل في عملية فرعية افتراضياً (`isolate_in_subprocess=True` — P0).
      - منع تكرار المعالجة (`Idempotency`) بمفتاح مركّب يعزل المستأجرين والإصدار والحدود:
        `tenant_id + PIPELINE_VERSION + limits.fingerprint() + sha256` (إصلاح P1).
      - سياسة احتفاظ للكاش (`cache_ttl_seconds` + `max_cache_entries` + `clear_tenant_cache`).
    """

    def __init__(
        self,
        *,
        limits: IngestionLimits | None = None,
        av_scanner: AvScannerCallable | None = None,
        isolate_in_subprocess: bool = True,
        cache_ttl_seconds: float = 3600.0,
        max_cache_entries: int = 256,
    ) -> None:
        base_limits = limits or IngestionLimits(isolate_in_subprocess=isolate_in_subprocess)
        self._limits = base_limits
        self._av_scanner = av_scanner
        self._cache_ttl_seconds = cache_ttl_seconds
        self._max_cache_entries = max_cache_entries
        self._cache: dict[str, tuple[float, IngestionResult]] = {}
        self._stats_by_mime: dict[str, dict[str, int]] = {}

    def cache_key_for(self, file_id: str, *, tenant_id: str = "default_single_tenant") -> str:
        """بناء مفتاح الـ Cache المركّب مع التحقق الصارم من صلاحية `tenant_id`."""
        clean_tenant = tenant_id.strip()
        if not clean_tenant or ":" in clean_tenant:
            raise ValueError("invalid_tenant_id: يجب تمرير معرف مستأجر صالح وغير فارغ لعزل الكاش")
        return f"{clean_tenant}:{PIPELINE_VERSION}:{self._limits.fingerprint()}:{file_id}"

    def clear_tenant_cache(self, tenant_id: str) -> int:
        """حذف جميع عناصر الكاش الخاصة بمستأجر معيّن (امتثال حق المحو — قانون 151/2020 و GDPR)."""
        prefix = f"{tenant_id.strip()}:"
        keys_to_delete = [k for k in self._cache if k.startswith(prefix)]
        for k in keys_to_delete:
            del self._cache[k]
        return len(keys_to_delete)

    def purge_expired_cache(self, now: float | None = None) -> int:
        """حذف العناصر المنتهية الصلاحية حسب `cache_ttl_seconds`."""
        current = now if now is not None else time.monotonic()
        expired = [
            k
            for k, (ts, _res) in self._cache.items()
            if (current - ts) > self._cache_ttl_seconds
        ]
        for k in expired:
            del self._cache[k]
        return len(expired)

    def process(
        self,
        file_path: str | Path,
        *,
        allowed_root: str | Path | None = None,
        tenant_id: str = "default_single_tenant",
    ) -> tuple[IngestionResult, bool]:
        """
        يعالج الملف ويعيد `(IngestionResult, from_cache)`.
        """
        self.purge_expired_cache()
        root = _resolve_allowed_root(allowed_root)
        raw_path = Path(file_path)
        safe_path = guard_path(raw_path, allowed_roots=(root,), must_stay_under_root=True).resolve(strict=True)
        if not safe_path.is_relative_to(root):
            raise ValueError("file_outside_allowed_root_after_realpath")
        cacheable = True
        if self._limits.isolate_in_subprocess and self._av_scanner is None:
            with tempfile.TemporaryDirectory(prefix="cela_ingest_sbx_") as staging_dir:
                os.chmod(staging_dir, 0o700)
                staged_file = Path(staging_dir) / sanitize_filename(safe_path.name)
                try:
                    staged_identity = _copy_to_private_readonly(
                        safe_path, staged_file, max_bytes=self._limits.max_file_size_bytes
                    )
                except _StagedFileTooLarge as exc:
                    cacheable = False
                    result = IngestionResult(
                        id=exc.file_id,
                        name=sanitize_filename(safe_path.name),
                        mime=detect_magic_mime(exc.head, safe_path.name)[0],
                        size=exc.size,
                        status=IngestionStatus.ZP_OR_EXE_BLOCKED.value,
                        parser="staging_limit_guard",
                        language=None,
                        text="",
                        metadata={"blocked_reason": "file_size_limit_exceeded"},
                        warnings=[f"file_size_limit_exceeded: max_file_size={exc.limit}"],
                    )
                else:
                    cache_key = self.cache_key_for(staged_identity[0], tenant_id=tenant_id)
                    if cache_key in self._cache:
                        return (self._cache[cache_key][1], True)
                    result = ingest_file_isolated(
                        staged_file,
                        allowed_root=staging_dir,
                        limits=self._limits,
                        _staged_identity=staged_identity,
                    )
        else:
            file_id, _, _ = _stream_sha256_and_head(safe_path)
            cache_key = self.cache_key_for(file_id, tenant_id=tenant_id)
            if cache_key in self._cache:
                return (self._cache[cache_key][1], True)
            result = ingest_file(
                safe_path,
                allowed_root=root,
                limits=self._limits,
                av_scanner=self._av_scanner,
            )
        if cacheable:
            if len(self._cache) >= self._max_cache_entries:
                oldest_key = next(iter(self._cache))
                del self._cache[oldest_key]
            self._cache[cache_key] = (time.monotonic(), result)

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


def _cli_worker_main(argv: list[str], *, apply_os_sandbox: bool = True) -> int:
    """نقطة دخول العامل الفرعي المعزول (`ingest_file_isolated`)."""
    import argparse

    parser = argparse.ArgumentParser(description="Isolated file ingestion worker")
    parser.add_argument("--worker-file", required=True)
    parser.add_argument("--worker-root", required=True)
    parser.add_argument("--worker-limits", required=True)
    parser.add_argument("--worker-out")
    args = parser.parse_args(argv)

    limits_dict = json.loads(args.worker_limits)
    limits_dict["isolate_in_subprocess"] = False
    limits = IngestionLimits(**limits_dict)

    if apply_os_sandbox:
        _apply_worker_rlimits(limits.worker_max_memory_mb, limits.parser_timeout_seconds)
        _disable_worker_network()

    try:
        res = ingest_file(
            args.worker_file, allowed_root=args.worker_root, limits=limits,
            _allow_private_worker_root=True,
        )
    except MemoryError:
        return WORKER_OOM_EXIT
    try:
        payload = res.to_dict()
        encoded = json.dumps(payload, ensure_ascii=False).encode("utf-8")
        if len(encoded) > MAX_WORKER_RESULT_BYTES:
            payload["status"] = IngestionStatus.PARTIAL.value
            payload["metadata"] = {**res.metadata, "text_truncated": True}
            payload["warnings"] = [*res.warnings, "text_truncated: worker result capped at configured byte limit"]
            text = res.text
            while len(encoded) > MAX_WORKER_RESULT_BYTES and text:
                text = text[: max(0, len(text) // 2)]
                payload["text"] = text
                encoded = json.dumps(payload, ensure_ascii=False).encode("utf-8")
            if len(encoded) > MAX_WORKER_RESULT_BYTES:
                payload.update({"text": "", "tables": [], "images": [],
                                "metadata": {"text_truncated": True},
                                "warnings": ["text_truncated: oversized structured output was removed"],
                                "status": IngestionStatus.PARTIAL.value})
                encoded = json.dumps(payload, ensure_ascii=False).encode("utf-8")
            if len(encoded) > MAX_WORKER_RESULT_BYTES:
                raise ValueError("worker_result_cannot_be_bounded")
    except MemoryError:
        return WORKER_OOM_EXIT
    if args.worker_out:
        out_path = Path(args.worker_out)
        if out_path.resolve().parent != Path(args.worker_root).resolve():
            raise ValueError("worker_output_path_outside_root")
        fd = _open_regular_no_follow(out_path, os.O_WRONLY)
        try:
            if not stat.S_ISREG(os.fstat(fd).st_mode):
                raise ValueError("worker_output_not_regular")
            os.ftruncate(fd, 0)
            view = memoryview(encoded)
            try:
                while view:
                    written = os.write(fd, view)
                    view = view[written:]
            except OSError as exc:
                if exc.errno == errno.EFBIG:
                    return WORKER_OUTPUT_EFBIG_EXIT
                raise
            finally:
                view.release()
        finally:
            os.close(fd)
    else:
        print(encoded.decode("utf-8"))
    return 0


if __name__ == "__main__":  # pragma: no cover
    raise SystemExit(_cli_worker_main(sys.argv[1:], apply_os_sandbox=True))
