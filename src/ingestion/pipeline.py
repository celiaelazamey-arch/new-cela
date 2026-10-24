"""
خط أنابيب استيعاب واستخراج الملفات الطبقي الآمن (Layered File Ingestion Pipeline)
=================================================================================
يجسّد هذا المحرك المواصفة المعمارية لاستيعاب الملفات (`docs/file-ingestion-pipeline.md`):

  `upload ← detect ← scan ← route ← extract ← normalize ← store ← index`

المبادئ الهندسية والأمنية المطبّقة:
  1. **لا تثق بالامتداد — افحص البصمة الثنائية (`detect_magic_mime`):**
     يفحص أول 64 بايت (`Magic Bytes`) لتحديد النوع الحقيقي (`PDF`, `PNG`, `JPEG`, `GIF`,
     `WEBP`, `BMP`, `TIFF`, `PSD`, `EPS`, `ZIP/DOCX/XLSX/PPTX`, `GZ`, `SQLite`, `EXE/ELF`)
     ويقارنه بامتداد الملف؛ وعند التعارض يعلّم الملف بـ `suspicious_extension_mismatch`.
  2. **حظر الملفات التنفيذية (`exe | dll | elf | sh | bat`):**
     لا تُنفَّذ ولا تُحلَّل أبداً ← تعود بحالة `status="blocked"` مع بصمة `sha256:` والـ `metadata` فقط.
  3. **الحماية الصارمة ضد قنابل الضغط (`Zip Bomb`) واجتياز المسارات (`Path Traversal`):**
     يفحص أرشيفات `ZIP` وملفات `DOCX/XLSX/PPTX` قبل الفك لفرض حدود:
     أقصى حجم ملف، أقصى حجم بعد الفك، أقصى نسبة ضغط، أقصى عدد ملفات، وأقصى عمق مجلدات.
  4. **دعم العربية وترميز `Windows-1256` (`detect_and_decode_text`):**
     يكتشف `UTF-8` و`UTF-8-SIG` و`UTF-16` تلقائياً، وعند الفشل يفحص `cp1256` (`Windows-1256`)
     بحساب كثافة الحروف العربية لمنع تشوّه النصوص العربية القديمة.
  5. **اكتشاف PDF الممسوح ضوئياً (`Scanned PDF`):**
     إذا استخرج `pypdf` صفحات نصها فارغ، يعلّم الملف بـ `scanned_pdf_needs_ocr` ويوجّهه
     للـ OCR أو يعيده بحالة `partial` بدلاً من إرجاع نص فارغ صامت.
  6. **دعم ملفات التصميم والمطابع (`PSD | PNG | TIFF | SVG`):**
     يقرأ ترويسة `PSD` الثنائية (`8BPS`: الأبعاد، عدد القنوات، عمق البت، ونظام الألوان `CMYK/RGB`)،
     ويستخرج `DPI` ونظام الألوان من الصور، ويعقّم `SVG/HTML` من `XSS` و`XXE` بدون Regex.
  7. **عدم تكرار المعالجة (`Idempotency`) وقياس جودة الاستخراج لكل صيغة:**
     يحسب `id = "sha256:..."` بتدفق مجزّأ (`64KB chunks`) دون تحميل الملفات العملاقة دفعة واحدة،
     ويحفظ النتائج في ذاكرة `IngestionPipeline` مع إحصاء نسبة النجاح لكل نوع `MIME`.
"""
from __future__ import annotations

import csv
import email
import hashlib
import io
import json
import logging
import struct
import zipfile
from dataclasses import dataclass, field
from enum import Enum
from pathlib import Path
from xml.etree import ElementTree as ET

from src.core.path_guard import guard_path

logger = logging.getLogger(__name__)

CHUNK_SIZE_BYTES = 65536


class IngestionStatus(str, Enum):
    """حالات المخرج الموحد الأربعة حسب المواصفة."""

    EXTRACTED = "extracted"
    PARTIAL = "partial"
    METADATA_ONLY = "metadata_only"
    ZP_OR_EXE_BLOCKED = "blocked"


