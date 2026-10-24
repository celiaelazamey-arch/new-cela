"""
مجموعة اختبارات خط أنابيب استيعاب الملفات الطبقي (v2.1 — Parametrized Corpus + اختبارات منع الإيجابيات الكاذبة).
كل حالة هجومية وكل صيغة تعمل كاختبار مستقل عبر `@pytest.mark.parametrize` بحيث لا يخفي فشل واحد بقية الحالات.
"""
from __future__ import annotations

import io
import socket
import struct
import sys
import zipfile
from pathlib import Path

import pytest
from PIL import Image
from pypdf import PdfWriter

from src.ingestion import (
    PIPELINE_VERSION,
    IngestionLimits,
    IngestionPipeline,
    IngestionStatus,
    ingest_file,
    ingest_file_isolated,
    safe_xml_fromstring,
    sanitize_filename,
    sanitize_svg_or_html,
)
from src.ingestion.pipeline import (
    EICAR_SIGNATURE,
    _cli_worker_main,
    _disable_worker_network,
    detect_magic_mime,
)


# ==============================================================================
# 1. اختبارات Parametrized لكشف البصمات الثنائية (Magic Bytes — 22 صيغة مستقلة)
# ==============================================================================
@pytest.mark.parametrize(
    ("head_bytes", "filename", "expected_mime"),
    [
        (b"%PDF-1.7\n", "doc.pdf", "application/pdf"),
        (b"%PDF-1.7\n", "artwork.ai", "application/pdf"),
        (b"\x89PNG\r\n\x1a\n", "img.png", "image/png"),
        (b"\xff\xd8\xff\xe0", "photo.jpg", "image/jpeg"),
        (b"GIF89a\x01\x00", "anim.gif", "image/gif"),
        (b"RIFF\x00\x00\x00\x00WEBP", "pic.webp", "image/webp"),
        (b"BM\x00\x00\x00\x00", "bitmap.bmp", "image/bmp"),
        (b"II*\x00\x08\x00", "scan.tif", "image/tiff"),
        (b"8BPS\x00\x01", "design.psd", "image/vnd.adobe.photoshop"),
        (b"%!PS-Adobe-3.0", "vector.eps", "application/postscript"),
        (b"SQLite format 3\x00", "data.sqlite", "application/vnd.sqlite3"),
        (b"\x1f\x8b\x08\x00", "archive.gz", "application/gzip"),
        (b"7z\xbc\xaf\x27\x1c\x00", "archive.7z", "application/x-7z-compressed"),
        (b"Rar!\x1a\x07\x00", "archive.rar", "application/vnd.rar"),
        (b"\x00" * 257 + b"ustar\x00", "archive.tar", "application/x-tar"),
        (b"MZ\x90\x00\x03", "app.exe", "application/x-msdownload"),
        (b"\x7fELF\x02\x01", "bin.elf", "application/x-elf"),
        (b"\xfe\xed\xfa\xcf\x00", "macos.macho", "application/x-mach-binary"),
        (b"\xca\xfe\xba\xbe\x00", "Main.class", "application/x-java-class"),
        (b"\x00asm\x01\x00\x00\x00", "mod.wasm", "application/wasm"),
        (b"#!/bin/sh\necho hi", "run.sh", "application/x-sh"),
        (b"ID3\x03\x00", "track.mp3", "audio/mpeg"),
        (b"fLaC\x00\x00", "lossless.flac", "audio/flac"),
        (b"OggS\x00\x02", "voice.ogg", "audio/ogg"),
        (b"\x00\x00\x00\x18ftypmp42", "clip.mp4", "video/mp4"),
        (b"wOFF\x00\x01", "font.woff", "font/woff"),
        (b"wOF2\x00\x01", "font.woff2", "font/woff2"),
        (b"OTTO\x00\x01", "font.otf", "font/otf"),
    ],
)
def test_magic_mime_detection_parametrized(
    head_bytes: bytes, filename: str, expected_mime: str
) -> None:
    detected, warnings = detect_magic_mime(head_bytes, filename)
    assert detected == expected_mime
    assert warnings == []


# ==============================================================================
# 2. اختبارات Parametrized لمعقّم SVG/HTML بالقائمة البيضاء (9 حمولات التفاف مستقلة)
# ==============================================================================
@pytest.mark.parametrize(
    ("attack_id", "svg_payload", "forbidden_substring"),
    [
        (
            "script_mixed_case",
            '<svg xmlns="http://www.w3.org/2000/svg"><sCrIpT>alert(1)</sCrIpT><text>آمن</text></svg>',
            "alert(1)",
        ),
        (
            "foreign_object_iframe",
            '<svg><foreignObject><iframe src="javascript:alert(2)"></iframe></foreignObject><text>آمن</text></svg>',
            "<foreignobject",
        ),
        (
            "use_external_href",
            '<svg><use href="https://evil.example/x.svg#payload" xlink:href="javascript:alert(3)"/><text>آمن</text></svg>',
            "<use",
        ),
        (
            "image_javascript_href",
            '<svg><image href="javascript:alert(4)"/><text>آمن</text></svg>',
            "<image",
        ),
        (
            "animate_href_mutation",
            '<svg><a href="#ok"><animate attributeName="href" to="javascript:alert(5)"/><text>آمن</text></a></svg>',
            "<animate",
        ),
        (
            "set_onload_mutation",
            '<svg><set attributeName="onload" to="alert(6)"/><text>آمن</text></svg>',
            "<set",
        ),
        (
            "entity_encoded_javascript_uri",
            '<svg><a xlink:href="&#106;av&#x09;ascript:alert(7)"><text>آمن</text></a></svg>',
            "javascript",
        ),
        (
            "data_uri_html",
            '<svg><a href="data:text/html,&lt;script&gt;alert(8)&lt;/script&gt;"><text>آمن</text></a></svg>',
            "data:text/html",
        ),
        (
            "css_style_url_and_expression",
            '<svg><rect width="10" height="10" style="fill: #ff0000; background: url(javascript:alert(9))" /><text>آمن</text></svg>',
            "url(",
        ),
    ],
)
def test_svg_allowlist_sanitizer_blocks_each_vector(
    attack_id: str, svg_payload: str, forbidden_substring: str
) -> None:
    cleaned, warnings = sanitize_svg_or_html(svg_payload)
    assert forbidden_substring not in cleaned.lower(), f"Failed vector: {attack_id}"
    assert "آمن" in cleaned
    assert any("xss_vector_sanitized" in w for w in warnings)


def test_svg_allows_safe_css_and_adds_noopener_and_blocks_xxe(tmp_path: Path) -> None:
    """التأكد من السماح بخصائص CSS الآمنة للطباعة (fill/stroke) وإضافة rel=noopener وحظر XXE."""
    svg_file = tmp_path / "print_safe.svg"
    svg_file.write_text(
        '<!DOCTYPE svg [<!ENTITY xxe SYSTEM "file:///etc/passwd">]>\n'
        '<svg xmlns="http://www.w3.org/2000/svg">'
        '<rect width="50" height="50" style="fill: #112233; stroke-width: 2px"/>'
        '<a href="https://example.com/spec"><text>تصميم &amp; طباعة &xxe;</text></a>'
        "</svg>",
        encoding="utf-8",
    )
    res = ingest_file(svg_file, allowed_root=tmp_path)
    assert res.status == IngestionStatus.EXTRACTED.value
    assert 'style="fill: #112233; stroke-width: 2px"' in res.text
    assert 'rel="noopener noreferrer"' in res.text
    assert "<!entity" not in res.text.lower()
    assert any("xxe_entity_blocked" in w for w in res.warnings)
    assert res.metadata["recommended_headers"]["Content-Disposition"] == "attachment"  # type: ignore[index]


