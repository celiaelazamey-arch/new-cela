"""مجموعة اختبارات خط أنابيب استيعاب الملفات الطبقي (Corpus متعدد الصيغ، عربي RTL، وملفات تالفة/هجومية)."""
from __future__ import annotations

import struct
import zipfile
from pathlib import Path

from PIL import Image
from pypdf import PdfWriter

from src.ingestion import (
    IngestionLimits,
    IngestionPipeline,
    IngestionStatus,
    ingest_file,
    sanitize_filename,
)


def test_arabic_utf8_and_windows_1256_encoding_detection(tmp_path: Path) -> None:
    """اختبار فك الترميز العربي UTF-8 والترميز القديم Windows-1256 (cp1256) بدون تشويه الحروف."""
    utf8_file = tmp_path / "order_utf8.txt"
    utf8_file.write_text("أمر شغل مطبعة سلك سكرين — 500 تيشيرت قطن أسود", encoding="utf-8")
    res_utf8 = ingest_file(utf8_file, allowed_root=tmp_path)
    assert res_utf8.status == IngestionStatus.EXTRACTED.value
    assert res_utf8.language == "ar"
    assert "سلك سكرين" in res_utf8.text
    assert res_utf8.metadata["encoding"] == "utf-8"

    # ملف نصي عربي بترميز Windows-1256 القديم (يفشل في UTF-8 العادي)
    cp1256_file = tmp_path / "legacy_arabic.txt"
    cp1256_file.write_bytes("فاتورة مبيعات مطبعة الملابس — الجنيه المصري".encode("cp1256"))
    res_cp = ingest_file(cp1256_file, allowed_root=tmp_path)
    assert res_cp.status == IngestionStatus.EXTRACTED.value
    assert res_cp.language == "ar"
    assert "الجنيه المصري" in res_cp.text
    assert res_cp.metadata["encoding"] == "cp1256"
    assert any("legacy_encoding_detected" in w for w in res_cp.warnings)


def test_csv_tables_and_svg_xss_xxe_sanitization(tmp_path: Path) -> None:
    """اختبار استخراج جداول CSV وتطهير ملفات SVG من XSS و XXE."""
    csv_file = tmp_path / "films.csv"
    csv_file.write_text("كود الفيلم,اللون,الحرير\nFLM-01,أبيض أرضية,55T\n", encoding="utf-8")
    res_csv = ingest_file(csv_file, allowed_root=tmp_path)
    assert res_csv.status == IngestionStatus.EXTRACTED.value
    assert len(res_csv.tables) == 1
    assert res_csv.tables[0][1][0] == "FLM-01"

    svg_file = tmp_path / "design.svg"
    svg_file.write_text(
        '<!DOCTYPE svg [<!ENTITY xxe SYSTEM "file:///etc/passwd">]>\n'
        '<svg xmlns="http://www.w3.org/2000/svg" onload="alert(1)">\n'
        "<script> evil() </script>\n"
        "<text>تصميم تيشيرت</text>\n"
        "</svg>",
        encoding="utf-8",
    )
    res_svg = ingest_file(svg_file, allowed_root=tmp_path)
    assert res_svg.status == IngestionStatus.EXTRACTED.value
    assert "<script>" not in res_svg.text
    assert "<!ENTITY" not in res_svg.text
    assert "تصميم تيشيرت" in res_svg.text
    assert any("xxe_entity_blocked" in w for w in res_svg.warnings)
    assert any("xss_vector_sanitized" in w for w in res_svg.warnings)


def test_magic_bytes_mismatch_and_executable_blocking(tmp_path: Path) -> None:
    """لا تثق بالامتداد: كشف ملف تنفيذي (MZ) متخفٍّ بامتداد .pdf وحظره فوراً."""
    fake_pdf = tmp_path / "invoice.pdf"
    fake_pdf.write_bytes(b"MZ\x90\x00\x03\x00\x00\x00FakeExecutablePayload")
    res = ingest_file(fake_pdf, allowed_root=tmp_path)
    assert res.status == IngestionStatus.ZP_OR_EXE_BLOCKED.value
    assert res.mime == "application/x-msdownload"
    assert any("suspicious_extension_mismatch" in w for w in res.warnings)
    assert any("executable_blocked" in w for w in res.warnings)


def test_scanned_pdf_detection_and_docx_extraction(tmp_path: Path) -> None:
    """كشف ملف PDF ممسوح ضوئياً (فارغ النص) واستخراج ملف Word (.docx) عربي."""
    # 1. PDF بصفحة فارغة (يحاكي PDF ممسوحاً ضوئياً بلا طبقة نصية)
    scanned_pdf = tmp_path / "scanned_ticket.pdf"
    writer = PdfWriter()
    writer.add_blank_page(width=200, height=200)
    with scanned_pdf.open("wb") as f:
        writer.write(f)

    res_pdf = ingest_file(scanned_pdf, allowed_root=tmp_path)
    assert res_pdf.status == IngestionStatus.PARTIAL.value
    assert res_pdf.pages == 1
    assert any("scanned_pdf_needs_ocr" in w for w in res_pdf.warnings)

    # 2. ملف DOCX حقيقي مبني بـ ZIP + word/document.xml
    docx_file = tmp_path / "contract.docx"
    with zipfile.ZipFile(docx_file, "w") as zf:
        zf.writestr(
            "word/document.xml",
            '<?xml version="1.0" encoding="UTF-8"?>'
            '<w:document xmlns:w="http://schemas.openxmlformats.org/wordprocessingml/2006/main">'
            "<w:body><w:p><w:r><w:t>عقد توريد مطبعة ملابس سلك سكرين</w:t></w:r></w:p>"
            "<w:tbl><w:tr><w:tc><w:p><w:r><w:t>البند الأول</w:t></w:r></w:p></w:tc></w:tr></w:tbl>"
            "</w:body></w:document>",
        )
    res_docx = ingest_file(docx_file, allowed_root=tmp_path)
    assert res_docx.status == IngestionStatus.EXTRACTED.value
    assert res_docx.language == "ar"
    assert "سلك سكرين" in res_docx.text
    assert res_docx.tables == [[["البند الأول"]]]


