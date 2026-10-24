# معمارية خط أنابيب استيعاب واستخراج الملفات الطبقي (Layered File Ingestion Pipeline v2.0)

> **المبدأ الحاكم:** دعم «كل» الصيغ حرفياً مستحيل؛ الحل الهندسي الصحيح هو **Pipeline طبقي**
> يتعامل مع الصيغ المدعومة عبر محللات متخصصة ومعزولة، مع سلسلة **Fallback** تنتهي دائماً بقبول الملف
> غير المدعوم أو التالف وتخزينه بـ `metadata_only` دون إسقاط النظام، وإخراج **JSON موحد** لكل ملف.
>
> الوحدة البرمجية المنفِّذة: [`src/ingestion/pipeline.py`](../src/ingestion/pipeline.py) (`PIPELINE_VERSION = "2.1.0"`) · الاختبارات الهجومية ومتعددة الصيغ: [`tests/test_ingestion.py`](../tests/test_ingestion.py).

---

## 🏗️ 1. تدفق المعمارية (The 8-Stage Hardened Pipeline)

```text
upload/read (64KB streaming chunks + guard_path + sanitize_filename)
  ↓
detect (Magic Bytes vs Extension mismatch check + Script content heuristics)
  ↓
scan (ClamAV / EICAR / custom AV hook · Executable/Mach-O/WASM/Script/Macro block · Max size)
  ↓
route (MIME table → Optional Subprocess Isolation → Specialist Parser → metadata_only)
  ↓
extract (Defused XML · Streaming zlib Zip-Bomb cap · Allowlist SVG/HTML · PSD 8BIM · Full XLSX · Effective DPI)
  ↓
normalize (Unified JSON Schema: id="sha256:...", status="extracted|partial|metadata_only|blocked")
  ↓
store & index (Idempotency cache by tenant_id + PIPELINE_VERSION + limits.fingerprint() + sha256)
```

---

## 🔍 2. الكشف عن النوع الحقيقي (لا تثق بالامتداد — `detect_magic_mime`)

تقرأ دالة `_stream_sha256_and_head` أول 4096 بايت أثناء حساب بصمة `SHA-256` بتدفق مجزّأ (`64KB chunks`)، وتفحص `detect_magic_mime` البصمة الثنائية (`Magic Bytes`) ومؤشرات الاسكربتات:

| البصمة الثنائية (`Magic Bytes` / المؤشر) | نوع `MIME` الفعلي | السلوك الأمني والتوجيه |
|---|---|---|
| `%PDF-` | `application/pdf` (أو `.ai` الحديث) | تحذير `suspicious_extension_mismatch` إن لم يكن `.pdf` أو `.ai` |
| `\x89PNG\r\n\x1a\n` · `\xff\xd8\xff` · `GIF8` · `RIFF..WEBP` · `II*\x00` · `BM` | `image/png` · `image/jpeg` · `image/gif` · `image/webp` · `image/tiff` · `image/bmp` | فحص سقف البكسلات (`max_image_pixels`) وحساب `DPI` المدمج والفعلي |
| **`8BPS`** | `image/vnd.adobe.photoshop` (`PSD`) | قراءة ترويسة `PSD` وقسم `Image Resources` (`8BIM`: `0x03EE`, `0x0415`, `0x0435`) للتمييز بين القنوات الأساسية وقنوات `Spot/Alpha` |
| `PK\x03\x04` | `application/zip` أو `DOCX/XLSX/PPTX` | فحص تدفقي بـ `zlib.decompressobj(-15)` ضد تزوير الترويسة والأرشيفات المتداخلة وماكرو `vbaProject.bin` |
| `\xd0\xcf\x11\xe0\xa1\xb1\x1a\xe1` | `application/x-ole-storage` (`OLE2`: `.doc/.xls/.ppt`) | فحص وجود `_VBA_PROJECT` / `Macros` ← إن وُجد يُحظر فوراً (`blocked`)، وإلا `metadata_only` |
| `7z\xbc\xaf\x27\x1c` · `Rar!\x1a\x07` · `ustar` · `\x1f\x8b` | `7z` · `rar` · `tar` · `gzip` | توثيق صريح كـ `metadata_only` (`unsupported_format_metadata_only`) في المرحلة الحالية |
| `ID3` · `fLaC` · `OggS` · `ftyp` · `wOFF` · `wOF2` · `OTTO` | صوت · فيديو · خطوط (`MP3, FLAC, OGG, MP4, WOFF, WOFF2, OTF`) | توثيق صريح كـ `metadata_only` (`unsupported_format_metadata_only`) |
| **`MZ` · `\x7fELF` · `\xfe\xed\xfa\xcf` (`Mach-O`) · `\xca\xfe\xba\xbe` (`Class`) · `\x00asm` (`WASM`) · `#!` · `@echo off / powershell / wscript`** | ملفات تنفيذية واسكربتات (`EXE, DLL, ELF, Mach-O, Java, WASM, SH, BAT, PS1, VBS`) | 🛑 **`status = "blocked"` فوراً** — تُكشف بالبصمة الثنائية أو محتوى الاسكربت حتى لو زُوّر امتدادها إلى `.txt` أو `.pdf` |