# ==============================================================================
# 3. اختبارات منع الإيجابيات الكاذبة (False Positives) لملفات Office والنصوص التقنية
# ==============================================================================
def test_real_docx_with_embedded_chart_and_data_descriptor_succeeds(tmp_path: Path) -> None:
    """
    ملف DOCX حقيقي يحتوي على:
      1. مخطط بياني مضمّن (`word/embeddings/Microsoft_Excel_Worksheet1.xlsx` — وهو ZIP داخل DOCX!).
      2. ترويسة LFH تستخدم Data Descriptor (`lfh_uncomp = 0` و `lfh_flags |= 0x08` كما في LibreOffice/Google Docs).
    يجب أن ينجح استخراجه (`status="extracted"`) دون حظره كـ Nested Zip Bomb أو Spoofed Header!
    """
    embedded_xlsx = tmp_path / "chart.xlsx"
    with zipfile.ZipFile(embedded_xlsx, "w") as zf:
        zf.writestr("xl/sharedStrings.xml", "<sst><si><t>Chart Data</t></si></sst>")

    docx_with_chart = tmp_path / "report_with_chart.docx"
    with zipfile.ZipFile(docx_with_chart, "w", compression=zipfile.ZIP_DEFLATED) as zf:
        zf.writestr(
            "word/document.xml",
            '<?xml version="1.0" encoding="UTF-8"?>'
            '<w:document xmlns:w="http://schemas.openxmlformats.org/wordprocessingml/2006/main">'
            "<w:body><w:p><w:r><w:t>تقرير مبيعات المطبعة مع رسم بياني مضمّن</w:t></w:r></w:p></w:body>"
            "</w:document>",
        )
        zf.write(embedded_xlsx, arcname="word/embeddings/Microsoft_Excel_Worksheet1.xlsx")

    # محاكاة سلوك Google Docs / LibreOffice بوضع 0 في حقول حجم LFH مع تفعيل بت Data Descriptor (0x08)
    raw = bytearray(docx_with_chart.read_bytes())
    assert raw[:4] == b"PK\x03\x04"
    flags = struct.unpack("<H", raw[6:8])[0] | 0x08
    raw[6:8] = struct.pack("<H", flags)
    raw[18:22] = struct.pack("<I", 0)  # lfh_comp = 0
    raw[22:26] = struct.pack("<I", 0)  # lfh_uncomp = 0
    docx_with_chart.write_bytes(bytes(raw))

    res = ingest_file(docx_with_chart, allowed_root=tmp_path)
    assert res.status == IngestionStatus.EXTRACTED.value
    assert "تقرير مبيعات المطبعة" in res.text
    assert any("ooxml_embedded_object_skipped" in w for w in res.warnings)


def test_technical_txt_mentioning_powershell_is_not_falsely_blocked(tmp_path: Path) -> None:
    """
    ملف `.txt` تقني يشرح أوامر `powershell` أو `@echo off` يجب ألا يُحظر بالخطأ (`status="extracted"`)،
    بينما ملف `.ps1` أو `.bat` بنفس المحتوى يُحظر فوراً (`status="blocked"`).
    """
    tech_notes = tmp_path / "devops_notes.txt"
    tech_notes.write_text(
        "ملاحظة تقنية: لتشغيل الفحص على ويندوز استخدم powershell أو @echo off في بيئة الاختبار.",
        encoding="utf-8",
    )
    res_txt = ingest_file(tech_notes, allowed_root=tmp_path)
    assert res_txt.status == IngestionStatus.EXTRACTED.value
    assert res_txt.mime == "text/plain"
    assert "ملاحظة تقنية" in res_txt.text
    assert any("script_keyword_warning" in w for w in res_txt.warnings)

    evil_ps1 = tmp_path / "payload.ps1"
    evil_ps1.write_text("powershell Invoke-Expression evil", encoding="utf-8")
    res_ps1 = ingest_file(evil_ps1, allowed_root=tmp_path)
    assert res_ps1.status == IngestionStatus.ZP_OR_EXE_BLOCKED.value


# ==============================================================================
# 4. اختبارات Parametrized لدفاعات XML (Billion Laughs, Depth, Elements, Size)
# ==============================================================================
@pytest.mark.parametrize(
    ("xml_payload", "limits", "expected_err"),
    [
        (
            b'<?xml version="1.0"?><!DOCTYPE lolz [<!ENTITY lol "lol">]><lolz>&lol;</lolz>',
            IngestionLimits(),
            "xml_bomb_entity_or_dtd_blocked",
        ),
        (
            ("<a>" * 10 + "x" + "</a>" * 10).encode("utf-8"),
            IngestionLimits(max_xml_depth=4),
            "xml_bomb_depth_exceeded",
        ),
        (
            ("<root>" + "<i/>" * 15 + "</root>").encode("utf-8"),
            IngestionLimits(max_xml_elements=8),
            "xml_bomb_elements_exceeded",
        ),
        (
            b"<root>0123456789</root>",
            IngestionLimits(max_xml_bytes=5),
            "xml_bomb_size_exceeded",
        ),
    ],
)
def test_defused_xml_guard_parametrized(
    xml_payload: bytes, limits: IngestionLimits, expected_err: str
) -> None:
    with pytest.raises(ValueError, match=expected_err):
        safe_xml_fromstring(xml_payload, limits=limits)


# ==============================================================================
# 5. اختبارات الأرشيفات الهجومية (3-level Nested ZIP, bzip2/lzma, Spoofed Header, Traversal)
# ==============================================================================
def test_zip_3_level_nested_and_bzip2_compression_rejected(tmp_path: Path) -> None:
    """رفض صريح لـ ZIP متداخل 3 مستويات ولطرق الضغط غير القياسية (bzip2 / lzma داخل ZIP)."""
    lvl3 = tmp_path / "lvl3.zip"
    with zipfile.ZipFile(lvl3, "w") as zf:
        zf.writestr("deep.txt", "level 3")
    lvl2 = tmp_path / "lvl2.zip"
    with zipfile.ZipFile(lvl2, "w") as zf:
        zf.write(lvl3, arcname="lvl3.zip")
    lvl1 = tmp_path / "lvl1.zip"
    with zipfile.ZipFile(lvl1, "w") as zf:
        zf.write(lvl2, arcname="lvl2.zip")

    res_nested = ingest_file(lvl1, allowed_root=tmp_path)
    assert res_nested.status == IngestionStatus.ZP_OR_EXE_BLOCKED.value
    assert any("zip_bomb_nested_archive" in w for w in res_nested.warnings)

    # ZIP يستخدم ضغط BZIP2 (غير مسموح — يُسمح فقط بـ STORED و DEFLATED)
    bzip2_zip = tmp_path / "bzip2_archive.zip"
    with zipfile.ZipFile(bzip2_zip, "w", compression=zipfile.ZIP_BZIP2) as zf:
        zf.writestr("data.txt", "compressed with bzip2")

    res_bz2 = ingest_file(bzip2_zip, allowed_root=tmp_path)
    assert res_bz2.status == IngestionStatus.ZP_OR_EXE_BLOCKED.value
    assert any("zip_unsupported_compression_method" in w for w in res_bz2.warnings)


def test_zip_spoofed_header_and_path_traversal_and_limits(tmp_path: Path) -> None:
    """اختبار كشف ترويسة ZIP المزوّرة واجتياز المسارات وتجاوز النسبة والعمق وعدد الملفات."""
    # 1. Path Traversal
    traversal_zip = tmp_path / "evil.zip"
    with zipfile.ZipFile(traversal_zip, "w") as zf:
        zf.writestr("../../etc/passwd", "root:x:0:0")
    res_trav = ingest_file(traversal_zip, allowed_root=tmp_path)
    assert res_trav.status == IngestionStatus.ZP_OR_EXE_BLOCKED.value
    assert any("archive_path_traversal" in w for w in res_trav.warnings)

    # 2. ترويسة ZIP مزوّرة (Spoofed Header): الملف الفعلي 200 بايت لكن الترويسة تدّعي 4 بايت
    spoofed_zip = tmp_path / "spoofed.zip"
    with zipfile.ZipFile(spoofed_zip, "w", compression=zipfile.ZIP_DEFLATED) as zf:
        zf.writestr("payload.txt", b"A" * 200)
    raw_zip = bytearray(spoofed_zip.read_bytes())
    while (pos := raw_zip.find(struct.pack("<I", 200))) != -1:
        raw_zip[pos : pos + 4] = struct.pack("<I", 4)
    spoofed_zip.write_bytes(bytes(raw_zip))

    res_spoofed = ingest_file(spoofed_zip, allowed_root=tmp_path)
    assert res_spoofed.status == IngestionStatus.ZP_OR_EXE_BLOCKED.value
    assert any("zip_bomb_spoofed_header" in w for w in res_spoofed.warnings)

    # 3. تعارض حجم LFH غير الصفري (10 بايت) مع Central Directory (150 بايت)
    lfh_mismatch = tmp_path / "lfh_mismatch.zip"
    with zipfile.ZipFile(lfh_mismatch, "w", compression=zipfile.ZIP_STORED) as zf:
        zf.writestr("doc.txt", b"B" * 150)
    raw_lfh = bytearray(lfh_mismatch.read_bytes())
    first_pos = raw_lfh.find(struct.pack("<I", 150))
    raw_lfh[first_pos : first_pos + 4] = struct.pack("<I", 10)
    lfh_mismatch.write_bytes(bytes(raw_lfh))
    res_lfh = ingest_file(lfh_mismatch, allowed_root=tmp_path)
    assert res_lfh.status == IngestionStatus.ZP_OR_EXE_BLOCKED.value
    assert any("zip_bomb_spoofed_header" in w for w in res_lfh.warnings)