@dataclass(frozen=True)
class IngestionLimits:
    """الحدود الأمنية الصارمة ضد استنزاف الذاكرة وقنابل الضغط (Zip Bombs)."""

    max_file_size_bytes: int = 50 * 1024 * 1024          # 50 MB
    max_uncompressed_bytes: int = 100 * 1024 * 1024      # 100 MB
    max_archive_entries: int = 500
    max_compression_ratio: float = 100.0
    max_directory_depth: int = 8
    max_text_chars: int = 200_000


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
    ".zip": "application/zip",
    ".gz": "application/gzip",
    ".sqlite": "application/vnd.sqlite3",
    ".db": "application/vnd.sqlite3",
    ".exe": "application/x-msdownload",
    ".dll": "application/x-msdownload",
    ".elf": "application/x-elf",
    ".sh": "application/x-sh",
    ".bat": "application/x-bat",
}

_EXECUTABLE_MIMES: frozenset[str] = frozenset(
    {
        "application/x-msdownload",
        "application/x-elf",
        "application/x-sh",
        "application/x-bat",
    }
)

_EXECUTABLE_EXTENSIONS: frozenset[str] = frozenset(
    {".exe", ".dll", ".elf", ".sh", ".bat", ".cmd", ".ps1", ".msi", ".com"}
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
    elif head.startswith(b"MZ"):
        magic_mime = "application/x-msdownload"
    elif head.startswith(b"\x7fELF"):
        magic_mime = "application/x-elf"
    elif head.startswith(b"#!"):
        magic_mime = "application/x-sh"
    elif head.startswith(b"PK\x03\x04"):
        if ext in (".docx", ".xlsx", ".pptx") and expected_mime:
            magic_mime = expected_mime
        else:
            magic_mime = "application/zip"
    else:
        stripped = head.lstrip()
        lower_head = stripped[:256].lower()
        if lower_head.startswith(b"<svg") or (b"<svg" in lower_head and b"xmlns" in lower_head):
            magic_mime = "image/svg+xml"
        elif lower_head.startswith((b"<!doctype html", b"<html")):
            magic_mime = "text/html"
        elif lower_head.startswith(b"<?xml"):
            magic_mime = "application/xml"

    # مقارنة البصمة الثنائية بالامتداد الظاهري
    if magic_mime is not None:
        if expected_mime and expected_mime != magic_mime:
            # استثناء مشروع: ملفات Office هي حاويات ZIP، وملفات .ai الحديثة هي PDF
            is_office_zip = magic_mime == "application/zip" and ext in (".docx", ".xlsx", ".pptx")
            is_ai_pdf = magic_mime == "application/pdf" and ext == ".ai"
            if not (is_office_zip or is_ai_pdf):
                warnings.append(
                    f"suspicious_extension_mismatch: الامتداد '{ext}' يتعارض مع البصمة الثنائية '{magic_mime}'"
                )
        return (magic_mime, warnings)

    if ext in _EXECUTABLE_EXTENSIONS:
        return (_EXTENSION_MIME_MAP.get(ext, "application/x-msdownload"), warnings)

    if expected_mime:
        return (expected_mime, warnings)

    return ("application/octet-stream", warnings)


def detect_language(text: str) -> str | None:
    """كشف اللغة السائدة في النص المستخرج (`ar` للعربية أو `en` للاتينية)."""
    if not text or not text.strip():
        return None
    arabic_count = sum(1 for ch in text if "\u0600" <= ch <= "\u06ff")
    latin_count = sum(1 for ch in text if ("a" <= ch.lower() <= "z"))
    if arabic_count == 0 and latin_count == 0:
        return None
    return "ar" if arabic_count >= latin_count else "en"


def detect_and_decode_text(raw_bytes: bytes) -> tuple[str, str, list[str]]:
    """
    فك ترميز النصوص مع دعم خاص للترميز العربي القديم `Windows-1256` (`cp1256`).
    يعيد `(decoded_text, encoding_used, warnings)`.
    """
    warnings: list[str] = []
    if raw_bytes.startswith(b"\xef\xbb\xbf"):
        return (raw_bytes.decode("utf-8-sig", errors="replace"), "utf-8-sig", warnings)
    if raw_bytes.startswith((b"\xff\xfe", b"\xfe\xff")):
        return (raw_bytes.decode("utf-16", errors="replace"), "utf-16", warnings)

    try:
        return (raw_bytes.decode("utf-8"), "utf-8", warnings)
    except UnicodeDecodeError:
        logger.debug("فشل UTF-8 الصارم — جاري فحص الترميز العربي Windows-1256 (cp1256)")

    # تجربة Windows-1256 (شائع جداً في ملفات الحسابات والمطابع العربية القديمة)
    cp1256_text = raw_bytes.decode("cp1256", errors="replace")
    arabic_chars = sum(1 for ch in cp1256_text if "\u0600" <= ch <= "\u06ff")
    if arabic_chars > 0:
        warnings.append("legacy_encoding_detected: تم فك الترميز العربي عبر Windows-1256 (cp1256)")
        return (cp1256_text, "cp1256", warnings)

    return (raw_bytes.decode("latin-1", errors="replace"), "latin-1", warnings)


def sanitize_svg_or_html(raw_text: str) -> tuple[str, list[str]]:
    """
    تنظيف ملفات `SVG` و`HTML` و`XML` من ثغرات `XSS` (`<script>`, `onload=`, `javascript:`)
    ومن الكيانات الخارجية `XXE` (`<!ENTITY` + `SYSTEM`/`PUBLIC`) بزمن خطي وبصفر Regex.
    """
    warnings: list[str] = []
    lowered = raw_text.lower()

    if "<!entity" in lowered and ("system" in lowered or "public" in lowered):
        warnings.append("xxe_entity_blocked: تم رصد وحذف تعريف كيان خارجي (XXE)")

    dangerous_markers = (
        "<script",
        "javascript:",
        "onload=",
        "onerror=",
        "onclick=",
        "onmouseover=",
    )
    for marker in dangerous_markers:
        if marker in lowered:
            warnings.append(f"xss_vector_sanitized: تم تحييد العنصر الخطير '{marker}'")

    # إزالة الأسطر أو المقاطع التي تحتوي على تعريفات <!ENTITY أو <script
    safe_lines: list[str] = []
    skip_script = False
    for line in raw_text.splitlines():
        ll = line.lower()
        if "<!entity" in ll or "<!doctype" in ll and "system" in ll:
            continue
        if "<script" in ll:
            skip_script = True
            if "</script>" in ll:
                skip_script = False
            continue
        if skip_script:
            if "</script>" in ll:
                skip_script = False
            continue
        cleaned_line = line
        for attr in ("onload=", "onerror=", "onclick=", "onmouseover=", "javascript:"):
            if attr in cleaned_line.lower():
                cleaned_line = cleaned_line.replace(attr, "data-sanitized=")
                cleaned_line = cleaned_line.replace(attr.upper(), "data-sanitized=")
        safe_lines.append(cleaned_line)

    return ("\n".join(safe_lines), warnings)


def _check_zip_safety(zf: zipfile.ZipFile, limits: IngestionLimits) -> list[str]:
    """
    فحص أرشيف ZIP ضد:
      1. Zip Bomb (تجاوز الحجم المفكوك أو نسبة الضغط أو عدد الملفات).
      2. Path Traversal (`..` أو مسار مطلق `/` داخل الأرشيف).
      3. تجاوز عمق المجلدات (`max_directory_depth`).
    يرفع `ValueError` عند وجود خطر أمني.
    """
    infos = zf.infolist()
    if len(infos) > limits.max_archive_entries:
        raise ValueError(
            f"zip_bomb_entries_exceeded: عدد ملفات الأرشيف ({len(infos)}) يتجاوز الحد ({limits.max_archive_entries})"
        )

    total_uncompressed = 0
    for info in infos:
        parts = info.filename.replace("\\", "/").split("/")
        if ".." in parts or info.filename.startswith(("/", "\\")):
            raise ValueError(f"archive_path_traversal: مسار غير آمن داخل الأرشيف '{info.filename}'")
        if len(parts) > limits.max_directory_depth:
            raise ValueError(
                f"archive_depth_exceeded: عمق المجلدات ({len(parts)}) يتجاوز الحد ({limits.max_directory_depth})"
            )
        total_uncompressed += info.file_size
        if total_uncompressed > limits.max_uncompressed_bytes:
            raise ValueError(
                f"zip_bomb_size_exceeded: الحجم بعد الفك ({total_uncompressed}) يتجاوز الحد المسموح"
            )
        compressed = max(info.compress_size, 1)
        ratio = info.file_size / compressed
        if info.file_size > 1024 * 1024 and ratio > limits.max_compression_ratio:
            raise ValueError(
                f"zip_bomb_ratio_exceeded: نسبة ضغط مريبة ({ratio:.1f}:1) في '{info.filename}'"
            )
    return [info.filename for info in infos if not info.is_dir()]


def _extract_openxml_document(path: Path, mime: str, limits: IngestionLimits) -> tuple[str, list[list[list[str]]], dict[str, object]]:
    """
    استخراج النصوص والجداول من ملفات Office (`DOCX`, `XLSX`, `PPTX`) بأمان تام
    باستخدام المكتبة القياسية (`zipfile` + `ElementTree`) بعد فحص `Zip Bomb`.
    """
    texts: list[str] = []
    tables: list[list[list[str]]] = []
    meta: dict[str, object] = {"author": None, "created": None}

    with zipfile.ZipFile(path, "r") as zf:
        names = _check_zip_safety(zf, limits)

        if mime.endswith("wordprocessingml.document") and "word/document.xml" in names:
            xml_bytes = zf.read("word/document.xml")
            root = ET.fromstring(xml_bytes)
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
            shared: list[str] = []
            if "xl/sharedStrings.xml" in names:
                ss_root = ET.fromstring(zf.read("xl/sharedStrings.xml"))
                for elem in ss_root.iter():
                    if elem.tag.split("}")[-1] == "t" and elem.text:
                        shared.append(elem.text)
            texts.extend(shared)

        elif mime.endswith("presentationml.presentation"):
            slide_files = sorted(n for n in names if n.startswith("ppt/slides/slide") and n.endswith(".xml"))
            meta["slides_count"] = len(slide_files)
            for sf in slide_files:
                s_root = ET.fromstring(zf.read(sf))
                for elem in s_root.iter():
                    if elem.tag.split("}")[-1] == "t" and elem.text:
                        texts.append(elem.text)

    return (" ".join(texts).strip(), tables, meta)


def _extract_psd_header(path: Path) -> dict[str, object]:
    """
    قراءة ترويسة ملف فوتوشوب الثنائية (`PSD` — `8BPS`) مباشرةً بدون مكتبات ثقيلة:
    يستخرج الأبعاد (`width`, `height`)، عدد القنوات (`channels` — مهم لفصل ألوان السلك سكرين!)،
    عمق البت (`bit_depth`)، ونظام الألوان (`color_mode`: CMYK / RGB / Grayscale / Spot).
    """
    with path.open("rb") as f:
        header = f.read(26)
    if len(header) < 26 or header[:4] != b"8BPS":
        raise ValueError("ملف PSD غير صالح أو تالف")
    version, _reserved, channels, height, width, depth, color_mode_code = struct.unpack(
        ">H6sHIIHH", header[4:26]
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
    return {
        "author": None,
        "created": None,
        "psd_version": version,
        "channels": channels,
        "width": width,
        "height": height,
        "bit_depth": depth,
        "color_mode": color_modes.get(color_mode_code, f"Unknown({color_mode_code})"),
    }


def ingest_file(
    file_path: str | Path,
    *,
    allowed_root: str | Path | None = None,
    limits: IngestionLimits | None = None,
) -> IngestionResult:
    """
    نقطة الدخول الموحدة لاستيعاب أي ملف عبر المراحل الطبقية:
    `detect (magic bytes) → scan (security/limits) → route & extract → normalize`.
    """
    active_limits = limits or IngestionLimits()
    raw_path = Path(file_path)
    root = Path(allowed_root).resolve() if allowed_root is not None else raw_path.resolve().parent
    safe_path = guard_path(raw_path, allowed_roots=(root,))
    clean_name = sanitize_filename(safe_path.name)

    file_id, size_bytes, head = _stream_sha256_and_head(safe_path)
    mime, warnings = detect_magic_mime(head, clean_name)
    ext = Path(clean_name).suffix.lower()

    # 1. حظر الملفات التنفيذية أو الملفات التي تتجاوز الحجم الأقصى
    if mime in _EXECUTABLE_MIMES or ext in _EXECUTABLE_EXTENSIONS:
        warnings.append("executable_blocked: الملفات التنفيذية لا تُنفَّذ ولا تُحلَّل أمنياً")
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

    # 2. توجيه حسب جدول MIME مع سلسلة Fallback (متخصص ← شامل ← metadata_only)
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
            decoded_text, encoding_used, enc_warnings = detect_and_decode_text(raw_bytes)
            warnings.extend(enc_warnings)
            tables: list[list[list[str]]] = []
            parser_name = "text_decoder"
            meta: dict[str, object] = {"author": None, "created": None, "encoding": encoding_used}

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
                parser_name = "svg_html_sanitizer"
                decoded_text, san_warnings = sanitize_svg_or_html(decoded_text)
                warnings.extend(san_warnings)
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

        # ---- (ب) ملفات PDF (و .ai المبنية على PDF) مع كشف PDF الممسوح ضوئياً ----
        if mime == "application/pdf":
            from pypdf import PdfReader

            reader = PdfReader(str(safe_path))
            pages_count = len(reader.pages)
            page_texts: list[str] = []
            for page in reader.pages:
                page_texts.append(page.extract_text() or "")
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
                    pages=pages_count,
                    metadata=meta,
                    warnings=warnings,
                )

            truncated = full_text[: active_limits.max_text_chars]
            return IngestionResult(
                id=file_id,
                name=clean_name,
                mime=mime,
                size=size_bytes,
                status=IngestionStatus.EXTRACTED.value,
                parser="pypdf",
                language=detect_language(truncated),
                text=truncated,
                pages=pages_count,
                metadata=meta,
                warnings=warnings,
            )

        # ---- (ج) ملفات Office Open XML (DOCX / XLSX / PPTX) ----
        if mime in (
            "application/vnd.openxmlformats-officedocument.wordprocessingml.document",
            "application/vnd.openxmlformats-officedocument.spreadsheetml.sheet",
            "application/vnd.openxmlformats-officedocument.presentationml.presentation",
        ):
            doc_text, doc_tables, doc_meta = _extract_openxml_document(
                safe_path, mime, active_limits
            )
            return IngestionResult(
                id=file_id,
                name=clean_name,
                mime=mime,
                size=size_bytes,
                status=IngestionStatus.EXTRACTED.value if doc_text or doc_tables else IngestionStatus.PARTIAL.value,
                parser="openxml_stdlib",
                language=detect_language(doc_text),
                text=doc_text[: active_limits.max_text_chars],
                pages=int(doc_meta.get("slides_count", 1)) if "slides_count" in doc_meta else 1,
                tables=doc_tables,
                metadata=doc_meta,
                warnings=warnings,
            )

        # ---- (د) أرشيفات ZIP مع فحص قنابل الضغط واجتياز المسارات ----
        if mime == "application/zip":
            with zipfile.ZipFile(safe_path, "r") as zf:
                entries = _check_zip_safety(zf, active_limits)
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

        # ---- (هـ) ملفات التصميم للمطابع: PSD (فوتوشوب) ----
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
                    f"Color: {psd_meta['color_mode']} | Channels: {psd_meta['channels']}"
                ),
                pages=1,
                images=[
                    {
                        "width": psd_meta["width"],
                        "height": psd_meta["height"],
                        "color_mode": psd_meta["color_mode"],
                        "channels": psd_meta["channels"],
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
                color_mode = img.mode
                dpi_raw = img.info.get("dpi")
                dpi = tuple(round(float(x)) for x in dpi_raw) if isinstance(dpi_raw, tuple) else None

            if dpi and min(dpi) < 300:
                warnings.append(
                    f"low_print_dpi: دقة الصورة ({dpi[0]} DPI) أقل من 300 DPI الموصى بها لأفلام الطباعة"
                )
            img_meta = {
                "author": None,
                "created": None,
                "width": width,
                "height": height,
                "color_mode": color_mode,
                "dpi": list(dpi) if dpi else None,
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
        if err_msg.startswith(("zip_bomb_", "archive_")):
            warnings.append(err_msg)
            return IngestionResult(
                id=file_id,
                name=clean_name,
                mime=mime,
                size=size_bytes,
                status=IngestionStatus.ZP_OR_EXE_BLOCKED.value,
                parser="archive_security_guard",
                language=None,
                text="",
                metadata={"author": None, "created": None, "blocked_reason": err_msg},
                warnings=warnings,
            )
        warnings.append(f"parser_fallback_to_metadata: {err_msg}")
    except Exception as exc:  # noqa: BLE001 — مقصود: فشل parser يرجع إلى metadata_only ولا يسقط النظام
        logger.warning("تعذّر استخراج '%s' بالمحلّل المتخصص (%s) — التحول إلى metadata_only", clean_name, exc)
        warnings.append(f"parser_fallback_to_metadata: {type(exc).__name__}: {exc}")

    # 3. الملاذ الأخير (Fallback): أي ملف غير مدعوم أو تالف يُقبل ويُخزّن بـ metadata_only (لا يُرفض!)
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
      - منع تكرار المعالجة لنفس المحتوى (`Idempotency` عبر `sha256:`).
      - قياس نسبة الاستخراج الناجح لكل نوع `MIME` (الجودة — القسم 12).
    """

    def __init__(self, *, limits: IngestionLimits | None = None) -> None:
        self._limits = limits or IngestionLimits()
        self._cache_by_hash: dict[str, IngestionResult] = {}
        self._stats_by_mime: dict[str, dict[str, int]] = {}

    def process(
        self,
        file_path: str | Path,
        *,
        allowed_root: str | Path | None = None,
    ) -> tuple[IngestionResult, bool]:
        """
        يعالج الملف ويعيد `(IngestionResult, from_cache)`.
        إذا سبق معالجة ملف بنفس بصمة `sha256:` يعيد النتيجة المخزّنة فوراً (`from_cache=True`).
        """
        raw_path = Path(file_path)
        root = Path(allowed_root).resolve() if allowed_root is not None else raw_path.resolve().parent
        safe_path = guard_path(raw_path, allowed_roots=(root,))
        file_id, _, _ = _stream_sha256_and_head(safe_path)

        if file_id in self._cache_by_hash:
            return (self._cache_by_hash[file_id], True)

        result = ingest_file(safe_path, allowed_root=root, limits=self._limits)
        self._cache_by_hash[file_id] = result

        bucket = self._stats_by_mime.setdefault(
            result.mime, {"total": 0, "extracted": 0, "partial": 0, "metadata_only": 0, "blocked": 0}
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