---

## 🛡️ 3. الإصلاحات والضوابط الأمنية (P0 + P1 + P2 Hardening)

### أولاً: ضوابط P0 (حرجة)
1. **حماية XML الصارمة (`safe_xml_fromstring` + `defusedxml`):**
   - حظر أي تصريح `<!DOCTYPE`, `<!ENTITY`, `<!ELEMENT`, `<!ATTLIST` قبل التحليل لمنع هجمات **XXE** و**Billion Laughs**.
   - استخدام `defusedxml.ElementTree` مع فرض سقوف حيّة عبر `iterparse` على حجم XML (`max_xml_bytes = 10 MB`)، عمق الشجرة (`max_xml_depth = 64`)، وعدد العناصر (`max_xml_elements = 50,000`).
2. **فك الضغط التدفقي المحدود وعدم الثقة بترويسة ZIP (`_safe_read_zip_entry`):**
   - ترويسة `ZIP` (`info.file_size`) قابلة للتزوير؛ لذا يطابق المحرك ترويسة `Local File Header` مع `Central Directory`، ويفك الضغط عبر `zlib.decompressobj(-15)` بسقف `max_length` صارم أثناء القراءة الفعلية.
   - إذا تجاوز البث الفعلي الحجم المعلن في الترويسة (`zip_bomb_spoofed_header`) أو السقف التراكمي (`max_uncompressed_bytes = 100 MB`) يُقطع الفك فوراً ويُحظر الملف.
   - حظر **الأرشيفات المتداخلة (`Nested Archives`)** بالامتداد وبالبصمة الثنائية (`PK\x03\x04`, `\x1f\x8b`, `7z`, `Rar!`).
   - حماية الصور من قنابل فك الضغط (`max_image_pixels = 64,000,000` بكسل) وتحديد سقف صفحات PDF (`max_pdf_pages = 500`).
3. **عزل المعالجة في عملية فرعية (`ingest_file_isolated` / `isolate_in_subprocess=True`):**
   - تشغيل المحلّل في عملية Python فرعية معزولة بلا صدفة (`shell=False`)، ببيئة نظيفة (`PATH=""`) وسقف زمني (`parser_timeout_seconds = 15s`) لمنع تعليق أو اختراق العملية الرئيسية.
   - عندما يكون العزل مفعّلاً، ينشئ الأب ملفاً مرحلياً خاصاً بصلاحية `0400` داخل مجلد `0700`؛ يُحسب SHA-256 والحجم أثناء مرور النسخ الواحد، وتكون هذه البصمة `id` ومفتاح الكاش. يعاد استخدام staging نفسه عند تشغيل العامل؛ فلا يعيد الأب قراءة المصدر لحساب المفتاح. العامل لا يستلم مسار المصدر أو جذر المستخدم، بل مسار الملف المرحلي والجذر المؤقت الخاص فقط.
   - يجري الكشف والفحص والاستخراج على كامل النسخة المرحلية قبل أي اقتطاع للمخرج. يكتب العامل JSON بسقف 5 MiB و`RLIMIT_FSIZE=6 MiB`؛ `stdout/stderr=DEVNULL`. عند `EFBIG` يعيد رمزاً معروفاً (`worker_result_efbig`)، وعند تجاوز حجم JSON يقتطع النص عمداً (`partial` + `text_truncated`). يقرأ الأب النتيجة بـ `O_NOFOLLOW` ويفحص `fstat` والحجم والبنية والعمق وعدد المفاتيح وأطوال السلاسل والهوية.
   - الجذر المختار لا يكون symlink، ولا `/` أو `$HOME` أو `/tmp` ولا أباً لمجلد المشروع أو مجلدات النظام/الأسرار المعروفة؛ تُحل الجذور والملفات بـ `realpath` ويُتحقق من containment.
   - حدود فك ZIP لكل خطوة، مع `memoryview` لتفادي نسخة تجميع ثانية؛ يحرر `safe_xml_fromstring` الـ view فور تحويله، مع التحقق من اكتمال DEFLATE والحجم وCRC وعدم استدعاء `flush()` غير المحدود.
   - مجلد العامل المؤقت يُحذف عبر `TemporaryDirectory` حتى بعد timeout/kill؛ ويضبط `TMPDIR` و`HOME` إلى مجلد العامل.
   - موارد النظام الاختيارية تشمل `RLIMIT_NPROC`, `RLIMIT_NOFILE`, `RLIMIT_CORE=0` عند دعم المنصة.

