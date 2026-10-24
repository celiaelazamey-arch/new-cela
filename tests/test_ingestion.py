"""
مجموعة اختبارات خط أنابيب استيعاب الملفات الطبقي (v2.0 — Corpus متعدد الصيغ + اختبارات هجومية P0/P1/P2).
تغطي:
  - العربية UTF-8 و Windows-1256 (cp1256) مع درجة الثقة (encoding_confidence).
  - تعقيم SVG/HTML بالقائمة البيضاء (Allowlist) ضد <foreignObject>، كيانات &#106;avascript:، data:، و CSS url().
  - حماية XML الصارمة (safe_xml_fromstring / defusedxml) ضد Billion Laughs وتجاوز العمق والعناصر.
  - كشف ترويسة ZIP المزوّرة أثناء القراءة التدفقية، الأرشيفات المتداخلة، واجتياز المسارات.
  - حظر التنفيذيات (PE, ELF, Mach-O, Java Class, WASM, Scripts) والماكرو (OOXML vbaProject.bin و OLE2) وفحص EICAR/AV.
  - عزل المعالجة في عملية فرعية (ingest_file_isolated) مع اختبار انتهاء المهلة (timeout).
  - قراءة ترويسة PSD وقسم Image Resources (0x03EE / 0x0415 / 0x0435) وحساب DPI الفعلي للطباعة.
  - استخراج XLSX الكامل (sharedStrings + inlineStr + المعادلات + الأوراق المتعددة + الاقتطاع partial).
  - اختبارات الحدود الدقيقة (50B vs 50B+1) وعزل مفتاح الـ Cache بـ (tenant + PIPELINE_VERSION + limits + sha256).
"""
from __future__ import annotations

import struct
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
)
from src.ingestion.pipeline import EICAR_SIGNATURE, _cli_worker_main, detect_magic_mime


def test_arabic_utf8_and_windows_1256_encoding_detection(tmp_path: Path) -> None:
    """اختبار فك الترميز العربي UTF-8 والترميز القديم Windows-1256 (cp1256) مع درجة الثقة."""
    utf8_file = tmp_path / "order_utf8.txt"
    utf8_file.write_text("أمر شغل مطبعة سلك سكرين — 500 تيشيرت قطن أسود", encoding="utf-8")
    res_utf8 = ingest_file(utf8_file, allowed_root=tmp_path)
    assert res_utf8.status == IngestionStatus.EXTRACTED.value
    assert res_utf8.language == "ar"
    assert "سلك سكرين" in res_utf8.text
    assert res_utf8.metadata["encoding"] == "utf-8"
    assert res_utf8.metadata["encoding_confidence"] == 1.0

    # ملف نصي عربي بترميز Windows-1256 القديم (يفشل في UTF-8 العادي)
    cp1256_file = tmp_path / "legacy_arabic.txt"
    cp1256_file.write_bytes(
        "فاتورة مبيعات مطبعة الملابس الجاهزة — السعر بالجنيه المصري".encode("cp1256")
    )
    res_cp = ingest_file(cp1256_file, allowed_root=tmp_path)
    assert res_cp.status == IngestionStatus.EXTRACTED.value
    assert res_cp.language == "ar"
    assert "الجنيه المصري" in res_cp.text
    assert res_cp.metadata["encoding"] == "cp1256"
    assert float(res_cp.metadata["encoding_confidence"]) >= 0.5  # type: ignore[arg-type]
    assert any("legacy_encoding_detected" in w for w in res_cp.warnings)

    # UTF-8-SIG و UTF-16 و JSON و EML
    utf16_file = tmp_path / "note_utf16.txt"
    utf16_file.write_bytes("Hello Print Shop".encode("utf-16"))
    res_16 = ingest_file(utf16_file, allowed_root=tmp_path)
    assert res_16.metadata["encoding"] == "utf-16"
    assert res_16.language == "en"

    json_file = tmp_path / "config.json"
    json_file.write_text('{"shop": "Cela", "colors": 4}', encoding="utf-8-sig")
    res_json = ingest_file(json_file, allowed_root=tmp_path)
    assert res_json.metadata["json_type"] == "dict"

    eml_file = tmp_path / "client.eml"
    eml_file.write_text(
        "From: client@example.invalid\nSubject: Print Order\n\nPlease print 200 hoodies.",
        encoding="utf-8",
    )
    res_eml = ingest_file(eml_file, allowed_root=tmp_path)
    assert res_eml.metadata["author"] == "client@example.invalid"
    assert res_eml.metadata["subject"] == "Print Order"