# ==============================================================================
# 6. اختبارات العربية (UTF-8 / cp1256)، و Excel/Word/PDF، و PSD 8BIM، والصور
# ==============================================================================
def test_arabic_utf8_and_cp1256_and_office_formats(tmp_path: Path) -> None:
    """اختبار العربية UTF-8 و Windows-1256 واستخراج XLSX من Excel و Google Sheets."""
    cp1256_file = tmp_path / "legacy_arabic.txt"
    cp1256_file.write_bytes(
        "فاتورة مبيعات مطبعة الملابس الجاهزة — السعر بالجنيه المصري".encode("cp1256")
    )
    res_cp = ingest_file(cp1256_file, allowed_root=tmp_path)
    assert res_cp.status == IngestionStatus.EXTRACTED.value
    assert res_cp.language == "ar"
    assert res_cp.metadata["encoding"] == "cp1256"
    assert float(res_cp.metadata["encoding_confidence"]) >= 0.5  # type: ignore[arg-type]

    # ملف XLSX يجمع أسلوب Excel (sharedStrings + معادلات) وأسلوب Google Sheets (inlineStr)
    xlsx_file = tmp_path / "sheets.xlsx"
    with zipfile.ZipFile(xlsx_file, "w") as zf:
        zf.writestr("xl/sharedStrings.xml", "<sst><si><t>تيشيرت قطن</t></si></sst>")
        zf.writestr(
            "xl/worksheets/sheet1.xml",
            "<worksheet><sheetData>"
            '<row r="1">'
            '<c r="A1" t="s"><v>0</v></c>'
            '<c r="B1" t="inlineStr"><is><t>طباعة بلاستيزول</t></is></c>'
            '<c r="C1"><f>10*25</f><v>250</v></c>'
            "</row>"
            '<row r="2"><c r="A2" t="inlineStr"><is><t>صف ثان</t></is></c></row>'
            "</sheetData></worksheet>",
        )
    res_xlsx = ingest_file(xlsx_file, allowed_root=tmp_path)
    assert res_xlsx.status == IngestionStatus.EXTRACTED.value
    assert res_xlsx.tables[0][0] == ["تيشيرت قطن", "طباعة بلاستيزول", "250"]
    assert res_xlsx.metadata["has_formulas"] is True

    # اقتطاع XLSX -> حالة partial
    res_trunc = ingest_file(xlsx_file, allowed_root=tmp_path, limits=IngestionLimits(max_xlsx_rows=1))
    assert res_trunc.status == IngestionStatus.PARTIAL.value
    assert any("xlsx_truncated" in w for w in res_trunc.warnings)

    # PDF ممسوح ضوئياً -> حالة partial + scanned_pdf_needs_ocr
    scanned_pdf = tmp_path / "scanned.pdf"
    writer = PdfWriter()
    writer.add_blank_page(width=200, height=200)
    with scanned_pdf.open("wb") as f:
        writer.write(f)
    res_pdf = ingest_file(scanned_pdf, allowed_root=tmp_path)
    assert res_pdf.status == IngestionStatus.PARTIAL.value
    assert any("scanned_pdf_needs_ocr" in w for w in res_pdf.warnings)


def test_psd_8bim_spot_channels_and_resource_limits_and_print_dpi(tmp_path: Path) -> None:
    """اختبار قراءة PSD 8BIM (0x0415 و 0x03EE)، وحدود موارد PSD، وحساب effective_dpi."""
    psd_file = tmp_path / "design_spot.psd"
    psd_header = b"8BPS" + struct.pack(">H6sHIIHH", 1, b"\x00" * 6, 6, 3508, 2480, 8, 4)
    names_payload = b""
    for ch_name in ("Pantone 186 C", "White Underbase"):
        names_payload += struct.pack(">I", len(ch_name)) + ch_name.encode("utf-16-be")
    block_1045 = (
        b"8BIM"
        + struct.pack(">H", 0x0415)
        + b"\x00\x00"
        + struct.pack(">I", len(names_payload))
        + names_payload
    )
    psd_file.write_bytes(
        psd_header + struct.pack(">I", 0) + struct.pack(">I", len(block_1045)) + block_1045
    )
    res_psd = ingest_file(psd_file, allowed_root=tmp_path)
    assert res_psd.status == IngestionStatus.EXTRACTED.value
    assert res_psd.metadata["base_channels"] == 4
    assert res_psd.metadata["extra_channels"] == 2
    assert res_psd.metadata["channel_names"] == ["Pantone 186 C", "White Underbase"]

    # تجاوز سقف موارد PSD -> blocked
    res_psd_limit = ingest_file(
        psd_file,
        allowed_root=tmp_path,
        limits=IngestionLimits(max_psd_resource_bytes=10),
    )
    assert res_psd_limit.status == IngestionStatus.ZP_OR_EXE_BLOCKED.value
    assert any("psd_resources_limit_exceeded" in w for w in res_psd_limit.warnings)

    # صورة JPEG بلا وسم DPI + حساب effective_dpi
    jpg_file = tmp_path / "art.jpg"
    Image.new("RGB", (600, 600), color=(10, 20, 30)).save(jpg_file, format="JPEG")
    res_eff = ingest_file(
        jpg_file,
        allowed_root=tmp_path,
        limits=IngestionLimits(target_print_width_cm=25.4, target_print_height_cm=25.4),
    )
    assert res_eff.metadata["effective_dpi"] == [60, 60]
    assert any("low_print_dpi" in w for w in res_eff.warnings)


# ==============================================================================
# 7. اختبارات العزل في العملية الفرعية (Default Subprocess Isolation, Network Block, Cache TTL)
# ==============================================================================
def test_pipeline_enables_subprocess_isolation_by_default_and_manages_tenant_cache(
    tmp_path: Path,
) -> None:
    """
    التأكد من:
      1. `IngestionPipeline()` يفعّل `isolate_in_subprocess=True` افتراضياً.
      2. عزل الكاش بين المستأجرين + رفض `tenant_id` الفارغ + مسح كاش مستأجر (`clear_tenant_cache`) + `purge_expired_cache`.
      3. حظر فتح الشبكة داخل العامل المعزول (`_disable_worker_network`).
    """
    sample = tmp_path / "order.txt"
    sample.write_text("طلبية طباعة 300 تيشيرت بولو", encoding="utf-8")

    pipeline = IngestionPipeline(cache_ttl_seconds=60.0, max_cache_entries=2)
    assert pipeline._limits.isolate_in_subprocess is True

    res1, cached1 = pipeline.process(sample, allowed_root=tmp_path, tenant_id="tenant_cairo")
    assert cached1 is False
    assert res1.status == IngestionStatus.EXTRACTED.value
    assert "تيشيرت بولو" in res1.text

    _res2, cached2 = pipeline.process(sample, allowed_root=tmp_path, tenant_id="tenant_cairo")
    assert cached2 is True

    # مستأجر آخر لا يرى كاش المستأجر الأول
    _res3, cached3 = pipeline.process(sample, allowed_root=tmp_path, tenant_id="tenant_alex")
    assert cached3 is False
    assert PIPELINE_VERSION in pipeline.cache_key_for(res1.id, tenant_id="tenant_cairo")

    # رفض tenant_id فارغ أو غير صالح
    with pytest.raises(ValueError, match="invalid_tenant_id"):
        pipeline.cache_key_for(res1.id, tenant_id="   ")

    # حذف كاش المستأجر (امتثال حق المحو)
    deleted = pipeline.clear_tenant_cache("tenant_cairo")
    assert deleted == 1

    # انتهاء المهلة في العملية الفرعية يقتل مجموعة العمليات ويعيد blocked
    res_timeout = ingest_file_isolated(sample, allowed_root=tmp_path, timeout_seconds=0.0001)
    assert res_timeout.status == IngestionStatus.ZP_OR_EXE_BLOCKED.value
    assert res_timeout.metadata["blocked_reason"] == "parser_timeout"

    # اختبار تعطيل الشبكة داخل العامل الفرعي مع استعادة socket بعد الاختبار
    orig_socket = socket.socket
    orig_create = socket.create_connection
    try:
        _disable_worker_network()
        with pytest.raises(PermissionError, match="network_disabled_in_ingestion_sandbox"):
            socket.socket()
    finally:
        socket.socket = orig_socket  # type: ignore[misc]
        socket.create_connection = orig_create


def test_macros_eicar_and_worker_cli(tmp_path: Path) -> None:
    """اختبار حظر ماكرو OOXML و OLE2 وفحص EICAR وتشغيل العامل الفرعي."""
    macro_docx = tmp_path / "macro.docx"
    with zipfile.ZipFile(macro_docx, "w") as zf:
        zf.writestr("word/document.xml", "<w:document><w:body><w:t>Hi</w:t></w:body></w:document>")
        zf.writestr("word/vbaProject.bin", b"VBA")
    assert ingest_file(macro_docx, allowed_root=tmp_path).status == IngestionStatus.ZP_OR_EXE_BLOCKED.value

    ole_macro = tmp_path / "evil.doc"
    ole_macro.write_bytes(b"\xd0\xcf\x11\xe0\xa1\xb1\x1a\xe1" + b"\x00" * 20 + b"_VBA_PROJECT")
    assert ingest_file(ole_macro, allowed_root=tmp_path).status == IngestionStatus.ZP_OR_EXE_BLOCKED.value

    eicar = tmp_path / "eicar.com"
    eicar.write_bytes(EICAR_SIGNATURE)
    res_eicar = ingest_file(eicar, allowed_root=tmp_path)
    assert res_eicar.status == IngestionStatus.ZP_OR_EXE_BLOCKED.value
    assert res_eicar.parser == "av_scanner"

    txt_w = tmp_path / "w.txt"
    txt_w.write_text("worker test", encoding="utf-8")
    assert (
        _cli_worker_main(
            ["--worker-file", str(txt_w), "--worker-root", str(tmp_path), "--worker-limits", "{}"],
            apply_os_sandbox=False,
        )
        == 0
    )
    assert sanitize_filename("../../evil<script>.pdf") == "evilscript.pdf"