### ثانياً: ضوابط P1 (عالية)
1. **تعقيم `SVG/HTML` بقائمة بيضاء (`_AllowlistSvgHtmlSanitizer`):**
   - استبدال الحذف النصي بمحلّل شجري (`HTMLParser`) يعتمد قائمة بيضاء صارمة للوسوم (`_ALLOWED_SVG_HTML_TAGS`) والسمات (`_ALLOWED_SVG_HTML_ATTRS`).
   - حذف الوسوم الخطيرة ومحتواها بالكامل (`<script>`, `<foreignObject>`, `<iframe>`, `<object>`, `<embed>`, `<style>`, `<handler>`).
   - فك ترميز كيانات HTML (`html.unescape`) وإزالة محارف التحكم والمسافات قبل فحص الروابط (`href`, `xlink:href`, `src`) وسمة `style`، مما يحبط التفافات `&#106;avascript:alert(1)` و`data:` و`url(javascript:...)`.
   - إرفاق ترويسات الأمان الموصى بها عند التقديم: `Content-Security-Policy: default-src 'none'; style-src 'unsafe-inline'; sandbox` و`Content-Disposition: attachment` و`X-Content-Type-Options: nosniff`.
2. **طبقة فحص الفيروسات والماكرو (`scan_for_malware` + Macro Guard):**
   - دعم `ClamAV` (`clamscan --no-summary` بلا shell) أو أي ماسح مخصّص (`av_scanner`)، مع كشف توقيع `EICAR` القياسي في كامل الملف بتدفق chunks (بما فيه المطابقة العابرة لحد chunk). يجري حد حجم الملف قبل المسح الكامل.
   - حظر ماكرو `VBA` داخل ملفات Office الحديثة (`vbaProject.bin`, `activeX/`) وملفات `OLE2` القديمة (`_VBA_PROJECT`).
3. **مفتاح Cache مركّب يعزل المستأجرين والإصدار (`IngestionPipeline.cache_key_for`):**
   - المفتاح: `f"{tenant_id}:{PIPELINE_VERSION}:{limits.fingerprint()}:{file_id}"` لمنع تسرب نتائج مستخدم لمستخدم آخر ولإبطال الكاش تلقائياً عند ترقية المحلّل أو تغيير الحدود.

### ثالثاً: تحسينات الدقة التقنية للمطابع والعربية (P2)
1. **قراءة قسم `Image Resources` (`8BIM`) في ملفات `PSD` (`_extract_psd_header`):**
   - ترويسة `PSD` تعطي عدد القنوات الكلي (`channels`) فقط؛ لذلك يحسب المحرك `base_channels` (مثل `4` لـ `CMYK` و`3` لـ `RGB`) و`extra_channels`، ثم يقرأ قسم `Image Resources` (`0x03EE` Pascal Alpha Names، `0x0415` Unicode Alpha Names، و`0x0435` `DisplayInfo`) لاستخراج الأسماء الحقيقية لقنوات `Spot/Alpha` (مثل `"Pantone 186 C"`, `"White Underbase"`).
2. **حساب الدقة الفعلية للطباعة (`effective_dpi` مقابل `dpi` المدمج):**
   - التمييز بين وسم `DPI` المدمج في الميتاداتا (الذي يغيب كثيراً في `JPEG` فيُصدر تحذير `missing_dpi_metadata`) وبين **الدقة الفعلية للطباعة (`effective_dpi`)** المحسوبة من `البكسلات ÷ (مقاس الطباعة بالسنتيمتر ÷ 2.54)` عند تمرير `target_print_width_cm` و`target_print_height_cm`.
3. **استخراج `XLSX` الكامل (`_extract_openxml_document`):**
   - قراءة `xl/sharedStrings.xml` بالإضافة إلى جميع أوراق العمل `xl/worksheets/sheet*.xml` لاستخراج النصوص المشتركة (`t="s"`), النصوص المباشرة (`t="inlineStr"`), الأرقام، ونتائج المعادلات (`<f>` + `<v>`)، مع تعليم الملف بـ `status="partial"` وتحذير `xlsx_truncated` عند تجاوز `max_xlsx_rows`.