def test_svg_allowlist_sanitizer_against_advanced_bypasses(tmp_path: Path) -> None:
    """
    اختبار معقّم القائمة البيضاء (Allowlist Sanitizer — P1) ضد:
      - <foreignObject>، <sCrIpT> بحروف مختلطة، و <style>.
      - كيانات HTML المشفّرة في الروابط (xlink:href="&#106;avascript:alert(1)").
      - روابط data: وتعبيرات CSS url(javascript:...).
      - الكيانات الخارجية XXE (<!ENTITY ... SYSTEM>).
    """
    csv_file = tmp_path / "films.csv"
    csv_file.write_text("كود الفيلم,اللون,الحرير\nFLM-01,أبيض أرضية,55T\n", encoding="utf-8")
    res_csv = ingest_file(csv_file, allowed_root=tmp_path)
    assert res_csv.status == IngestionStatus.EXTRACTED.value
    assert len(res_csv.tables) == 1
    assert res_csv.tables[0][1][0] == "FLM-01"

    svg_file = tmp_path / "bypass_payload.svg"
    svg_file.write_text(
        '<!DOCTYPE svg [<!ENTITY xxe SYSTEM "file:///etc/passwd">]>\n'
        '<svg xmlns="http://www.w3.org/2000/svg" xmlns:xlink="http://www.w3.org/1999/xlink" '
        'oNlOaD="alert(1)" data-evil="1">\n'
        "  <sCrIpT>evil_script()</sCrIpT>\n"
        '  <foreignObject width="100" height="50">'
        '<body xmlns="http://www.w3.org/1999/xhtml"><iframe src="javascript:alert(2)"></iframe></body>'
        "</foreignObject>\n"
        '  <a xlink:href="&#106;av&#x09;ascript:alert(3)" href="data:text/html,&lt;script&gt;alert(4)&lt;/script&gt;">رابط</a>\n'
        '  <rect width="10" height="10" style="background:url(javascript:alert(5))" />\n'
        '  <a href="https://example.com/safe" id="ok"><text>تصميم تيشيرت آمن &amp; جاهز &xxe;</text></a>\n'
        "</svg>",
        encoding="utf-8",
    )
    res_svg = ingest_file(svg_file, allowed_root=tmp_path)
    assert res_svg.status == IngestionStatus.EXTRACTED.value
    assert "<script" not in res_svg.text.lower()
    assert "evil_script" not in res_svg.text
    assert "<foreignobject" not in res_svg.text.lower()
    assert "<iframe" not in res_svg.text.lower()
    assert "javascript:" not in res_svg.text.lower()
    assert "data:text/html" not in res_svg.text.lower()
    assert "onload=" not in res_svg.text.lower()
    assert "<!entity" not in res_svg.text.lower()
    assert "تصميم تيشيرت آمن" in res_svg.text
    assert 'href="https://example.com/safe"' in res_svg.text
    assert any("xxe_entity_blocked" in w for w in res_svg.warnings)
    assert any("xss_vector_sanitized" in w for w in res_svg.warnings)
    headers = res_svg.metadata.get("recommended_headers")
    assert isinstance(headers, dict)
    assert headers.get("Content-Disposition") == "attachment"


def test_defused_xml_guard_blocks_billion_laughs_and_deep_trees(tmp_path: Path) -> None:
    """اختبار حارس XML (P0) ضد Billion Laughs وتجاوز عمق الشجرة وعدد العناصر."""
    billion_laughs = (
        b'<?xml version="1.0"?>\n'
        b'<!DOCTYPE lolz [\n'
        b' <!ENTITY lol "lol">\n'
        b' <!ENTITY lol2 "&lol;&lol;&lol;&lol;&lol;">\n'
        b']>\n<lolz>&lol2;</lolz>'
    )
    with pytest.raises(ValueError, match="xml_bomb_entity_or_dtd_blocked"):
        safe_xml_fromstring(billion_laughs)

    # ملف XML على القرص يحتوي على <!ENTITY -> يُحظر عند الاستيعاب
    xml_bomb_file = tmp_path / "bomb.xml"
    xml_bomb_file.write_bytes(billion_laughs)
    res_xml = ingest_file(xml_bomb_file, allowed_root=tmp_path)
    assert res_xml.status == IngestionStatus.ZP_OR_EXE_BLOCKED.value
    assert any("xml_bomb_entity_or_dtd_blocked" in w for w in res_xml.warnings)

    # تجاوز أقصى عمق XML
    deep_xml = ("<a>" * 12 + "text" + "</a>" * 12).encode("utf-8")
    with pytest.raises(ValueError, match="xml_bomb_depth_exceeded"):
        safe_xml_fromstring(deep_xml, limits=IngestionLimits(max_xml_depth=5))

    # تجاوز أقصى عدد عناصر XML
    many_nodes_xml = ("<root>" + "<item/>" * 20 + "</root>").encode("utf-8")
    with pytest.raises(ValueError, match="xml_bomb_elements_exceeded"):
        safe_xml_fromstring(many_nodes_xml, limits=IngestionLimits(max_xml_elements=10))

    # تجاوز حجم XML الأقصى
    with pytest.raises(ValueError, match="xml_bomb_size_exceeded"):
        safe_xml_fromstring(b"<root>1234567890</root>", limits=IngestionLimits(max_xml_bytes=5))


