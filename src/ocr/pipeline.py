"""
خط أنابيب OCR → فهرس المعرفة
============================
النص المستخرج لا قيمة له إن بقي في متغير ونسي. هنا يدخل الفهرس **بمرجع**:

    صورة/PDF ممسوح  ──►  نص  ──►  CorpusIndex(source="ocr:اسم الملف")
                                        └─► استشهاد [ocr:file.png#L1-L12]

ملاحظات أمان:
  • النص من OCR **بيانات غير موثوقة**: قد يحتوي ما يشبه التعليمات (حقن).
    يُخزَّن كمرجع ويُقدَّم للنموذج داخل كتلة سياق موثّقة، ولا يُنفَّذ.
  • الملفات تُقرأ للنص فقط؛ لا كتابة على المصدر ولا تعديل عليه.
  • الملفات الضخمة تُقتطع بحد صريح يُسجَّل في التحذيرات.
"""
from __future__ import annotations

from collections.abc import Sequence
from pathlib import Path

from pydantic import BaseModel, Field

from src.ocr.engine import (
    DEFAULT_LANG,
    OcrBackend,
    OcrResult,
    available_backends,
    extract_many,
)


class IngestEntry(BaseModel):
    """ملف واحد: نجح أو فشل، وكم قطعة أضاف."""

    path: str
    ok: bool
    chunks: int = 0
    chars: int = 0
    backend: str = ""
    warning: str = ""


class IngestReport(BaseModel):
    """نتيجة دفعة كاملة — قابلة للعرض والتسجيل."""

    entries: list[IngestEntry] = Field(default_factory=list)
    failures: dict[str, str] = Field(default_factory=dict)

    @property
    def added_chunks(self) -> int:
        return sum(entry.chunks for entry in self.entries)

    def summary(self) -> str:
        ok = [entry for entry in self.entries if entry.ok]
        return (
            f"OCR: {len(ok)}/{len(self.entries) + len(self.failures)} ملفاً، "
            f"{self.added_chunks} قطعة معرفة"
            + (f"، {len(self.failures)} فشل" if self.failures else "")
        )


def source_name(result: OcrResult) -> str:
    """مرجع ثابت داخل الفهرس: `ocr:اسم الملف`."""
    return f"ocr:{Path(result.source).name}"


def ingest_results(results: Sequence[OcrResult], corpus) -> IngestReport:
    """
    يضيف نتائج OCR إلى فهرس موجود.

    نص فارغ لا يُضاف: لا نملأ الفهرس بضجيج يُضعف البحث لاحقاً.
    """
    report = IngestReport()
    for result in results:
        if result.empty:
            report.entries.append(
                IngestEntry(
                    path=result.source,
                    ok=False,
                    backend=result.backend,
                    warning="لا نص مستخرج",
                )
            )
            continue

        title = Path(result.source).name
        body = result.text
        header = f"# {title}\n\nمصدر: {source_name(result)} ({result.backend})\n\n"
        chunks = corpus.add_document(header + body, source=source_name(result), title=title)
        report.entries.append(
            IngestEntry(
                path=result.source,
                ok=True,
                chunks=len(chunks),
                chars=result.chars,
                backend=result.backend,
                warning="؛ ".join(result.warnings),
            )
        )
    return report


def ingest_paths(
    paths: Sequence[str | Path],
    corpus,
    backend: OcrBackend | None = None,
    lang: str = DEFAULT_LANG,
) -> IngestReport:
    """الواجهة الكاملة: استخراج + فهرسة، وفشل ملف لا يوقف الدفعة."""
    results, failures = extract_many(paths, backend=backend, lang=lang)
    report = ingest_results(results, corpus)
    report.failures.update(failures)
    return report


def health() -> dict:
    """حالة جاهزية OCR — تُستخدم في التشخيص وفي نموذج الذات."""
    backends = available_backends()
    return {
        "ready": any(item["available"] for item in backends),
        "backends": backends,
        "note": (
            "OCR يتطلب Tesseract (مجاني) على الجهاز؛ وقراءة PDF النصية تحتاج pypdf."
        ),
    }