4. **ثقة الترميز العربي `Windows-1256` (`detect_and_decode_text_with_confidence`):**
   - حساب درجة الثقة (`encoding_confidence`) بناءً على نسبة الحروف العربية المتماسكة إلى البايتات العالية، مع الاستعانة بـ `charset_normalizer` كمرجع ثانٍ.

---

## 📋 4. عقد المخرج الموحد (Unified JSON Output Schema v2.0)

```json
{
  "id": "sha256:...",
  "name": "design_cmyk_spot.psd",
  "mime": "image/vnd.adobe.photoshop",
  "size": 146,
  "status": "extracted",
  "parser": "psd_header_resources",
  "language": null,
  "text": "",
  "pages": 1,
  "metadata": {
    "author": null,
    "created": null,
    "psd_version": 1,
    "channels": 6,
    "base_channels": 4,
    "extra_channels": 2,
    "channel_names": ["Pantone 186 C", "White Underbase"],
    "has_spot_channel_resources": true,
    "width": 2480,
    "height": 3508,
    "bit_depth": 8,
    "color_mode": "CMYK"
  },
  "warnings": []
}
```

---

## ✅ 5. التحقق المنفذ وحدود الاستنتاج

- `ruff check .`: ناجح.
- `pytest -q`: **653 passed, 68 warnings**؛ 67 تحذيراً من `DeprecationWarning` في Qiskit وتحذير واحد من اختبار ZIP يضيف اسماً مكرراً عمداً. لا تظهر `ResourceWarning`. اختبارات الاستيعاب/حارس المسارات والـrunner: **195 passed** مع `-W error::ResourceWarning`.
- فحوص التغطية المركزة:
  `pytest -q tests/test_ingestion.py tests/test_path_guard_security.py --cov=src.ingestion.pipeline --cov=src.core.path_guard --cov-branch --cov-report=term-missing:skip-covered`
  أعادت **191 passed, 1 warning**. تغطية الأسطر والفروع **100%** لكل من `_read_worker_result`, `_check_zip_safety`, `_cli_worker_main`, `sanitize_svg_or_html`, `sanitize_for_log`, `guard_path`, `detect_magic_mime`، و`scan_for_malware`. هذا لا يعني 100% للوحدتين بالكامل: تغطية `pipeline.py` الإجمالية 90% في هذا النطاق، والـsuite المركزة ليست suite التطبيق كلها.
- `spec_check.py`, `policy_guard.py`, `secret_scan.py`, `dependency_guard.py`، و`pii_scan.py` نجحت. أبلغ `dependency_guard.py` عن غياب اختياري لـ`browser-use` من بيئة الاختبار.
- شُغّل `pip-audit` على `requirements.txt`, `requirements-quantum.txt`, `requirements-ocr.txt`, و`requirements-integrations.txt` إلى تقرير مؤقت خارج الريبو، ثم `security_audit.py` عليه: **82 حزمة محلولة، 0 متأثرة، ولا ثغرات معروفة** في هذا الفحص. التقرير المؤقت ليس مرفقاً في Git، والنتيجة لقطة وقتية لا ضماناً مستقبلياً. أضيف كذلك تدقيق يومي لمجموعات النواة في `security-scan.yml`.
- `detect_magic_mime` و`scan_for_malware` صارتا 100% أسطر/فروع في suite التغطية المركزة؛ ويبحث فحص EICAR الآن في كامل الملف ضمن chunks مع overlap بين الحدود، بعد حارس الحجم.
- الفجوات المتبقية في الدوال المطلوب تدقيقها: `detect_and_decode_text_with_confidence` (الأسطر 1001، 1003، 1033–1038)، `_extract_openxml_document` (1540–1553، 1597، 1622–1623، 1633)، و`_extract_psd_header` (1745، 1750). فروعها غير المغطاة مذكورة في ملف PR/تقرير التغطية؛ لم تُغلق بعد.
- لم تُنفذ مصفوفة الملفات الواقعية بعد؛ سجلها في [مصفوفة الاختبار اليدوي](file-ingestion-manual-test-matrix.md).
- حفظ SVG الأصلي دون تغيير لم يُتحقق منه؛ طبقة `pipeline.py` تنتج المعاينة المعقّمة فقط ولا تثبت سلوك طبقة التخزين.
- لا تُعد نتائج CI أو unit tests بديلاً عن مصفوفة الاختبار الواقعية أو مراجعة أمنية مستقلة.