def test_magic_bytes_executables_macho_scripts_macros_and_av_scan(tmp_path: Path) -> None:
    """
    اختبار كشف البصمة الثنائية وحظر:
      - ملفات PE (MZ) و Mach-O و Java Class و WASM المتخفّية.
      - اسكربتات PowerShell/Batch المتخفّية بامتداد .txt (بدون الاعتماد على الامتداد وحده!).
      - ماكرو VBA داخل OOXML (vbaProject.bin) وداخل OLE2 القديم (.doc).
      - بصمة EICAR وماسح الفيروسات المخصّص (av_scanner).
    """
    # 1. ملف تنفيذي (MZ) متخفٍّ بامتداد .pdf
    fake_pdf = tmp_path / "invoice.pdf"
    fake_pdf.write_bytes(b"MZ\x90\x00\x03\x00\x00\x00FakeExecutablePayload")
    res_pe = ingest_file(fake_pdf, allowed_root=tmp_path)
    assert res_pe.status == IngestionStatus.ZP_OR_EXE_BLOCKED.value
    assert res_pe.mime == "application/x-msdownload"
    assert any("suspicious_extension_mismatch" in w for w in res_pe.warnings)
    assert any("executable_blocked" in w for w in res_pe.warnings)

    # 2. ملف Mach-O متخفٍّ بامتداد .png
    fake_png = tmp_path / "logo.png"
    fake_png.write_bytes(b"\xfe\xed\xfa\xcf\x00\x00\x00\x0cMachOBinaryPayload")
    res_macho = ingest_file(fake_png, allowed_root=tmp_path)
    assert res_macho.status == IngestionStatus.ZP_OR_EXE_BLOCKED.value
    assert res_macho.mime == "application/x-mach-binary"
    assert any("suspicious_extension_mismatch" in w for w in res_macho.warnings)

    # 3. اسكربت Batch/PowerShell متخفٍّ بامتداد .txt (يُكشف من المحتوى لا الامتداد)
    disguised_script = tmp_path / "readme.txt"
    disguised_script.write_bytes(b"@echo off\r\npowershell Invoke-Expression evil.ps1\r\n")
    res_script = ingest_file(disguised_script, allowed_root=tmp_path)
    assert res_script.status == IngestionStatus.ZP_OR_EXE_BLOCKED.value
    assert res_script.mime == "application/x-executable-script"
    assert any("suspicious_extension_mismatch" in w for w in res_script.warnings)

    # 4. ملف DOCX يحتوي على ماكرو خبيث (word/vbaProject.bin)
    macro_docx = tmp_path / "macro_contract.docx"
    with zipfile.ZipFile(macro_docx, "w") as zf:
        zf.writestr("word/document.xml", "<w:document><w:body><w:t>Hello</w:t></w:body></w:document>")
        zf.writestr("word/vbaProject.bin", b"FakeVBAMacroPayload")
    res_macro = ingest_file(macro_docx, allowed_root=tmp_path)
    assert res_macro.status == IngestionStatus.ZP_OR_EXE_BLOCKED.value
    assert any("macro_detected_blocked" in w for w in res_macro.warnings)

    # 5. ملف OLE2 قديم (.doc) مع ماكرو مقابل ملف OLE2 نظيف
    ole_macro = tmp_path / "legacy_evil.doc"
    ole_macro.write_bytes(b"\xd0\xcf\x11\xe0\xa1\xb1\x1a\xe1" + b"\x00" * 32 + b"_VBA_PROJECT")
    res_ole_evil = ingest_file(ole_macro, allowed_root=tmp_path)
    assert res_ole_evil.status == IngestionStatus.ZP_OR_EXE_BLOCKED.value
    assert any("macro_detected_blocked" in w for w in res_ole_evil.warnings)

    ole_clean = tmp_path / "legacy_clean.doc"
    ole_clean.write_bytes(b"\xd0\xcf\x11\xe0\xa1\xb1\x1a\xe1" + b"\x00" * 64)
    res_ole_clean = ingest_file(ole_clean, allowed_root=tmp_path)
    assert res_ole_clean.status == IngestionStatus.METADATA_ONLY.value
    assert any("legacy_ole2_format_metadata_only" in w for w in res_ole_clean.warnings)

    # 6. فحص EICAR القياسي وماسح مضاد الفيروسات المخصّص
    eicar_file = tmp_path / "eicar.txt"
    eicar_file.write_bytes(EICAR_SIGNATURE)
    res_eicar = ingest_file(eicar_file, allowed_root=tmp_path)
    assert res_eicar.status == IngestionStatus.ZP_OR_EXE_BLOCKED.value
    assert res_eicar.parser == "av_scanner"
    assert any("malware_detected_blocked" in w for w in res_eicar.warnings)

    clean_txt = tmp_path / "suspect.txt"
    clean_txt.write_text("file content", encoding="utf-8")
    res_custom_av = ingest_file(
        clean_txt,
        allowed_root=tmp_path,
        av_scanner=lambda _p: (False, "Custom.Trojan.PrintShop"),
    )
    assert res_custom_av.status == IngestionStatus.ZP_OR_EXE_BLOCKED.value
    assert res_custom_av.metadata["malware_signature"] == "Custom.Trojan.PrintShop"


