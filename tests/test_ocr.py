"""
اختبارات OCR — معالجة صور حقيقية (Pillow)، PDF حقيقي (pypdf)، ومحرّك وهمي للتوجيه.

المحرّك الحقيقي (Tesseract) غير مثبت في بيئة التطوير هذه، فاختباراته تُتخطّى
صراحةً عند غيابه؛ ولا ندّعي قراءة صور حقيقية بدونه.
"""
from __future__ import annotations

import asyncio
from pathlib import Path

import pytest

from src.core.policy import PolicyEngine, ToolRisk
from src.core.quota import QuotaGuard
from src.knowledge.corpus import CorpusIndex
from src.ocr.cli import build_parser
from src.ocr.cli import main as cli_main
from src.ocr.engine import (
    DEFAULT_LANG,
    MIN_WIDTH_FOR_OCR,
    OcrError,
    OcrResult,
    OcrUnavailable,
    TesseractBackend,
    available_backends,
    detect_kind,
    extract_many,
    extract_pdf_text,
    extract_text,
    prepare_image,
)
from src.ocr.pipeline import health, ingest_paths, ingest_results, source_name
from src.tools.builtin import build_default_registry, ocr_extract
from src.tools.registry import ToolContext

pypdf_installed = pytest.mark.skipif(
    not any(item["name"] == "pypdf" and item["available"] for item in available_backends()),
    reason="pypdf غير مثبت",
)


class StubBackend:
    """محرّك وهمي: يثبت التوجيه والفهرسة بلا ادّعاء قراءة بصرية."""

    name = "stub"

    def __init__(self, text: str = "نص مستخرج", available: bool = True) -> None:
        self.text = text
        self._available = available
        self.calls: list[tuple[Path, str]] = []

    def available(self) -> bool:
        return self._available

    def extract(self, path: Path, lang: str = DEFAULT_LANG) -> str:
        self.calls.append((path, lang))
        if not self._available:
            raise OcrUnavailable("غير متاح")
        return self.text


def make_image(path: Path, size: tuple[int, int] = (120, 60)) -> Path:
    from PIL import Image

    Image.new("RGB", size, "white").save(path)
    return path


def make_pdf(path: Path, text: str = "Hello OCR") -> Path:
    """يبني PDF بسيطاً صالحاً يدوياً (بلا مكتبات توليد)."""
    stream = f"BT /F1 12 Tf 20 100 Td ({text}) Tj ET".encode()
    objects = [
        b"<< /Type /Catalog /Pages 2 0 R >>",
        b"<< /Type /Pages /Kids [3 0 R] /Count 1 >>",
        (
            b"<< /Type /Page /Parent 2 0 R /MediaBox [0 0 300 200] "
            b"/Resources << /Font << /F1 4 0 R >> >> /Contents 5 0 R >>"
        ),
        b"<< /Type /Font /Subtype /Type1 /BaseFont /Helvetica >>",
        b"<< /Length " + str(len(stream)).encode() + b" >>\nstream\n" + stream + b"\nendstream",
    ]
    out = bytearray(b"%PDF-1.4\n")
    offsets: list[int] = []
    for index, body in enumerate(objects, start=1):
        offsets.append(len(out))
        out += f"{index} 0 obj\n".encode() + body + b"\nendobj\n"
    xref = len(out)
    out += f"xref\n0 {len(objects) + 1}\n".encode()
    out += b"0000000000 65535 f \n"
    for offset in offsets:
        out += f"{offset:010d} 00000 n \n".encode()
    out += (
        f"trailer\n<< /Size {len(objects) + 1} /Root 1 0 R >>\nstartxref\n{xref}\n%%EOF\n"
    ).encode()
    path.write_bytes(bytes(out))
    return path


# ================= التصنيف والتوجيه =================

@pytest.mark.parametrize(
    ("name", "kind"),
    [
        ("a.png", "image"),
        ("a.JPEG", "image"),
        ("a.tiff", "image"),
        ("a.pdf", "pdf"),
        ("a.txt", "text"),
        ("a.docx", "unknown"),
        ("noext", "unknown"),
    ],
)
def test_detect_kind(name: str, kind: str) -> None:
    assert detect_kind(name) == kind


def test_extract_text_file(tmp_path: Path) -> None:
    path = tmp_path / "note.txt"
    path.write_text("محتوى نصي", encoding="utf-8")
    result = extract_text(path)
    assert result.kind == "text"
    assert result.text == "محتوى نصي"
    assert result.backend == "plain-text"


def test_missing_file_raises(tmp_path: Path) -> None:
    with pytest.raises(OcrError, match="غير موجود"):
        extract_text(tmp_path / "ghost.png")


def test_unsupported_suffix_raises(tmp_path: Path) -> None:
    path = tmp_path / "file.docx"
    path.write_bytes(b"x")
    with pytest.raises(OcrError, match="غير مدعومة"):
        extract_text(path)