def test_psd_header_and_print_image_dpi_extraction(tmp_path: Path) -> None:
    """اختبار استخراج ترويسة ملف فوتوشوب PSD (CMYK 4 قنوات) وفحص DPI لصور الطباعة."""
    psd_file = tmp_path / "design_cmyk.psd"
    # ترويسة PSD قانونية: 8BPS + version(1) + 6 reserved + channels(5: CMYK+Spot) + height(3508) + width(2480) + depth(8) + mode(4=CMYK)
    psd_header = b"8BPS" + struct.pack(">H6sHIIHH", 1, b"\x00" * 6, 5, 3508, 2480, 8, 4)
    psd_file.write_bytes(psd_header + b"\x00" * 32)

    res_psd = ingest_file(psd_file, allowed_root=tmp_path)
    assert res_psd.status == IngestionStatus.EXTRACTED.value
    assert res_psd.mime == "image/vnd.adobe.photoshop"
    assert res_psd.metadata["color_mode"] == "CMYK"
    assert res_psd.metadata["channels"] == 5
    assert res_psd.metadata["width"] == 2480
    assert res_psd.metadata["height"] == 3508

    # صورة PNG بدقة 72 DPI (أقل من 300 DPI المطلوبة للطباعة)
    low_dpi_png = tmp_path / "artwork_low.png"
    img = Image.new("RGB", (200, 150), color=(255, 0, 0))
    img.save(low_dpi_png, dpi=(72, 72))

    res_img = ingest_file(low_dpi_png, allowed_root=tmp_path)
    assert res_img.status == IngestionStatus.EXTRACTED.value
    assert res_img.metadata["width"] == 200
    assert any("low_print_dpi" in w for w in res_img.warnings)


def test_zip_bomb_and_archive_path_traversal_blocked(tmp_path: Path) -> None:
    """اختبار حظر أرشيفات ZIP التي تحتوي على Path Traversal أو تتجاوز عدد الملفات المسموح."""
    traversal_zip = tmp_path / "evil.zip"
    with zipfile.ZipFile(traversal_zip, "w") as zf:
        zf.writestr("../../etc/passwd", "root:x:0:0")

    res_trav = ingest_file(traversal_zip, allowed_root=tmp_path)
    assert res_trav.status == IngestionStatus.ZP_OR_EXE_BLOCKED.value
    assert any("archive_path_traversal" in w for w in res_trav.warnings)

    # أرشيف يتجاوز الحد الأقصى لعدد الملفات
    many_zip = tmp_path / "too_many.zip"
    with zipfile.ZipFile(many_zip, "w") as zf:
        for i in range(6):
            zf.writestr(f"file_{i}.txt", "ok")

    strict_limits = IngestionLimits(max_archive_entries=3)
    res_many = ingest_file(many_zip, allowed_root=tmp_path, limits=strict_limits)
    assert res_many.status == IngestionStatus.ZP_OR_EXE_BLOCKED.value
    assert any("zip_bomb_entries_exceeded" in w for w in res_many.warnings)


def test_corrupted_and_unknown_files_fallback_to_metadata_only_and_idempotency(tmp_path: Path) -> None:
    """الملفات التالفة أو المجهولة لا تسقط النظام بل تعود بـ metadata_only، مع اختبار Idempotency."""
    corrupt_pdf = tmp_path / "corrupt.pdf"
    corrupt_pdf.write_bytes(b"%PDF-1.7\nCorruptedBinaryStreamWithoutXref")
    res_corrupt = ingest_file(corrupt_pdf, allowed_root=tmp_path)
    assert res_corrupt.status == IngestionStatus.METADATA_ONLY.value
    assert res_corrupt.id.startswith("sha256:")
    assert any("parser_fallback_to_metadata" in w for w in res_corrupt.warnings)

    unknown_bin = tmp_path / "mystery.unknownfmt"
    unknown_bin.write_bytes(b"\x01\x02\x03\x04CustomProprietaryFormat")

    pipeline = IngestionPipeline()
    res1, cached1 = pipeline.process(unknown_bin, allowed_root=tmp_path)
    assert cached1 is False
    assert res1.status == IngestionStatus.METADATA_ONLY.value
    assert res1.to_dict()["status"] == "metadata_only"

    # استدعاء ثانٍ لنفس المحتوى -> يعود من الذاكرة فوراً (Idempotency)
    res2, cached2 = pipeline.process(unknown_bin, allowed_root=tmp_path)
    assert cached2 is True
    assert res2.id == res1.id

    metrics = pipeline.extraction_metrics()
    assert "application/octet-stream" in metrics
    assert sanitize_filename("../../evil<script>.pdf") == "evilscript.pdf"