def test_scanned_pdf_and_full_xlsx_and_pptx_extraction(tmp_path: Path) -> None:
    """
    اختبار:
      - كشف PDF الممسوح ضوئياً (فارغ النص -> partial + scanned_pdf_needs_ocr).
      - استخراج DOCX و PPTX.
      - استخراج XLSX الكامل (sharedStrings + inlineStr + الأرقام + المعادلات + الاقتطاع partial).
    """
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

    # 2. ملف DOCX حقيقي
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

    # 3. ملف XLSX يحتوي على sharedStrings و inlineStr ومعادلات وأرقام عبر ورقة عمل
    xlsx_file = tmp_path / "pricing.xlsx"
    with zipfile.ZipFile(xlsx_file, "w") as zf:
        zf.writestr(
            "xl/sharedStrings.xml",
            '<?xml version="1.0" encoding="UTF-8"?>'
            "<sst><si><t>تيشيرت قطن</t></si></sst>",
        )
        zf.writestr(
            "xl/worksheets/sheet1.xml",
            '<?xml version="1.0" encoding="UTF-8"?>'
            "<worksheet><sheetData>"
            '<row r="1">'
            '<c r="A1" t="s"><v>0</v></c>'
            '<c r="B1" t="inlineStr"><is><t>طباعة بلاستيزول</t></is></c>'
            '<c r="C1"><f>10*25</f><v>250</v></c>'
            "</row>"
            '<row r="2"><c r="A2" t="inlineStr"><is><t>صف إضافي</t></is></c></row>'
            "</sheetData></worksheet>",
        )
    res_xlsx = ingest_file(xlsx_file, allowed_root=tmp_path)
    assert res_xlsx.status == IngestionStatus.EXTRACTED.value
    assert "تيشيرت قطن" in res_xlsx.text
    assert "طباعة بلاستيزول" in res_xlsx.text
    assert "250" in res_xlsx.text
    assert res_xlsx.metadata["has_formulas"] is True
    assert res_xlsx.tables[0][0] == ["تيشيرت قطن", "طباعة بلاستيزول", "250"]

    # اختبار اقتطاع XLSX عند تجاوز max_xlsx_rows -> حالة partial
    res_xlsx_trunc = ingest_file(
        xlsx_file,
        allowed_root=tmp_path,
        limits=IngestionLimits(max_xlsx_rows=1),
    )
    assert res_xlsx_trunc.status == IngestionStatus.PARTIAL.value
    assert any("xlsx_truncated" in w for w in res_xlsx_trunc.warnings)

    # 4. ملف PPTX
    pptx_file = tmp_path / "deck.pptx"
    with zipfile.ZipFile(pptx_file, "w") as zf:
        zf.writestr(
            "ppt/slides/slide1.xml",
            '<?xml version="1.0" encoding="UTF-8"?><p:sld xmlns:p="http://example.com"><t>عرض المطبعة</t></p:sld>',
        )
    res_pptx = ingest_file(pptx_file, allowed_root=tmp_path)
    assert res_pptx.status == IngestionStatus.EXTRACTED.value
    assert "عرض المطبعة" in res_pptx.text