@pytest.mark.parametrize(
    ("ext", "content", "expected_parser"),
    [
        (".csv", "col1,col2\nval1,val2\n", "stdlib_csv"),
        (".tsv", "col1\tcol2\nval1\tval2\n", "stdlib_csv"),
        (".json", '{"a": 1}', "stdlib_json"),
        (".eml", "From: user@example.invalid\nSubject: Hi\n\nBody", "stdlib_email"),
        (".html", "<!DOCTYPE html><html><body><p>Hello</p></body></html>", "svg_html_allowlist_sanitizer"),
        (".xml", '<?xml version="1.0"?><root><item>1</item></root>', "svg_html_allowlist_sanitizer"),
    ],
)
def test_text_subformats_parametrized(
    tmp_path: Path, ext: str, content: str, expected_parser: str
) -> None:
    f = tmp_path / f"sample{ext}"
    f.write_text(content, encoding="utf-8")
    res = ingest_file(f, allowed_root=tmp_path)
    assert res.status == IngestionStatus.EXTRACTED.value
    assert res.parser == expected_parser


def test_archive_limits_pptx_psd_pascal_and_cache_expiration(tmp_path: Path) -> None:
    """اختبار حدود الأرشيف (depth, ratio, entries)، و PPTX، و PSD 0x03EE، وانتهاء صلاحية الكاش."""
    # 1. تجاوز عمق المجلدات
    deep_zip = tmp_path / "deep.zip"
    with zipfile.ZipFile(deep_zip, "w") as zf:
        zf.writestr("a/b/c/d/e/f.txt", "x")
    assert (
        ingest_file(deep_zip, allowed_root=tmp_path, limits=IngestionLimits(max_directory_depth=3)).status
        == IngestionStatus.ZP_OR_EXE_BLOCKED.value
    )

    # 2. تجاوز نسبة الضغط
    ratio_zip = tmp_path / "ratio.zip"
    with zipfile.ZipFile(ratio_zip, "w", compression=zipfile.ZIP_DEFLATED) as zf:
        zf.writestr("z.bin", b"\x00" * (2 * 1024 * 1024))
    assert (
        ingest_file(ratio_zip, allowed_root=tmp_path, limits=IngestionLimits(max_compression_ratio=5.0)).status
        == IngestionStatus.ZP_OR_EXE_BLOCKED.value
    )

    # 3. تجاوز عدد الملفات
    many_zip = tmp_path / "many.zip"
    with zipfile.ZipFile(many_zip, "w") as zf:
        for i in range(5):
            zf.writestr(f"f{i}.txt", "ok")
    assert (
        ingest_file(many_zip, allowed_root=tmp_path, limits=IngestionLimits(max_archive_entries=2)).status
        == IngestionStatus.ZP_OR_EXE_BLOCKED.value
    )

    # 4. PPTX و PSD Pascal (0x03EE + 0x0435) وصورة PNG بـ DPI منخفض وقنبلة صورة
    pptx_f = tmp_path / "slides.pptx"
    with zipfile.ZipFile(pptx_f, "w") as zf:
        zf.writestr("ppt/slides/slide1.xml", "<p:sld xmlns:p='http://x'><t>Slide 1</t></p:sld>")
    assert ingest_file(pptx_f, allowed_root=tmp_path).status == IngestionStatus.EXTRACTED.value

    psd_p = tmp_path / "p.psd"
    psd_hdr = b"8BPS" + struct.pack(">H6sHIIHH", 1, b"\x00" * 6, 5, 500, 500, 8, 4)
    pascal_data = b"\x09Spot Gold"
    b1006 = b"8BIM" + struct.pack(">H", 0x03EE) + b"\x00\x00" + struct.pack(">I", len(pascal_data)) + pascal_data + b"\x00"
    b1077 = b"8BIM" + struct.pack(">H", 0x0435) + b"\x00\x00" + struct.pack(">I", 2) + b"\x00\x01"
    res_block = b1006 + b1077
    psd_p.write_bytes(psd_hdr + struct.pack(">I", 0) + struct.pack(">I", len(res_block)) + res_block)
    res_psd = ingest_file(psd_p, allowed_root=tmp_path)
    assert res_psd.metadata["channel_names"] == ["Spot Gold"]
    assert res_psd.metadata["has_spot_channel_resources"] is True

    low_png = tmp_path / "low.png"
    Image.new("RGB", (100, 100), color=(1, 2, 3)).save(low_png, dpi=(72, 72))
    res_png = ingest_file(low_png, allowed_root=tmp_path)
    assert any("low_print_dpi" in w for w in res_png.warnings)

    res_bomb = ingest_file(low_png, allowed_root=tmp_path, limits=IngestionLimits(max_image_pixels=50))
    assert res_bomb.status == IngestionStatus.ZP_OR_EXE_BLOCKED.value

    # 5. اختبار انتهاء الصلاحية وتدوير الكاش ومقاييس الاستخراج
    pipeline = IngestionPipeline(isolate_in_subprocess=False, cache_ttl_seconds=0.01, max_cache_entries=1)
    pipeline.process(low_png, allowed_root=tmp_path, tenant_id="t1")
    assert pipeline.purge_expired_cache(now=1e12) == 1
    assert "image/png" in pipeline.extraction_metrics()

    # 6. اختبار تعارض الامتداد، الحدود الدقيقة للحجم، UTF-16، OLE2 النظيف، والـ Fallback للملفات التالفة
    fake_pdf = tmp_path / "fake.pdf"
    fake_pdf.write_bytes(b"MZ\x90\x00Executable")
    res_fake = ingest_file(fake_pdf, allowed_root=tmp_path)
    assert res_fake.status == IngestionStatus.ZP_OR_EXE_BLOCKED.value
    assert any("suspicious_extension_mismatch" in w for w in res_fake.warnings)

    over_f = tmp_path / "over.txt"
    over_f.write_bytes(b"A" * 51)
    assert (
        ingest_file(over_f, allowed_root=tmp_path, limits=IngestionLimits(max_file_size_bytes=50)).status
        == IngestionStatus.ZP_OR_EXE_BLOCKED.value
    )

    u16 = tmp_path / "u16.txt"
    u16.write_bytes("Hello".encode("utf-16"))
    assert ingest_file(u16, allowed_root=tmp_path).metadata["encoding"] == "utf-16"

    ole_clean = tmp_path / "clean.doc"
    ole_clean.write_bytes(b"\xd0\xcf\x11\xe0\xa1\xb1\x1a\xe1" + b"\x00" * 32)
    assert ingest_file(ole_clean, allowed_root=tmp_path).status == IngestionStatus.METADATA_ONLY.value

    corrupt_pdf = tmp_path / "bad.pdf"
    corrupt_pdf.write_bytes(b"%PDF-1.7\nBroken")
    assert ingest_file(corrupt_pdf, allowed_root=tmp_path).status == IngestionStatus.METADATA_ONLY.value

    unk = tmp_path / "x.unknown"
    unk.write_bytes(b"\x01\x02\x03\x04")
    assert ingest_file(unk, allowed_root=tmp_path).status == IngestionStatus.METADATA_ONLY.value
    assert (
        ingest_file(u16, allowed_root=tmp_path, av_scanner=lambda _p: (False, "TestSig")).status
        == IngestionStatus.ZP_OR_EXE_BLOCKED.value
    )



def test_sanitizer_uri_canonical_case_and_balanced_markup() -> None:
    """Regression: reject backslash network-paths, preserve SVG camelCase, and balance markup."""
    from src.ingestion.pipeline import _is_safe_uri

    assert not _is_safe_uri("/\\\\evil.example/path")
    sanitized, _warnings = sanitize_svg_or_html(
        '<svg viewBox="0 0 10 10"><linearGradient id="g"><stop offset="0"/></linearGradient>'
        '<a href="https://example.invalid">ok</a></svg></div><a>unclosed'
    )
    assert "viewBox=" in sanitized
    assert "<linearGradient" in sanitized and "</linearGradient>" in sanitized
    assert "cela-g" in sanitized
    assert sanitized.count("<a ") == sanitized.count("</a>")
    assert "href=\"https://example.invalid\"" in sanitized
    external_svg, _ = sanitize_svg_or_html('<svg><svg href="https://evil.invalid/a.svg"/></svg>')
    assert "https://evil.invalid" not in external_svg


