"""
محرّك OCR — نص من الصور والمستندات الممسوحة، بتكلفة صفرية
=========================================================
يعتمد على **Tesseract** (مفتوح المصدر، مجاني، دعم عربي عبر `ara`) عبر سطر
الأوامر، فلا نضيف حزمة Python إلزامية. الصور تُهيَّأ مسبقاً بـ Pillow
(رمادي + سطوع تلقائي + تكبير عند الحاجة) لأن دقة Tesseract تنهار على الصور
الصغيرة أو منخفضة التباين.

حدود معلنة:
  • لا محرّك OCR مدمج: إن لم يكن `tesseract` مثبتاً في الجهاز، تُرفض العملية
    برسالة إرشادية بدل نتيجة مزيفة.
  • PDF: نقرأ طبقة النص إن وُجدت (pypdf اختياري). PDF ممسوح يحتاج تحويلاً إلى
    صور (pymupdf) — لا نخمّن ولا ندّعي قراءته.
  • النص المستخرج **بيانات غير موثوقة**، لا تعليمات. لا يُنفَّذ ولا يُعامل
    كأمر، ويُمرَّر للفهرس كمرجع يمكن التحقق منه.
  • الدقة تعتمد كلياً على جودة الصورة واللغة المثبّتة — لا نقدّم ضماناً.
"""
from __future__ import annotations

import logging
import shutil
import subprocess
import tempfile
from collections.abc import Sequence
from contextlib import contextmanager
from pathlib import Path
from typing import Protocol, runtime_checkable

from pydantic import BaseModel, Field

logger = logging.getLogger("new-cela.ocr")

IMAGE_SUFFIXES = frozenset({".png", ".jpg", ".jpeg", ".tif", ".tiff", ".bmp", ".webp"})
PDF_SUFFIXES = frozenset({".pdf"})
TEXT_SUFFIXES = frozenset({".txt", ".md"})
DEFAULT_LANG = "ara+eng"
MAX_CHARS = 200_000
MAX_PIXELS = 40_000_000
MIN_WIDTH_FOR_OCR = 1000


class OcrError(RuntimeError):
    """فشل OCR بسبب مدخل غير صالح أو مستند غير مدعوم."""


class OcrUnavailable(OcrError):
    """لا يوجد محرّك OCR متاح — رسالة إرشادية بدل نتيجة فارغة."""


class OcrResult(BaseModel):
    """نتيجة استخراج واحدة — قابلة للتسجيل والاستشهاد."""

    text: str
    backend: str
    kind: str
    source: str = ""
    chars: int = 0
    pages: int = 0
    warnings: list[str] = Field(default_factory=list)

    @property
    def empty(self) -> bool:
        return not self.text.strip()


@runtime_checkable
class OcrBackend(Protocol):
    """عقد محرّك OCR — يسمح بمحرّك بديل أو وهمي في الاختبارات."""

    name: str

    def available(self) -> bool: ...

    def extract(self, path: Path, lang: str = DEFAULT_LANG) -> str: ...


# ================= Tesseract =================

class TesseractBackend:
    """
    Tesseract عبر CLI — بلا حزم Python.

    التثبيت (مرة واحدة، مجاني):
      • Debian/Ubuntu: `apt install tesseract-ocr tesseract-ocr-ara`
      • macOS: `brew install tesseract tesseract-lang`
    """

    name = "tesseract"

    def __init__(self, binary: str = "tesseract", timeout: float = 90.0) -> None:
        self.binary = binary
        self.timeout = timeout

    def available(self) -> bool:
        return shutil.which(self.binary) is not None

    def extract(self, path: Path, lang: str = DEFAULT_LANG) -> str:
        if not self.available():
            raise OcrUnavailable(
                "محرّك Tesseract غير مثبت. ثبّته مجاناً:\n"
                "  Debian/Ubuntu: sudo apt install tesseract-ocr tesseract-ocr-ara\n"
                "  macOS:         brew install tesseract tesseract-lang\n"
                "ثم أعد المحاولة."
            )
        if not path.is_file():
            raise OcrError(f"ملف غير موجود: {path}")

        with prepared_temp(path) as prepared:
            try:
                completed = subprocess.run(
                    [
                        shutil.which(self.binary) or self.binary,
                        str(prepared),
                        "stdout",
                        "-l",
                        lang,
                        "--psm",
                        "3",
                    ],
                    capture_output=True,
                    timeout=self.timeout,
                    check=False,
                )
            except subprocess.TimeoutExpired as exc:
                raise OcrError(f"تجاوز OCR المهلة ({self.timeout:.0f} ثانية).") from exc
            except OSError as exc:
                raise OcrUnavailable(f"تعذّر تشغيل Tesseract: {exc}") from exc

        text = completed.stdout.decode("utf-8", errors="replace").strip()
        if completed.returncode != 0 and not text:
            stderr = completed.stderr.decode("utf-8", errors="replace")[:300]
            raise OcrError(f"فشل Tesseract (رمز {completed.returncode}): {stderr}")
        return text[:MAX_CHARS]