def test_psd_image_resources_spot_channels_and_effective_print_dpi(tmp_path: Path) -> None:
    """
    اختبار (P2):
      - قراءة ترويسة PSD وقسم Image Resources (0x03EE + 0x0415 + 0x0435) للتمييز بين
        القنوات الأساسية (CMYK=4) وقنوات Spot الإضافية وأسمائها الفعلية.
      - التعامل الآمن مع ملف PSD مقطوع/تالف.
      - حساب الدقة الفعلية للطباعة (effective_dpi) عند غياب وسم DPI في JPEG.
      - حظر قنابل فك ضغط الصور (max_image_pixels).
    """
    psd_file = tmp_path / "design_cmyk_spot.psd"
    # ترويسة PSD: 8BPS + version(1) + 6 reserved + channels(6: CMYK 4 + 2 Spot) + height(3508) + width(2480) + depth(8) + mode(4=CMYK)
    psd_header = b"8BPS" + struct.pack(">H6sHIIHH", 1, b"\x00" * 6, 6, 3508, 2480, 8, 4)
    color_mode_section = struct.pack(">I", 0)

    # بناء مورد 0x0415 (Unicode Alpha/Spot Names) + مورد 0x0435 (DisplayInfo)
    names_payload = b""
    for ch_name in ("Pantone 186 C", "White Underbase"):
        utf16_bytes = ch_name.encode("utf-16-be")
        names_payload += struct.pack(">I", len(ch_name)) + utf16_bytes
    block_1045 = (
        b"8BIM"
        + struct.pack(">H", 0x0415)
        + b"\x00\x00"
        + struct.pack(">I", len(names_payload))
        + names_payload
        + (b"\x00" if len(names_payload) % 2 == 1 else b"")
    )
    block_1077 = b"8BIM" + struct.pack(">H", 0x0435) + b"\x00\x00" + struct.pack(">I", 4) + b"\x00\x01\x02\x03"
    resources_payload = block_1045 + block_1077
    image_resources_section = struct.pack(">I", len(resources_payload)) + resources_payload

    psd_file.write_bytes(psd_header + color_mode_section + image_resources_section)

    res_psd = ingest_file(psd_file, allowed_root=tmp_path)
    assert res_psd.status == IngestionStatus.EXTRACTED.value
    assert res_psd.mime == "image/vnd.adobe.photoshop"
    assert res_psd.metadata["color_mode"] == "CMYK"
    assert res_psd.metadata["channels"] == 6
    assert res_psd.metadata["base_channels"] == 4
    assert res_psd.metadata["extra_channels"] == 2
    assert res_psd.metadata["channel_names"] == ["Pantone 186 C", "White Underbase"]
    assert res_psd.metadata["has_spot_channel_resources"] is True

    # ملف PSD مقطوع -> لا يسقط النظام بل يعود بـ metadata_only
    truncated_psd = tmp_path / "broken.psd"
    truncated_psd.write_bytes(b"8BPS\x00\x01short")
    res_trunc_psd = ingest_file(truncated_psd, allowed_root=tmp_path)
    assert res_trunc_psd.status == IngestionStatus.METADATA_ONLY.value
    assert any("parser_fallback_to_metadata" in w for w in res_trunc_psd.warnings)

    # صورة JPEG بلا وسم DPI -> تحذير missing_dpi_metadata، ثم حساب effective_dpi بمقاس الطباعة
    no_dpi_jpg = tmp_path / "photo.jpg"
    img = Image.new("RGB", (600, 600), color=(20, 40, 60))
    img.save(no_dpi_jpg, format="JPEG")

    res_no_dpi = ingest_file(no_dpi_jpg, allowed_root=tmp_path)
    assert res_no_dpi.status == IngestionStatus.EXTRACTED.value
    assert any("missing_dpi_metadata" in w for w in res_no_dpi.warnings)

    # عند تمرير مقاس طباعة 25.4x25.4 سم (10x10 بوصة) لصورة 600x600 -> الدقة الفعلية 60 DPI < 300
    res_eff_dpi = ingest_file(
        no_dpi_jpg,
        allowed_root=tmp_path,
        limits=IngestionLimits(target_print_width_cm=25.4, target_print_height_cm=25.4),
    )
    assert res_eff_dpi.metadata["effective_dpi"] == [60, 60]
    assert any("low_print_dpi" in w for w in res_eff_dpi.warnings)

    # حظر قنبلة فك ضغط الصور (Image Decompression Bomb)
    res_img_bomb = ingest_file(
        no_dpi_jpg,
        allowed_root=tmp_path,
        limits=IngestionLimits(max_image_pixels=100),
    )
    assert res_img_bomb.status == IngestionStatus.ZP_OR_EXE_BLOCKED.value
    assert any("image_decompression_bomb_blocked" in w for w in res_img_bomb.warnings)