def test_worker_result_schema_rejects_invalid_status_and_size() -> None:
    from src.ingestion.pipeline import IngestionResult

    payload = {
        "id": "sha256:abc", "name": "sample.txt", "mime": "text/plain", "size": 1,
        "status": "invented", "parser": "text", "text": "x",
    }
    with pytest.raises(ValueError, match="unknown status"):
        IngestionResult.from_dict(payload)
    payload["status"] = IngestionStatus.EXTRACTED.value
    payload["size"] = True
    with pytest.raises(ValueError, match="invalid size"):
        IngestionResult.from_dict(payload)


@pytest.mark.parametrize(
    ("untrusted", "must_not_appear"),
    [
        ('<a href="/\\\\evil.example">link</a>', "/\\\\evil.example"),
        ('<svg src="https://evil.example/a.svg"/>', "https://evil.example"),
        ('</div></div><a>unclosed', "</div>"),
        ('<svg><script>alert(1)</script><text>safe</text></svg>', "alert(1)"),
    ],
    ids=["backslash-network-path", "external-resource-src", "unbalanced-tags", "script-subtree"],
)
def test_sanitizer_rejects_uri_and_markup_attacks(untrusted: str, must_not_appear: str) -> None:
    sanitized, _warnings = sanitize_svg_or_html(untrusted)
    assert must_not_appear not in sanitized


@pytest.mark.parametrize(
    ("markup", "canonical"),
    [
        ('<svg viewbox="0 0 1 1"></svg>', "viewBox="),
        ('<lineargradient><stop/></lineargradient>', "<linearGradient>"),
        ('<svg id="root"></svg>', 'id="cela-root"'),
    ],
    ids=["viewbox-case", "svg-tag-case", "dom-clobber-id-prefix"],
)
def test_sanitizer_preserves_canonical_svg_and_prefixes_ids(markup: str, canonical: str) -> None:
    sanitized, _warnings = sanitize_svg_or_html(markup)
    assert canonical in sanitized


@pytest.mark.parametrize(
    ("attack", "expected_error"),
    [("duplicate_name", "archive_duplicate_entry"), ("encrypted_flag", "zip_encrypted_entry")],
    ids=["duplicate-normalized-name", "encrypted-entry"],
)
def test_zip_safety_rejects_duplicate_and_encrypted_entries(attack: str, expected_error: str) -> None:
    from src.ingestion.pipeline import _check_zip_safety

    stream = io.BytesIO()
    with zipfile.ZipFile(stream, "w", compression=zipfile.ZIP_STORED) as archive:
        archive.writestr("sample.txt", "one")
        if attack == "duplicate_name":
            archive.writestr("sample.txt", "two")
    raw = bytearray(stream.getvalue())
    if attack == "encrypted_flag":
        local_header = raw.index(b"PK\x03\x04")
        raw[local_header + 6] |= 1
        central_header = raw.index(b"PK\x01\x02")
        raw[central_header + 8] |= 1
    with (
        zipfile.ZipFile(io.BytesIO(raw)) as archive,
        pytest.raises(ValueError, match=expected_error),
    ):
        _check_zip_safety(archive, IngestionLimits(), verify_streams=False)


@pytest.mark.parametrize("worker_exit", [-9, -11, -6, 1], ids=["sigkill", "segfault", "abort", "exception"])
def test_isolated_worker_crashes_fail_closed_and_has_no_pipes(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, worker_exit: int
) -> None:
    import subprocess

    from src.ingestion import pipeline

    source = tmp_path / "source.txt"
    source.write_text("fixture", encoding="utf-8")

    class ExitedWorker:
        pid = 12345
        returncode = worker_exit

        def wait(self, timeout: float | None = None) -> int:
            return worker_exit

        def poll(self) -> int:
            return worker_exit

    def fake_popen(*_args: object, **kwargs: object) -> ExitedWorker:
        assert kwargs["stdout"] is subprocess.DEVNULL
        assert kwargs["stderr"] is subprocess.DEVNULL
        return ExitedWorker()

    monkeypatch.setattr(pipeline.subprocess, "Popen", fake_popen)
    result = ingest_file_isolated(source, allowed_root=tmp_path)
    assert result.status == IngestionStatus.ZP_OR_EXE_BLOCKED.value
    expected_reason = "parser_oom" if worker_exit == -9 else f"signal_{-worker_exit}" if worker_exit < 0 else "parser_crash"
    assert result.metadata["blocked_reason"] == expected_reason


def test_ingestion_requires_explicit_allowed_root(tmp_path: Path) -> None:
    source = tmp_path / "source.txt"
    source.write_text("fixture", encoding="utf-8")
    with pytest.raises(ValueError, match="allowed_root_required"):
        ingest_file(source)


@pytest.mark.parametrize(
    ("script", "timeout", "expected_reason"),
    [
        ("import os; os.abort()", 5.0, "signal_6"),
        ("raise SystemExit(76)", 5.0, "worker_result_efbig"),
        ("import os\nwhile True: os.write(1, b'x' * 1048576)", 0.25, "parser_timeout"),
        (
            "import os\np='{worker_out}'\nos.unlink(p)\nos.symlink('/etc/passwd', p)",
            5.0,
            "worker_result_invalid",
        ),
        pytest.param(
            "import resource\nresource.setrlimit(resource.RLIMIT_AS, (268435456, 268435456))\n" +
            "try: bytearray(1073741824)\nexcept MemoryError: raise SystemExit(75)",
            5.0, "parser_oom", marks=pytest.mark.skipif(sys.platform != "linux", reason="RLIMIT_AS probe is Linux-only"),
        ),
        pytest.param(
            "import os\nfrom src.ingestion.pipeline import _apply_worker_rlimits\n" +
            "_apply_worker_rlimits(512, 15)\nfd=os.open('{worker_out}', os.O_WRONLY)\n" +
            "while True: os.write(fd, b'x' * 1048576)",
            5.0, "parser_crash", marks=pytest.mark.skipif(sys.platform != "linux", reason="RLIMIT_FSIZE probe is Linux-only"),
        ),
    ],
    ids=["real-abort", "worker-efbig-exit", "multi-gigabyte-stdout", "result-symlink", "rlimit-as-oom", "rlimit-fsize-output"],
)
def test_real_worker_faults_are_bounded_and_fail_closed(
    tmp_path: Path, script: str, timeout: float, expected_reason: str
) -> None:
    source = tmp_path / "fault.txt"
    source.write_text("safe", encoding="utf-8")
    result = ingest_file_isolated(
        source,
        allowed_root=tmp_path,
        timeout_seconds=timeout,
        _worker_cmd=[sys.executable, "-c", script],
    )
    assert result.status == IngestionStatus.ZP_OR_EXE_BLOCKED.value
    assert result.metadata["blocked_reason"] == expected_reason


@pytest.mark.parametrize(
    "broad_root",
    ["/", "/tmp", str(Path.home()), "/home", "/etc", "/var", str(Path.cwd())],
    ids=["filesystem-root", "system-temp-root", "home-root", "home-parent", "etc", "var", "project-root"],
)
def test_allowed_root_rejects_broad_roots(tmp_path: Path, broad_root: str) -> None:
    source = tmp_path / "source.txt"
    source.write_text("safe", encoding="utf-8")
    with pytest.raises(ValueError, match="allowed_root_too_broad"):
        ingest_file(source, allowed_root=broad_root)


def test_allowed_root_rejects_symlink_escape(tmp_path: Path) -> None:
    allowed = tmp_path / "selected"
    allowed.mkdir()
    outside = tmp_path / "outside.txt"
    outside.write_text("outside", encoding="utf-8")
    link = allowed / "alias.txt"
    link.symlink_to(outside)
    with pytest.raises(ValueError, match="file_outside_allowed_root_after_realpath"):
        ingest_file(link, allowed_root=allowed)