def test_image_with_stub_backend(tmp_path: Path) -> None:
    path = make_image(tmp_path / "scan.png")
    backend = StubBackend("سطر أول\nسطر ثانٍ")
    result = extract_text(path, backend=backend)
    assert result.kind == "image"
    assert result.backend == "stub"
    assert "سطر أول" in result.text
    assert result.chars > 0
    assert backend.calls and backend.calls[0][1] == DEFAULT_LANG


def test_unavailable_backend_raises_actionable_error(tmp_path: Path) -> None:
    path = make_image(tmp_path / "scan.png")
    with pytest.raises(OcrUnavailable, match="غير متاح"):
        extract_text(path, backend=StubBackend(available=False))


def test_empty_extraction_is_warned(tmp_path: Path) -> None:
    path = make_image(tmp_path / "blank.png")
    result = extract_text(path, backend=StubBackend(""))
    assert result.empty
    assert result.warnings


def test_language_is_forwarded(tmp_path: Path) -> None:
    path = make_image(tmp_path / "scan.png")
    backend = StubBackend("x")
    extract_text(path, backend=backend, lang="ara")
    assert backend.calls[0][1] == "ara"


# ================= المعالجة المسبقة (Pillow حقيقي) =================

def test_prepare_image_grayscale_and_upscale(tmp_path: Path) -> None:
    path = make_image(tmp_path / "small.png", (100, 50))
    raw = prepare_image(path)

    from io import BytesIO

    from PIL import Image

    with Image.open(BytesIO(raw)) as prepared:
        assert prepared.mode == "L"  # رمادي
        # التكبير محدود بـ 4× لتفادي نفخ الصور الصغيرة جداً
        assert prepared.width == min(MIN_WIDTH_FOR_OCR, 100 * 4)
        assert prepared.height >= 50


def test_prepare_image_rejects_non_image(tmp_path: Path) -> None:
    path = tmp_path / "fake.png"
    path.write_text("لست صورة", encoding="utf-8")
    with pytest.raises(OcrError, match="تعذّر قراءة"):
        prepare_image(path)


def test_prepare_image_rejects_wrong_suffix(tmp_path: Path) -> None:
    path = tmp_path / "file.pdf"
    path.write_bytes(b"%PDF")
    with pytest.raises(OcrError, match="صيغة"):
        prepare_image(path)


# ================= PDF =================

@pypdf_installed
def test_pdf_text_layer_is_extracted(tmp_path: Path) -> None:
    path = make_pdf(tmp_path / "doc.pdf", "Hello OCR")
    result = extract_text(path)
    assert result.kind == "pdf"
    assert result.pages == 1
    assert "Hello OCR" in result.text
    assert result.backend == "pypdf"


@pypdf_installed
def test_pdf_page_marker_in_text(tmp_path: Path) -> None:
    path = make_pdf(tmp_path / "doc.pdf", "Page one")
    text, pages = extract_pdf_text(path)
    assert pages == 1
    assert "[صفحة 1]" in text