def default_backend() -> OcrBackend:
    """المحرّك الافتراضي — يُستبدل في الاختبارات بواحد وهمي."""
    return TesseractBackend()


# ================= المعالجة المسبقة =================

@contextmanager
def prepared_temp(path: Path):
    """
    يحسّن الصورة في ملف مؤقت ثم يحذفه — لا بقايا على القرص.

    إن غاب Pillow، نمرّر الصورة الأصلية كما هي (تدهور واضح لا انهيار).
    """
    try:
        prepared = prepare_image(path)
    except ImportError:
        yield path
        return
    except OcrError:
        raise

    with tempfile.NamedTemporaryFile(suffix=".png", delete=False) as handle:
        temp_path = Path(handle.name)
    try:
        temp_path.write_bytes(prepared)
        yield temp_path
    finally:
        temp_path.unlink(missing_ok=True)


def prepare_image(path: Path) -> bytes:
    """
    رمادي + سطوع تلقائي + تكبير إلى عرض 1000px على الأقل، ثم PNG.

    التكبير مفيد لأن Tesseract يتعامل مع الحروف الصغيرة بضعف شديد.
    """
    try:
        from PIL import Image, ImageOps
    except ImportError as exc:
        raise ImportError("Pillow غير مثبت") from exc

    if not path.is_file():
        raise OcrError(f"ملف غير موجود: {path}")
    if path.suffix.lower() not in IMAGE_SUFFIXES:
        raise OcrError(f"ليس صيغة صورة مدعومة: {path.suffix or '(بلا امتداد)'}")

    try:
        with Image.open(path) as image:
            width, height = image.size
            if width * height > MAX_PIXELS:
                raise OcrError(f"صورة أكبر من الحد ({MAX_PIXELS:,} بكسل).")
            grey = ImageOps.autocontrast(image.convert("L"))
            if width < MIN_WIDTH_FOR_OCR and width > 0:
                factor = min(4.0, MIN_WIDTH_FOR_OCR / width)
                grey = grey.resize(
                    (int(width * factor), int(height * factor)), Image.LANCZOS
                )
            from io import BytesIO

            buffer = BytesIO()
            grey.save(buffer, format="PNG")
            return buffer.getvalue()
    except OSError as exc:
        raise OcrError(f"تعذّر قراءة الصورة: {exc}") from exc


# ================= التوجيه =================

def detect_kind(path: str | Path) -> str:
    """يصنّف المدخل: image | pdf | text | unknown."""
    suffix = Path(path).suffix.lower()
    if suffix in IMAGE_SUFFIXES:
        return "image"
    if suffix in PDF_SUFFIXES:
        return "pdf"
    if suffix in TEXT_SUFFIXES:
        return "text"
    return "unknown"


def _load_pypdf():
    """استيراد pypdf إن وُجد — بدونه لا نقرأ PDF نصياً."""
    try:
        from pypdf import PdfReader

        return PdfReader
    except ImportError:
        return None