def test_zip_spoofed_header_nested_archive_and_path_traversal_blocked(tmp_path: Path) -> None:
    """
    اختبار دفاعات الأرشيف (P0):
      1. Path Traversal (`../../etc/passwd`).
      2. أرشيف متداخل (`inner.zip` داخل `outer.zip`).
      3. ترويسة ZIP مزوّرة تدّعي أن حجم الملف 4 بايت بينما البيانات الفعلية أكبر بكثير.
      4. تجاوز الحد الأقصى لعدد الملفات في الأرشيف.
    """
    # 1. Path Traversal
    traversal_zip = tmp_path / "evil.zip"
    with zipfile.ZipFile(traversal_zip, "w") as zf:
        zf.writestr("../../etc/passwd", "root:x:0:0")

    res_trav = ingest_file(traversal_zip, allowed_root=tmp_path)
    assert res_trav.status == IngestionStatus.ZP_OR_EXE_BLOCKED.value
    assert any("archive_path_traversal" in w for w in res_trav.warnings)

    # 2. Nested Archive (zip داخل zip)
    inner_zip_bytes_io = tmp_path / "inner.zip"
    with zipfile.ZipFile(inner_zip_bytes_io, "w") as zf:
        zf.writestr("a.txt", "nested")
    nested_zip = tmp_path / "nested.zip"
    with zipfile.ZipFile(nested_zip, "w") as zf:
        zf.write(inner_zip_bytes_io, arcname="sub/inner.zip")

    res_nested = ingest_file(nested_zip, allowed_root=tmp_path)
    assert res_nested.status == IngestionStatus.ZP_OR_EXE_BLOCKED.value
    assert any("zip_bomb_nested_archive" in w for w in res_nested.warnings)

    # 3. ترويسة ZIP مزوّرة (Spoofed Header): الملف الفعلي 200 بايت لكن الترويسة تدّعي 4 بايت
    spoofed_zip = tmp_path / "spoofed.zip"
    payload = b"A" * 200
    with zipfile.ZipFile(spoofed_zip, "w", compression=zipfile.ZIP_DEFLATED) as zf:
        zf.writestr("payload.txt", payload)

    raw_zip = bytearray(spoofed_zip.read_bytes())
    uncompressed_len_bytes = struct.pack("<I", 200)
    fake_len_bytes = struct.pack("<I", 4)
    # تزوير حقل uncompressed size في Local File Header و Central Directory
    replaced_count = 0
    idx = 0
    while True:
        pos = raw_zip.find(uncompressed_len_bytes, idx)
        if pos == -1:
            break
        raw_zip[pos : pos + 4] = fake_len_bytes
        replaced_count += 1
        idx = pos + 4
    assert replaced_count >= 1
    spoofed_zip.write_bytes(bytes(raw_zip))

    res_spoofed = ingest_file(spoofed_zip, allowed_root=tmp_path)
    assert res_spoofed.status == IngestionStatus.ZP_OR_EXE_BLOCKED.value
    assert any("zip_bomb_spoofed_header" in w for w in res_spoofed.warnings)

    # 4. أرشيف يتجاوز الحد الأقصى لعدد الملفات
    many_zip = tmp_path / "too_many.zip"
    with zipfile.ZipFile(many_zip, "w") as zf:
        for i in range(6):
            zf.writestr(f"file_{i}.txt", "ok")

    strict_limits = IngestionLimits(max_archive_entries=3)
    res_many = ingest_file(many_zip, allowed_root=tmp_path, limits=strict_limits)
    assert res_many.status == IngestionStatus.ZP_OR_EXE_BLOCKED.value
    assert any("zip_bomb_entries_exceeded" in w for w in res_many.warnings)


def test_isolated_subprocess_execution_and_timeout(tmp_path: Path) -> None:
    """اختبار عزل المعالجة في عملية فرعية (P0) واختبار انتهاء المهلة (timeout)."""
    sample = tmp_path / "isolated_note.txt"
    sample.write_text("تشغيل المحلل داخل عملية فرعية معزولة", encoding="utf-8")

    # 1. نجاح الاستخراج في عملية فرعية معزولة
    res_iso = ingest_file(
        sample,
        allowed_root=tmp_path,
        limits=IngestionLimits(isolate_in_subprocess=True, parser_timeout_seconds=10.0),
    )
    assert res_iso.status == IngestionStatus.EXTRACTED.value
    assert "عملية فرعية معزولة" in res_iso.text

    # 2. اختبار انتهاء المهلة الزمنية للعملية الفرعية
    res_timeout = ingest_file_isolated(
        sample,
        allowed_root=tmp_path,
        timeout_seconds=0.0001,
    )
    assert res_timeout.status == IngestionStatus.ZP_OR_EXE_BLOCKED.value
    assert any("parser_subprocess_timeout" in w for w in res_timeout.warnings)