def test_pdf_without_pypdf_is_actionable(monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> None:
    from src.ocr import engine

    monkeypatch.setattr(engine, "_load_pypdf", lambda: None)
    path = tmp_path / "doc.pdf"
    path.write_bytes(b"%PDF-1.4\n")
    with pytest.raises(OcrUnavailable, match="pypdf"):
        extract_text(path)


# ================= الدفعات =================

def test_extract_many_continues_after_failure(tmp_path: Path) -> None:
    good = tmp_path / "a.txt"
    good.write_text("نص", encoding="utf-8")
    results, failures = extract_many([good, tmp_path / "missing.txt"])
    assert len(results) == 1
    assert len(failures) == 1


def test_ingest_results_adds_citations(tmp_path: Path) -> None:
    corpus = CorpusIndex(tmp_path / "c.jsonl")
    result = OcrResult(
        text="فاتورة رقم 42 بمبلغ 100", backend="stub", kind="image", source="/x/invoice.png"
    )
    report = ingest_results([result], corpus)

    assert report.added_chunks > 0
    assert corpus.sources() == ["ocr:invoice.png"]
    hit = corpus.search("فاتورة رقم")
    assert hit
    assert "[ocr:invoice.png#L" in hit[0].render()
    assert report.summary().startswith("OCR:")


def test_empty_result_is_not_ingested(tmp_path: Path) -> None:
    corpus = CorpusIndex(tmp_path / "c.jsonl")
    report = ingest_results(
        [OcrResult(text="  ", backend="stub", kind="image", source="blank.png")], corpus
    )
    assert len(corpus) == 0
    assert report.entries[0].ok is False


def test_ingest_paths_end_to_end(tmp_path: Path) -> None:
    image = make_image(tmp_path / "scan.png")
    corpus = CorpusIndex(tmp_path / "c.jsonl")
    report = ingest_paths([image], corpus, backend=StubBackend("محتوى الصفحة الممسوحة"))

    assert report.entries[0].ok
    assert report.failures == {}
    assert corpus.search("الصفحة الممسوحة")


def test_source_name_uses_basename() -> None:
    result = OcrResult(text="x", backend="s", kind="image", source="/deep/dir/a.png")
    assert source_name(result) == "ocr:a.png"


def test_health_reports_honestly() -> None:
    info = health()
    assert isinstance(info["ready"], bool)
    assert {item["name"] for item in info["backends"]} == {"tesseract", "pypdf"}


# ================= المحرّك الحقيقي =================

def test_tesseract_availability_is_boolean() -> None:
    assert isinstance(TesseractBackend().available(), bool)


def test_tesseract_missing_gives_install_instructions(tmp_path: Path) -> None:
    backend = TesseractBackend()
    if backend.available():
        pytest.skip("Tesseract مثبت — لا يمكن اختبار الغياب")
    path = make_image(tmp_path / "scan.png")
    with pytest.raises(OcrUnavailable, match="tesseract-ocr"):
        backend.extract(path)


# ================= أداة داخل السجل =================

def test_tool_registered_as_read_scope() -> None:
    registry = build_default_registry()
    spec = registry.get("ocr_extract")
    assert spec is not None
    assert spec.risk is ToolRisk.READ
    assert spec.scope == "fs.read"
    assert spec.path_params == ("path",)


def test_tool_blocks_path_escape(tmp_path: Path) -> None:
    outside = tmp_path.parent / "outside.png"
    make_image(outside)
    policy = PolicyEngine(allowed_roots=[tmp_path / "inner"])
    registry = build_default_registry(ToolContext(policy=policy, quota=QuotaGuard()))
    result = asyncio.run(registry.invoke("ocr_extract", {"path": "../outside.png"}))
    assert not result.ok
    assert result.denied


def test_tool_uses_backend_and_returns_text(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    from src.ocr import engine

    monkeypatch.setattr(engine, "default_backend", lambda: StubBackend("نص الأداة"))
    make_image(tmp_path / "scan.png")
    policy = PolicyEngine(allowed_roots=[tmp_path])
    registry = build_default_registry(ToolContext(policy=policy, quota=QuotaGuard()))
    result = asyncio.run(registry.invoke("ocr_extract", {"path": "scan.png"}))
    assert result.ok, result.error
    assert "نص الأداة" in result.output
    assert "stub" in result.output


def test_tool_reports_missing_engine(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    from src.ocr import engine

    monkeypatch.setattr(engine, "default_backend", lambda: StubBackend(available=False))
    make_image(tmp_path / "scan.png")
    policy = PolicyEngine(allowed_roots=[tmp_path])
    registry = build_default_registry(ToolContext(policy=policy, quota=QuotaGuard()))
    result = asyncio.run(registry.invoke("ocr_extract", {"path": "scan.png"}))
    assert not result.ok
    assert "OcrUnavailable" in result.error


def test_tool_empty_text_is_error(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    from src.ocr import engine

    monkeypatch.setattr(engine, "default_backend", lambda: StubBackend("   "))
    make_image(tmp_path / "scan.png")
    policy = PolicyEngine(allowed_roots=[tmp_path])
    registry = build_default_registry(ToolContext(policy=policy, quota=QuotaGuard()))
    result = asyncio.run(registry.invoke("ocr_extract", {"path": "scan.png"}))
    assert not result.ok


def test_ocr_handler_defined() -> None:
    assert callable(ocr_extract)


# ================= CLI =================

def test_cli_parser_extract() -> None:
    args = build_parser().parse_args(["extract", "a.png", "b.pdf", "--lang", "ara"])
    assert args.paths == ["a.png", "b.pdf"]
    assert args.lang == "ara"


def test_cli_health_returns_zero() -> None:
    assert cli_main(["health"]) == 0


def test_build_knowledge_ingests_ocr_text(tmp_path: Path) -> None:
    """مسار حقيقي بلا Tesseract: ملف نصي يُمرَّر عبر --ocr إلى الفهرس."""
    import argparse

    from src.main import build_knowledge

    note = tmp_path / "scan.txt"
    note.write_text("نتيجة مسح ضوئي: فاتورة رقم 7", encoding="utf-8")
    args = argparse.Namespace(
        knowledge=str(tmp_path / "corpus.jsonl"), learn=None, learn_limit=3, ocr=[str(note)]
    )
    corpus = build_knowledge(args)

    assert corpus is not None
    assert corpus.sources() == ["ocr:scan.txt"]
    assert corpus.search("فاتورة رقم")


def test_build_knowledge_returns_none_without_sources() -> None:
    import argparse

    from src.main import build_knowledge

    assert build_knowledge(argparse.Namespace(knowledge=None, learn=None, ocr=None)) is None


def test_build_knowledge_survives_ocr_failure(tmp_path: Path, monkeypatch) -> None:
    import argparse

    from src.main import build_knowledge
    from src.ocr import pipeline

    def boom(*args, **kwargs):
        raise RuntimeError("محرّك معطوب")

    monkeypatch.setattr(pipeline, "ingest_paths", boom)
    args = argparse.Namespace(
        knowledge=str(tmp_path / "corpus.jsonl"), learn=None, learn_limit=3, ocr=["x.png"]
    )
    corpus = build_knowledge(args)
    assert corpus is not None
    assert len(corpus) == 0