def extract_pdf_text(path: Path) -> tuple[str, int]:
    """يقرأ طبقة النص من PDF. يعيد (النص، عدد الصفحات)."""
    reader_cls = _load_pypdf()
    if reader_cls is None:
        raise OcrUnavailable(
            "قراءة PDF تحتاج pypdf: pip install -r requirements-ocr.txt\n"
            "وإن كان الملف ممسوحاً (بلا طبقة نص) فحوّله إلى صور أولاً."
        )
    try:
        reader = reader_cls(str(path))
        pages = len(reader.pages)
        parts: list[str] = []
        for index, page in enumerate(reader.pages, start=1):
            text = (page.extract_text() or "").strip()
            if text:
                parts.append(f"[صفحة {index}]\n{text}")
        return "\n\n".join(parts), pages
    except Exception as exc:
        raise OcrError(f"تعذّر قراءة PDF ({type(exc).__name__}).") from exc


def extract_text(
    path: str | Path,
    backend: OcrBackend | None = None,
    lang: str = DEFAULT_LANG,
) -> OcrResult:
    """
    يستخرج النص من ملف واحد وفق نوعه.

    يرفع `OcrUnavailable` حين لا يوجد محرّك، و`OcrError` للمدخلات غير الصالحة.
    """
    target = Path(path)
    kind = detect_kind(target)
    result = OcrResult(text="", backend="", kind=kind, source=str(target))

    if not target.is_file():
        raise OcrError(f"ملف غير موجود: {target}")

    if kind == "text":
        result.text = target.read_text(encoding="utf-8", errors="replace")[:MAX_CHARS]
        result.backend = "plain-text"
        result.chars = len(result.text)
        return result

    if kind == "pdf":
        text, pages = extract_pdf_text(target)
        result.text = text[:MAX_CHARS]
        result.pages = pages
        result.chars = len(result.text)
        result.backend = "pypdf"
        if not text.strip():
            result.warnings.append(
                "PDF بلا طبقة نص — يبدو ممسوحاً؛ حوّله إلى صور ثم استخدم OCR."
            )
        return result

    if kind == "image":
        engine = backend or default_backend()
        result.backend = engine.name
        if not engine.available():
            raise OcrUnavailable(
                f"محرّك OCR «{engine.name}» غير متاح. ثبّت Tesseract ثم أعد المحاولة."
            )
        text = engine.extract(target, lang=lang)
        result.text = text[:MAX_CHARS]
        result.chars = len(result.text)
        if not text.strip():
            result.warnings.append("لم يُستخرج نص — تحقّق من جودة الصورة أو اللغة.")
        return result

    raise OcrError(
        f"صيغة غير مدعومة: {target.suffix or '(بلا امتداد)'}. "
        f"المدعوم: صور {sorted(IMAGE_SUFFIXES)} أو PDF أو نص."
    )


def available_backends() -> list[dict]:
    """حالة المحركات المتاحة — تُعرض في CLI وفي نموذج الذات."""
    backend = TesseractBackend()
    return [
        {
            "name": "tesseract",
            "available": backend.available(),
            "languages_hint": "يتطلب حزمة ara للعربية",
            "install": "apt install tesseract-ocr tesseract-ocr-ara",
        },
        {
            "name": "pypdf",
            "available": _load_pypdf() is not None,
            "languages_hint": "طبقة نص PDF فقط (لا OCR)",
            "install": "pip install -r requirements-ocr.txt",
        },
    ]


def extract_many(
    paths: Sequence[str | Path],
    backend: OcrBackend | None = None,
    lang: str = DEFAULT_LANG,
) -> tuple[list[OcrResult], dict[str, str]]:
    """يستخرج من عدة ملفات؛ فشل ملف يُسجَّل ولا يُسقط الباقي."""
    results: list[OcrResult] = []
    failures: dict[str, str] = {}
    for path in paths:
        try:
            results.append(extract_text(path, backend=backend, lang=lang))
        except OcrError as exc:
            failures[str(path)] = f"{type(exc).__name__}: {exc}"
        except Exception as exc:  # noqa: BLE001 — دفاع أخير
            failures[str(path)] = f"{type(exc).__name__}: {exc}"
    return results, failures