def test_boundary_sizes_unsupported_formats_and_multitenant_cache(tmp_path: Path) -> None:
    """
    اختبار:
      - الحدود الدقيقة لحجم الملف (100 بايت بالضبط ينجح، 101 بايت يُحظر).
      - الصيغ غير المدعومة حالياً (7z, mp3, woff2) تعود بـ metadata_only مع تحذير واضح.
      - عزل مفتاح الـ Cache بـ (tenant_id + PIPELINE_VERSION + limits + sha256).
    """
    # 1. اختبار الحدود الدقيقة: 100 بايت مقابل 101 بايت
    exact_file = tmp_path / "exact_100.txt"
    exact_file.write_bytes(b"A" * 100)
    over_file = tmp_path / "over_101.txt"
    over_file.write_bytes(b"A" * 101)

    boundary_limits = IngestionLimits(max_file_size_bytes=100)
    res_exact = ingest_file(exact_file, allowed_root=tmp_path, limits=boundary_limits)
    assert res_exact.status == IngestionStatus.EXTRACTED.value

    res_over = ingest_file(over_file, allowed_root=tmp_path, limits=boundary_limits)
    assert res_over.status == IngestionStatus.ZP_OR_EXE_BLOCKED.value
    assert any("file_size_exceeded" in w for w in res_over.warnings)

    # 2. ملفات تالفة وصيغ غير مدعومة (7z, mp3, woff2) تعود بـ metadata_only
    corrupt_pdf = tmp_path / "corrupt.pdf"
    corrupt_pdf.write_bytes(b"%PDF-1.7\nCorruptedBinaryStreamWithoutXref")
    res_corrupt = ingest_file(corrupt_pdf, allowed_root=tmp_path)
    assert res_corrupt.status == IngestionStatus.METADATA_ONLY.value
    assert any("parser_fallback_to_metadata" in w for w in res_corrupt.warnings)

    archive_7z = tmp_path / "bundle.7z"
    archive_7z.write_bytes(b"7z\xbc\xaf\x27\x1c\x00\x04Fake7zHeader")
    res_7z = ingest_file(archive_7z, allowed_root=tmp_path)
    assert res_7z.status == IngestionStatus.METADATA_ONLY.value
    assert res_7z.mime == "application/x-7z-compressed"
    assert any("unsupported_format_metadata_only" in w for w in res_7z.warnings)

    # 3. اختبار عزل مفتاح الـ Cache بين المستأجرين (Multi-Tenant + Versioned Idempotency)
    unknown_bin = tmp_path / "mystery.unknownfmt"
    unknown_bin.write_bytes(b"\x01\x02\x03\x04CustomProprietaryFormat")

    pipeline = IngestionPipeline()
    res_t1_a, cached_t1_a = pipeline.process(unknown_bin, allowed_root=tmp_path, tenant_id="tenant_cairo")
    assert cached_t1_a is False
    assert res_t1_a.status == IngestionStatus.METADATA_ONLY.value

    # نفس المستأجر -> يعود من الـ Cache
    res_t1_b, cached_t1_b = pipeline.process(unknown_bin, allowed_root=tmp_path, tenant_id="tenant_cairo")
    assert cached_t1_b is True
    assert res_t1_b.id == res_t1_a.id

    # مستأجر مختلف لنفس الملف -> لا يستلم كاش المستأجر الأول!
    res_t2, cached_t2 = pipeline.process(unknown_bin, allowed_root=tmp_path, tenant_id="tenant_alex")
    assert cached_t2 is False
    assert res_t2.id == res_t1_a.id

    key_cairo = pipeline.cache_key_for(res_t1_a.id, tenant_id="tenant_cairo")
    key_alex = pipeline.cache_key_for(res_t1_a.id, tenant_id="tenant_alex")
    assert key_cairo != key_alex
    assert PIPELINE_VERSION in key_cairo

    metrics = pipeline.extraction_metrics()
    assert "application/octet-stream" in metrics
    assert sanitize_filename("../../evil<script>.pdf") == "evilscript.pdf"