def test_worker_serialization_truncates_result_under_fsize_cap(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    import json

    from src.ingestion import pipeline

    result = pipeline.IngestionResult(
        id="sha256:test", name="large.txt", mime="text/plain", size=6_000_000,
        status=IngestionStatus.EXTRACTED.value, parser="test", language="en", text="x" * 6_000_000,
    )
    monkeypatch.setattr(pipeline, "ingest_file", lambda *_args, **_kwargs: result)
    source = tmp_path / "worker-input"
    source.touch()
    out = tmp_path / "result.json"
    out.touch()
    assert pipeline._cli_worker_main(
        ["--worker-file", str(source), "--worker-root", str(tmp_path),
         "--worker-limits", "{}", "--worker-out", str(out)],
        apply_os_sandbox=False,
    ) == 0
    assert out.stat().st_size <= pipeline.MAX_WORKER_RESULT_BYTES
    payload = json.loads(out.read_text(encoding="utf-8"))
    assert payload["status"] == IngestionStatus.PARTIAL.value
    assert payload["metadata"]["text_truncated"] is True
    assert any("text_truncated" in warning for warning in payload["warnings"])


def test_isolated_staging_rejects_symlink_input(tmp_path: Path) -> None:
    root = tmp_path / "selected"
    root.mkdir()
    target = root / "real.txt"
    target.write_text("trusted", encoding="utf-8")
    alias = root / "alias.txt"
    alias.symlink_to(target)
    result = ingest_file_isolated(alias, allowed_root=root)
    assert result.status == IngestionStatus.ZP_OR_EXE_BLOCKED.value
    assert result.metadata["blocked_reason"] == "parser_subprocess_staging_error"


@pytest.mark.parametrize(
    ("attack", "expected"),
    [
        ("unsupported-method", "zip_unsupported_compression_method"),
        ("zip64-size", "zip_bomb_zip64_blocked"),
        ("nested-extension", "zip_bomb_nested_archive"),
        ("closed-stream", "archive_stream_closed"),
        ("bad-local-signature", "archive_corrupt_header"),
        ("bad-local-method", "zip_unsupported_compression_method"),
        ("spoofed-local-size", "zip_bomb_spoofed_header"),
        ("actual-size-overrun", "zip_bomb_spoofed_header"),
        ("cumulative-size-limit", "zip_bomb_size_exceeded"),
        ("nested-signature", "zip_bomb_nested_archive"),
        ("crc-mismatch", "archive_entry_crc_mismatch"),
        ("central-compressed-size-zero", "archive_entry_size_mismatch"),
    ],
    ids=["method", "zip64", "nested-extension", "closed", "local-signature", "local-method",
         "local-size", "actual-size", "cumulative-limit", "nested-magic", "crc", "truncated-size"],
)
def test_safe_zip_entry_rejects_malformed_archives(attack: str, expected: str) -> None:
    from src.ingestion.pipeline import _safe_read_zip_entry

    payload = b"PK\x03\x04payload" if attack == "nested-signature" else b"hello zip"
    archive_bytes = io.BytesIO()
    with zipfile.ZipFile(archive_bytes, "w", compression=zipfile.ZIP_STORED) as archive:
        archive.writestr("nested.zip" if attack == "nested-extension" else "safe.txt", payload)
    raw = bytearray(archive_bytes.getvalue())
    if attack == "bad-local-signature":
        raw[0:4] = b"FAIL"
    elif attack == "bad-local-method":
        struct.pack_into("<H", raw, 8, 99)
    elif attack == "spoofed-local-size":
        struct.pack_into("<I", raw, 22, len(payload) + 1)
    elif attack == "central-compressed-size-zero":
        struct.pack_into("<H", raw, 6, 0x08)  # Data Descriptor: use central-directory sizes
    with zipfile.ZipFile(io.BytesIO(raw)) as archive:
        info = archive.infolist()[0]
        if attack == "unsupported-method":
            info.compress_type = 99
        elif attack == "zip64-size":
            info.file_size = 0xFFFFFFFF
        elif attack == "actual-size-overrun":
            info.file_size = 1
        elif attack == "crc-mismatch":
            info.CRC ^= 1
        elif attack == "central-compressed-size-zero":
            info.compress_size = 0
        if attack == "closed-stream":
            archive.close()
        limits = IngestionLimits(max_uncompressed_bytes=1 if attack == "cumulative-size-limit" else 100)
        with pytest.raises((ValueError, zipfile.BadZipFile), match=expected):
            _safe_read_zip_entry(
                archive, info, limits=limits, cumulative_bytes=[0],
                forbid_nested_archive=attack in {"nested-extension", "nested-signature"},
            )


def test_safe_zip_entry_empty_and_unconsumed_tail_paths() -> None:
    from src.ingestion.pipeline import CHUNK_SIZE_BYTES, _safe_read_zip_entry

    archive_bytes = io.BytesIO()
    long_payload = b"Z" * (CHUNK_SIZE_BYTES * 4)
    with zipfile.ZipFile(archive_bytes, "w", compression=zipfile.ZIP_DEFLATED) as archive:
        archive.writestr("empty.txt", b"")
        archive.writestr("large.txt", long_payload)
    with zipfile.ZipFile(io.BytesIO(archive_bytes.getvalue())) as archive:
        infos = {info.filename: info for info in archive.infolist()}
        empty = _safe_read_zip_entry(archive, infos["empty.txt"], limits=IngestionLimits(), cumulative_bytes=[0])
        assert bytes(empty) == b""
        large = _safe_read_zip_entry(archive, infos["large.txt"], limits=IngestionLimits(), cumulative_bytes=[0])
        assert bytes(large) == long_payload


def test_safe_zip_entry_rejects_truncated_stream_and_nonblocking_read() -> None:
    from src.ingestion.pipeline import _safe_read_zip_entry

    archive_bytes = io.BytesIO()
    payload = b"A" * 1000
    with zipfile.ZipFile(archive_bytes, "w", compression=zipfile.ZIP_DEFLATED) as archive:
        archive.writestr("data.txt", payload)
    raw = bytearray(archive_bytes.getvalue())
    struct.pack_into("<H", raw, 6, 0x08)  # treat sizes as supplied by central directory
    with zipfile.ZipFile(io.BytesIO(raw)) as archive:
        info = archive.infolist()[0]
        info.compress_size -= 1
        with pytest.raises(zipfile.BadZipFile, match="archive_entry_deflate_stream_incomplete"):
            _safe_read_zip_entry(archive, info, limits=IngestionLimits(), cumulative_bytes=[0])

    with zipfile.ZipFile(io.BytesIO(archive_bytes.getvalue())) as archive:
        info = archive.infolist()[0]

        class TruncatedReader:
            def __init__(self, data: bytes) -> None:
                self._stream = io.BytesIO(data)
                self._reads = 0

            def seek(self, offset: int) -> int:
                return self._stream.seek(offset)

            def read(self, size: int = -1) -> bytes:
                self._reads += 1
                if self._reads == 1:
                    return self._stream.read(size)
                return b""

        archive.fp = TruncatedReader(archive_bytes.getvalue())  # type: ignore[assignment]
        with pytest.raises(zipfile.BadZipFile, match="archive_entry_size_mismatch"):
            _safe_read_zip_entry(archive, info, limits=IngestionLimits(), cumulative_bytes=[0])


def test_isolated_worker_result_identity_is_verified(tmp_path: Path) -> None:
    source = tmp_path / "source.txt"
    source.write_text("safe", encoding="utf-8")
    script = (
        "import json\np={'id':'sha256:wrong','name':'source.txt','mime':'text/plain','size':4,"
        "'status':'extracted','parser':'fake','text':'safe'}\n"
        "open('{worker_out}','w').write(json.dumps(p))"
    )
    result = ingest_file_isolated(source, allowed_root=tmp_path, _worker_cmd=[sys.executable, "-c", script])
    assert result.status == IngestionStatus.ZP_OR_EXE_BLOCKED.value
    assert result.metadata["blocked_reason"] == "worker_result_identity_mismatch"


def test_isolated_worker_rejects_changed_staged_copy(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    from src.ingestion import pipeline

    source = tmp_path / "source.txt"
    source.write_text("original", encoding="utf-8")

    def fail_copy(_source: Path, _destination: Path) -> tuple[str, int, bytes]:
        raise ValueError("source_changed_during_copy")

    monkeypatch.setattr(pipeline, "_copy_to_private_readonly", fail_copy)
    result = ingest_file_isolated(source, allowed_root=tmp_path)
    assert result.status == IngestionStatus.ZP_OR_EXE_BLOCKED.value
    assert result.metadata["blocked_reason"] == "parser_subprocess_staging_error"


def test_isolated_worker_timeout_uses_kill_fallback(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    from src.ingestion import pipeline

    source = tmp_path / "hang.txt"
    source.write_text("safe", encoding="utf-8")
    monkeypatch.setattr(pipeline.os, "killpg", lambda *_args: (_ for _ in ()).throw(OSError("unsupported")))
    script = "while True: pass"
    result = ingest_file_isolated(
        source, allowed_root=tmp_path, timeout_seconds=0.2,
        _worker_cmd=[sys.executable, "-c", script],
    )
    assert result.status == IngestionStatus.ZP_OR_EXE_BLOCKED.value
    assert result.metadata["blocked_reason"] == "parser_timeout"


def test_isolated_file_symlink_resolving_outside_root_is_rejected(tmp_path: Path) -> None:
    root = tmp_path / "selected"
    root.mkdir()
    outside = tmp_path / "outside.txt"
    outside.write_text("outside", encoding="utf-8")
    alias = root / "escape.txt"
    alias.symlink_to(outside)
    with pytest.raises(ValueError, match="file_outside_allowed_root_after_realpath"):
        ingest_file_isolated(alias, allowed_root=root)


def test_worker_timeout_wait_after_kill_is_bounded(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    import subprocess

    from src.ingestion import pipeline

    source = tmp_path / "hang.txt"
    source.write_text("safe", encoding="utf-8")

    class NeverWaits:
        pid = 9876
        returncode: int | None = None

        def wait(self, timeout: float | None = None) -> int:
            raise subprocess.TimeoutExpired("fake-worker", timeout)

        def poll(self) -> None:
            return None

        def kill(self) -> None:
            self.returncode = -9

    monkeypatch.setattr(pipeline.subprocess, "Popen", lambda *_args, **_kwargs: NeverWaits())
    monkeypatch.setattr(pipeline.os, "killpg", lambda *_args: (_ for _ in ()).throw(OSError("fallback")))
    result = ingest_file_isolated(source, allowed_root=tmp_path)
    assert result.metadata["blocked_reason"] == "parser_timeout"


def test_worker_wait_exception_triggers_cleanup_and_fails_closed(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    from src.ingestion import pipeline

    source = tmp_path / "error.txt"
    source.write_text("safe", encoding="utf-8")

    class BadWait:
        pid = 9877
        returncode: int | None = None
        calls = 0

        def wait(self, timeout: float | None = None) -> int:
            self.calls += 1
            if self.calls == 1:
                raise OSError("wait failed")
            return self.returncode or 0

        def poll(self) -> int | None:
            return self.returncode

        def kill(self) -> None:
            self.returncode = -9

    monkeypatch.setattr(pipeline.subprocess, "Popen", lambda *_args, **_kwargs: BadWait())
    monkeypatch.setattr(pipeline.os, "killpg", lambda *_args: (_ for _ in ()).throw(OSError("fallback")))
    result = ingest_file_isolated(source, allowed_root=tmp_path)
    assert result.status == IngestionStatus.ZP_OR_EXE_BLOCKED.value
    assert result.metadata["blocked_reason"] == "parser_crash"


def test_parent_stages_readonly_copy_and_worker_can_read_it(tmp_path: Path) -> None:
    source = tmp_path / "design.txt"
    source.write_text("Cairo print art", encoding="utf-8")
    script = (
        "import hashlib,json,os,stat\np='{worker_file}'\ndata=open(p,'rb').read()\n"
        "assert stat.S_IMODE(os.stat(p).st_mode)==0o400\n"
        "assert stat.S_IMODE(os.stat(os.path.dirname(p)).st_mode)==0o700\n"
        "result={'id':'sha256:'+hashlib.sha256(data).hexdigest(),'name':'design.txt',"
        "'mime':'text/plain','size':len(data),'status':'extracted','parser':'test','text':data.decode()}\n"
        "open('{worker_out}','w').write(json.dumps(result))"
    )
    result = ingest_file_isolated(
        source, allowed_root=tmp_path, _worker_cmd=[sys.executable, "-c", script]
    )
    assert result.status == IngestionStatus.EXTRACTED.value
    assert result.text == "Cairo print art"


@pytest.mark.parametrize(
    ("payload", "message"),
    [
        ({"nest": [[[[[[[[[[[[[[[[[[[[[[[[[[[[[[[[[[[[0]]]]]]]]]]]]]]]]]]]]]]]]]]]]]]]]]]]]}, "complexity"),
        ({"keys": {str(i): i for i in range(10_001)}}, "keys"),
        ({"text": "x" * 1_000_001}, "string too long"),
        ({"warnings": ["x" * 4097]}, "warning limit"),
        ({"number": float("inf")}, "non-finite"),
    ],
    ids=["deep-json", "too-many-keys", "long-string", "oversized-warning", "non-finite-number"],
)
def test_worker_json_complexity_is_bounded(payload: dict[str, object], message: str) -> None:
    from src.ingestion.pipeline import _validate_worker_json

    with pytest.raises(ValueError, match=message):
        _validate_worker_json(payload)


def test_safe_xml_releases_zip_memoryview() -> None:
    from src.ingestion.pipeline import _safe_read_zip_entry

    archive_bytes = io.BytesIO()
    with zipfile.ZipFile(archive_bytes, "w", compression=zipfile.ZIP_DEFLATED) as archive:
        archive.writestr("document.xml", "<root>safe</root>")
    with zipfile.ZipFile(io.BytesIO(archive_bytes.getvalue())) as archive:
        view = _safe_read_zip_entry(
            archive, archive.infolist()[0], limits=IngestionLimits(), cumulative_bytes=[0]
        )
        safe_xml_fromstring(view)
        with pytest.raises(ValueError, match="released memoryview"):
            bytes(view)


def test_allowed_root_rejects_symlink_root(tmp_path: Path) -> None:
    from src.ingestion.pipeline import _resolve_allowed_root

    actual = tmp_path / "actual"
    actual.mkdir()
    link = tmp_path / "selected-link"
    link.symlink_to(actual)
    with pytest.raises(ValueError, match="allowed_root_symlink_rejected"):
        _resolve_allowed_root(link)


def test_worker_efbig_maps_to_specific_exit_reason(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    import errno

    from src.ingestion import pipeline

    source = tmp_path / "input.txt"
    source.touch()
    output = tmp_path / "result.json"
    output.touch()
    result = pipeline.IngestionResult(
        id="sha256:test", name="input.txt", mime="text/plain", size=1,
        status=IngestionStatus.EXTRACTED.value, parser="test", language=None, text="safe",
    )
    monkeypatch.setattr(pipeline, "ingest_file", lambda *_args, **_kwargs: result)
    monkeypatch.setattr(
        pipeline.os,
        "write",
        lambda *_args: (_ for _ in ()).throw(OSError(errno.EFBIG, "file too large")),
    )
    code = pipeline._cli_worker_main(
        ["--worker-file", str(source), "--worker-root", str(tmp_path),
         "--worker-limits", "{}", "--worker-out", str(output)],
        apply_os_sandbox=False,
    )
    assert code == pipeline.WORKER_OUTPUT_EFBIG_EXIT


def test_timeout_removes_private_worker_directory(tmp_path: Path) -> None:
    source = tmp_path / "hang.txt"
    source.write_text("safe", encoding="utf-8")
    marker = tmp_path / "staging-path.txt"
    script = (
        "from pathlib import Path\nPath(r'" + str(marker) + "').write_text('{worker_root}')\n"
        "while True: pass"
    )
    result = ingest_file_isolated(
        source, allowed_root=tmp_path, timeout_seconds=0.2,
        _worker_cmd=[sys.executable, "-c", script],
    )
    assert result.metadata["blocked_reason"] == "parser_timeout"
    staging_path = Path(marker.read_text(encoding="utf-8"))
    assert not staging_path.exists()


def test_deep_worker_json_recursion_is_fail_closed(tmp_path: Path) -> None:
    source = tmp_path / "deep.txt"
    source.write_text("safe", encoding="utf-8")
    script = "open('{worker_out}','w').write('[' * 2000 + '0' + ']' * 2000)"
    result = ingest_file_isolated(
        source, allowed_root=tmp_path, _worker_cmd=[sys.executable, "-c", script]
    )
    assert result.status == IngestionStatus.ZP_OR_EXE_BLOCKED.value
    assert result.metadata["blocked_reason"] == "worker_result_invalid"


def test_safe_zip_safety_declared_total_limit() -> None:
    from src.ingestion.pipeline import _check_zip_safety

    archive_bytes = io.BytesIO()
    with zipfile.ZipFile(archive_bytes, "w") as archive:
        archive.writestr("payload.txt", "long enough")
    with (
        zipfile.ZipFile(io.BytesIO(archive_bytes.getvalue())) as archive,
        pytest.raises(ValueError, match="zip_bomb_size_exceeded"),
    ):
        _check_zip_safety(archive, IngestionLimits(max_uncompressed_bytes=1))


def test_guard_path_rejects_non_string_pathlike() -> None:
    from src.core.path_guard import UnsafePath, guard_path

    with pytest.raises(UnsafePath, match="نوع مسار"):
        guard_path(b"not-a-text-path")


def test_worker_cli_handles_memory_error_and_sandbox_setup(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    from src.ingestion import pipeline

    source = tmp_path / "input.txt"
    source.touch()
    args = ["--worker-file", str(source), "--worker-root", str(tmp_path), "--worker-limits", "{}"]
    monkeypatch.setattr(pipeline, "_apply_worker_rlimits", lambda *_args: None)
    monkeypatch.setattr(pipeline, "_disable_worker_network", lambda: None)
    monkeypatch.setattr(pipeline, "ingest_file", lambda *_args, **_kwargs: (_ for _ in ()).throw(MemoryError()))
    assert pipeline._cli_worker_main(args, apply_os_sandbox=True) == pipeline.WORKER_OOM_EXIT


def test_worker_cli_falls_back_for_oversized_structured_output(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    import json

    from src.ingestion import pipeline

    result = pipeline.IngestionResult(
        id="sha256:test", name="large.txt", mime="text/plain", size=1,
        status=IngestionStatus.EXTRACTED.value, parser="test", language=None, text="",
        tables=[["x" * (pipeline.MAX_WORKER_RESULT_BYTES + 100)]],
    )
    monkeypatch.setattr(pipeline, "ingest_file", lambda *_args, **_kwargs: result)
    source = tmp_path / "input.txt"
    source.touch()
    output = tmp_path / "result.json"
    output.touch()
    assert pipeline._cli_worker_main(
        ["--worker-file", str(source), "--worker-root", str(tmp_path),
         "--worker-limits", "{}", "--worker-out", str(output)],
        apply_os_sandbox=False,
    ) == 0
    payload = json.loads(output.read_text(encoding="utf-8"))
    assert payload["tables"] == []
    assert payload["status"] == IngestionStatus.PARTIAL.value


def test_worker_cli_rejects_output_outside_root(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    from src.ingestion import pipeline

    source = tmp_path / "input.txt"
    source.touch()
    output = tmp_path.parent / f"{tmp_path.name}-outside-result.json"
    output.touch()
    monkeypatch.setattr(
        pipeline,
        "ingest_file",
        lambda *_args, **_kwargs: pipeline.IngestionResult(
            id="sha256:test", name="input.txt", mime="text/plain", size=1,
            status="extracted", parser="test", language=None, text="x",
        ),
    )
    with pytest.raises(ValueError, match="worker_output_path_outside_root"):
        pipeline._cli_worker_main(
            ["--worker-file", str(source), "--worker-root", str(tmp_path),
             "--worker-limits", "{}", "--worker-out", str(output)],
            apply_os_sandbox=False,
        )


def test_worker_result_reader_rejects_stat_oversize_and_read_race(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    from src.ingestion import pipeline

    output = tmp_path / "result.json"
    output.write_bytes(b"12345")
    monkeypatch.setattr(pipeline, "MAX_WORKER_RESULT_BYTES", 4)
    with pytest.raises(ValueError, match="not_regular_or_too_large"):
        pipeline._read_worker_result(output)

    output.write_bytes(b"")
    monkeypatch.setattr(pipeline.os, "read", lambda *_args: b"abc")
    with pytest.raises(ValueError, match="worker_output_too_large"):
        pipeline._read_worker_result(output)


def test_svg_html_sanitizer_rejection_branches() -> None:
    from src.ingestion.pipeline import _AllowlistSvgHtmlSanitizer

    sanitizer = _AllowlistSvgHtmlSanitizer()
    sanitizer.handle_decl("DOCTYPE entity SYSTEM public")
    sanitizer.handle_decl("plain declaration")
    sanitizer._warn_once("duplicate-warning")
    sanitizer._warn_once("duplicate-warning")
    sanitizer.unknown_decl("[CDATA[bad]]")
    sanitizer.handle_starttag("script", [])
    sanitizer.handle_starttag("script", [])
    sanitizer.handle_starttag("div", [])
    sanitizer.handle_data("hidden")
    sanitizer.handle_endtag("div")
    sanitizer.handle_endtag("script")
    sanitizer.handle_endtag("script")
    sanitizer.handle_data("visible")
    sanitizer.handle_starttag("svg", [])
    sanitizer.handle_starttag("g", [])
    sanitizer.handle_endtag("svg")
    sanitizer.handle_entityref("amp")
    sanitizer.handle_entityref("external")
    sanitizer.handle_charref("65")
    sanitizer.handle_starttag(
        "a",
        [("rel", "opener"), ("onclick", "alert(1)"), ("bogus", "x"),
         ("href", "javascript:alert(1)"), ("style", "background:url(x)"), ("class", "ok")],
    )
    sanitizer.handle_endtag("missing")
    sanitizer.handle_startendtag("br", [])
    sanitizer._skip_depth = 1
    sanitizer.handle_data("hidden")
    sanitizer.handle_entityref("amp")
    sanitizer.handle_charref("65")
    assert "visible" in "".join(sanitizer.output)
    assert "hidden" not in "".join(sanitizer.output)
    assert any("xxe_entity_blocked" in warning for warning in sanitizer.warnings)
    assert any("xss_vector_sanitized" in warning for warning in sanitizer.warnings)


def test_worker_cli_bounds_unrepresentable_minimum_output(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    from src.ingestion import pipeline

    source = tmp_path / "input.txt"
    source.touch()
    output = tmp_path / "result.json"
    output.touch()
    result = pipeline.IngestionResult(
        id="sha256:test", name="input.txt", mime="text/plain", size=1,
        status="extracted", parser="test", language=None, text="x",
    )
    monkeypatch.setattr(pipeline, "ingest_file", lambda *_args, **_kwargs: result)
    monkeypatch.setattr(pipeline, "MAX_WORKER_RESULT_BYTES", 1)
    with pytest.raises(ValueError, match="worker_result_cannot_be_bounded"):
        pipeline._cli_worker_main(
            ["--worker-file", str(source), "--worker-root", str(tmp_path),
             "--worker-limits", "{}", "--worker-out", str(output)],
            apply_os_sandbox=False,
        )


def test_worker_cli_memoryerror_during_json_serialization(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    from src.ingestion import pipeline

    source = tmp_path / "input.txt"
    source.touch()
    result = pipeline.IngestionResult(
        id="sha256:test", name="input.txt", mime="text/plain", size=1,
        status="extracted", parser="test", language=None, text="x",
    )
    monkeypatch.setattr(pipeline, "ingest_file", lambda *_args, **_kwargs: result)
    monkeypatch.setattr(pipeline.json, "dumps", lambda *_args, **_kwargs: (_ for _ in ()).throw(MemoryError()))
    code = pipeline._cli_worker_main(
        ["--worker-file", str(source), "--worker-root", str(tmp_path), "--worker-limits", "{}"],
        apply_os_sandbox=False,
    )
    assert code == pipeline.WORKER_OOM_EXIT


def test_worker_cli_rejects_nonregular_result_fd(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    from types import SimpleNamespace

    from src.ingestion import pipeline

    source = tmp_path / "input.txt"
    source.touch()
    output = tmp_path / "result.json"
    output.touch()
    result = pipeline.IngestionResult(
        id="sha256:test", name="input.txt", mime="text/plain", size=1,
        status="extracted", parser="test", language=None, text="x",
    )
    monkeypatch.setattr(pipeline, "ingest_file", lambda *_args, **_kwargs: result)
    monkeypatch.setattr(pipeline, "_open_regular_no_follow", lambda *_args, **_kwargs: 123)
    monkeypatch.setattr(pipeline.os, "fstat", lambda _fd: SimpleNamespace(st_mode=pipeline.stat.S_IFDIR))
    monkeypatch.setattr(pipeline.os, "close", lambda _fd: None)
    with pytest.raises(ValueError, match="worker_output_not_regular"):
        pipeline._cli_worker_main(
            ["--worker-file", str(source), "--worker-root", str(tmp_path),
             "--worker-limits", "{}", "--worker-out", str(output)],
            apply_os_sandbox=False,
        )


def test_worker_cli_rethrows_non_efbig_write_error(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    import errno

    from src.ingestion import pipeline

    source = tmp_path / "input.txt"
    source.touch()
    output = tmp_path / "result.json"
    output.touch()
    result = pipeline.IngestionResult(
        id="sha256:test", name="input.txt", mime="text/plain", size=1,
        status="extracted", parser="test", language=None, text="x",
    )
    monkeypatch.setattr(pipeline, "ingest_file", lambda *_args, **_kwargs: result)
    monkeypatch.setattr(
        pipeline.os,
        "write",
        lambda *_args: (_ for _ in ()).throw(OSError(errno.EIO, "io failure")),
    )
    with pytest.raises(OSError, match="io failure"):
        pipeline._cli_worker_main(
            ["--worker-file", str(source), "--worker-root", str(tmp_path),
             "--worker-limits", "{}", "--worker-out", str(output)],
            apply_os_sandbox=False,
        )


def test_default_pipeline_stages_and_hashes_source_in_single_parent_pass(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    from src.ingestion import pipeline as pipeline_module

    source = tmp_path / "single-pass.txt"
    source.write_text("single parent pass", encoding="utf-8")
    original_copy = pipeline_module._copy_to_private_readonly
    copy_calls = 0

    def count_copy(*args: object, **kwargs: object) -> tuple[str, int, bytes]:
        nonlocal copy_calls
        copy_calls += 1
        return original_copy(*args, **kwargs)  # type: ignore[arg-type]

    monkeypatch.setattr(pipeline_module, "_copy_to_private_readonly", count_copy)
    monkeypatch.setattr(
        pipeline_module,
        "_stream_sha256_and_head",
        lambda *_args: (_ for _ in ()).throw(AssertionError("source was redundantly hashed")),
    )
    result, from_cache = IngestionPipeline().process(source, allowed_root=tmp_path)
    assert not from_cache
    assert result.id.startswith("sha256:")
    assert copy_calls == 1