def test_all_security_branches_and_format_coverage(tmp_path: Path) -> None:
    """
    اختبار شامل لجميع فروع الأمان المتبقية وبصمات الصيغ (لضمان تغطية >95% شاملة كل فروع الأمان):
      - جميع بصمات Magic Bytes (GIF, WEBP, BMP, TIFF, EPS, SQLite, GZ, RAR, TAR, ELF, Class, WASM, SH, Audio/Video/Fonts, HTML).
      - تعارض LFH مع Central Directory، وتجاوز الحجم التراكمي، وتجاوز عمق المجلدات ونسبة الضغط، وأرشيف متداخل متخفٍّ ببصمة ثنائية.
      - مورد PSD 0x03EE (Pascal channel names) وصورة PNG بـ DPI منخفض.
      - نقطة دخول العامل المعزول `_cli_worker_main`.
    """
    magic_cases = [
        (b"GIF89a\x01\x00", "a.gif", "image/gif"),
        (b"RIFF\x00\x00\x00\x00WEBP", "a.webp", "image/webp"),
        (b"BM\x00\x00\x00\x00", "a.bmp", "image/bmp"),
        (b"II*\x00\x08\x00", "a.tif", "image/tiff"),
        (b"%!PS-Adobe-3.0", "a.eps", "application/postscript"),
        (b"SQLite format 3\x00", "a.sqlite", "application/vnd.sqlite3"),
        (b"\x1f\x8b\x08\x00", "a.gz", "application/gzip"),
        (b"Rar!\x1a\x07\x00", "a.rar", "application/vnd.rar"),
        (b"\x00" * 257 + b"ustar\x00", "a.tar", "application/x-tar"),
        (b"\x7fELF\x02\x01", "a.elf", "application/x-elf"),
        (b"\xca\xfe\xba\xbe\x00", "a.class", "application/x-java-class"),
        (b"\x00asm\x01\x00\x00\x00", "a.wasm", "application/wasm"),
        (b"#!/bin/sh\necho hi", "a.sh", "application/x-sh"),
        (b"ID3\x03\x00", "a.mp3", "audio/mpeg"),
        (b"fLaC\x00\x00", "a.flac", "audio/flac"),
        (b"OggS\x00\x02", "a.ogg", "audio/ogg"),
        (b"\x00\x00\x00\x18ftypmp42", "a.mp4", "video/mp4"),
        (b"wOFF\x00\x01", "a.woff", "font/woff"),
        (b"wOF2\x00\x01", "a.woff2", "font/woff2"),
        (b"OTTO\x00\x01", "a.otf", "font/otf"),
        (b"<!DOCTYPE html><html></html>", "a.html", "text/html"),
        (b"REM simple batch", "a.bat", "application/x-bat"),
    ]
    for head_bytes, fname, expected_mime in magic_cases:
        detected, warns = detect_magic_mime(head_bytes, fname)
        assert detected == expected_mime, f"Failed for {fname}: got {detected}"
        assert warns == []

    # 1. أرشيف يتجاوز عمق المجلدات المسموح (archive_depth_exceeded)
    deep_zip = tmp_path / "deep_dirs.zip"
    with zipfile.ZipFile(deep_zip, "w") as zf:
        zf.writestr("a/b/c/d/e/f/g/h/i/j.txt", "deep")
    res_deep = ingest_file(deep_zip, allowed_root=tmp_path, limits=IngestionLimits(max_directory_depth=4))
    assert res_deep.status == IngestionStatus.ZP_OR_EXE_BLOCKED.value
    assert any("archive_depth_exceeded" in w for w in res_deep.warnings)

    # 2. أرشيف يتجاوز نسبة الضغط المسموحة (zip_bomb_ratio_exceeded)
    ratio_zip = tmp_path / "high_ratio.zip"
    with zipfile.ZipFile(ratio_zip, "w", compression=zipfile.ZIP_DEFLATED) as zf:
        zf.writestr("zeros.bin", b"\x00" * (2 * 1024 * 1024))
    res_ratio = ingest_file(
        ratio_zip,
        allowed_root=tmp_path,
        limits=IngestionLimits(max_compression_ratio=5.0),
    )
    assert res_ratio.status == IngestionStatus.ZP_OR_EXE_BLOCKED.value
    assert any("zip_bomb_ratio_exceeded" in w for w in res_ratio.warnings)

    # 3. أرشيف متداخل متخفٍّ بامتداد .bin لكن محتواه يبدأ بـ PK\x03\x04
    disguised_nested = tmp_path / "disguised_nested.zip"
    with zipfile.ZipFile(disguised_nested, "w") as zf:
        zf.writestr("payload.bin", b"PK\x03\x04FakeInnerZipStream")
    res_disg = ingest_file(disguised_nested, allowed_root=tmp_path)
    assert res_disg.status == IngestionStatus.ZP_OR_EXE_BLOCKED.value
    assert any("zip_bomb_nested_archive" in w for w in res_disg.warnings)

    # 4. تعارض حجم Local File Header مع Central Directory (فقط أول موضع يُزوّر)
    lfh_mismatch_zip = tmp_path / "lfh_mismatch.zip"
    with zipfile.ZipFile(lfh_mismatch_zip, "w", compression=zipfile.ZIP_STORED) as zf:
        zf.writestr("doc.txt", b"B" * 150)
    raw_lfh = bytearray(lfh_mismatch_zip.read_bytes())
    first_pos = raw_lfh.find(struct.pack("<I", 150))
    assert first_pos != -1
    raw_lfh[first_pos : first_pos + 4] = struct.pack("<I", 10)
    lfh_mismatch_zip.write_bytes(bytes(raw_lfh))
    res_lfh = ingest_file(lfh_mismatch_zip, allowed_root=tmp_path)
    assert res_lfh.status == IngestionStatus.ZP_OR_EXE_BLOCKED.value
    assert any("zip_bomb_spoofed_header" in w for w in res_lfh.warnings)

    # 5. PSD مع مورد 0x03EE (Pascal string channel names) وصورة PNG بـ DPI منخفض مدمج
    psd_pascal = tmp_path / "pascal_spot.psd"
    psd_hdr = b"8BPS" + struct.pack(">H6sHIIHH", 1, b"\x00" * 6, 5, 1000, 1000, 8, 4)
    pascal_data = b"\x09Spot Gold\x05Alpha"
    block_1006 = (
        b"8BIM"
        + struct.pack(">H", 0x03EE)
        + b"\x00\x00"
        + struct.pack(">I", len(pascal_data))
        + pascal_data
        + (b"\x00" if len(pascal_data) % 2 == 1 else b"")
    )
    psd_pascal.write_bytes(
        psd_hdr + struct.pack(">I", 0) + struct.pack(">I", len(block_1006)) + block_1006
    )
    res_pascal = ingest_file(psd_pascal, allowed_root=tmp_path)
    assert res_pascal.metadata["channel_names"] == ["Spot Gold", "Alpha"]

    low_png = tmp_path / "low_dpi.png"
    img = Image.new("RGB", (100, 100), color=(255, 0, 0))
    img.save(low_png, dpi=(72, 72))
    res_low_png = ingest_file(low_png, allowed_root=tmp_path)
    assert any("low_print_dpi" in w for w in res_low_png.warnings)

    # 6. تشغيل _cli_worker_main مباشرةً
    txt_worker = tmp_path / "worker.txt"
    txt_worker.write_text("اختبار عامل سطر الأوامر", encoding="utf-8")
    rc = _cli_worker_main(
        [
            "--worker-file",
            str(txt_worker),
            "--worker-root",
            str(tmp_path),
            "--worker-limits",
            "{}",
        ]
    )
    assert rc == 0

