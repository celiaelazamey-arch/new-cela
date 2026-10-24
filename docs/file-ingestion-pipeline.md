# معمارية خط أنابيب استيعاب واستخراج الملفات الطبقي (Layered File Ingestion Pipeline)

> **المبدأ الحاكم:** دعم «كل» الصيغ حرفياً مستحيل؛ الحل الهندسي الصحيح هو **Pipeline طبقي**
> يتعامل مع الصيغ الأشهر عبر محللات متخصصة، مع سلسلة **Fallback** تنتهي دائماً بقبول الملف
> وتخزينه بـ `metadata_only` دون إسقاط النظام، وإخراج **JSON موحد** لكل ملف.
>
> الوحدة البرمجية المنفِّذة: [`src/ingestion/pipeline.py`](../src/ingestion/pipeline.py) · الاختبارات (Corpus): [`tests/test_ingestion.py`](../tests/test_ingestion.py).

---

## 🏗️ 1. تدفق المعمارية (The 8-Stage Pipeline)

```text
upload/read (64KB streaming chunks)
  ↓
detect (Magic Bytes vs Extension mismatch check)
  ↓
scan (Executable block · Max size · Zip-Bomb guard · Path Traversal · SVG/HTML XSS & XXE)
  ↓
route (MIME table → Specialist Parser → Office/Archive → Image/PSD/DPI → OCR notice → metadata_only)
  ↓
extract (UTF-8 / Windows-1256 Arabic · pypdf · OpenXML stdlib · Pillow DPI · PSD 8BPS header)
  ↓
normalize (Unified JSON Schema: id="sha256:...", status="extracted|partial|metadata_only|blocked")
  ↓
store & index (Idempotency cache by sha256 + per-MIME extraction success metrics)
```

---

## 🔍 2. الكشف عن النوع الحقيقي (لا تثق بالامتداد — `detect_magic_mime`)

تقرأ دالة `_stream_sha256_and_head` أول 4096 بايت أثناء حساب بصمة `SHA-256` بتدفق مجزّأ (`64KB chunks`)، وتفحص `detect_magic_mime` البصمة الثنائية (`Magic Bytes`):

| البصمة الثنائية (`Magic Bytes`) | نوع `MIME` الفعلي | السلوك عند تعارض الامتداد |
|---|---|---|
| `%PDF-` | `application/pdf` (أو `.ai` الحديث) | تحذير `suspicious_extension_mismatch` إن لم يكن `.pdf` أو `.ai` |
| `\x89PNG\r\n\x1a\n` · `\xff\xd8\xff` · `GIF8` · `RIFF..WEBP` · `II*\x00` | `image/png` · `image/jpeg` · `image/gif` · `image/webp` · `image/tiff` | كشف فوري وتوجيه لمحلل الصور وفحص `DPI` |
| **`8BPS`** | `image/vnd.adobe.photoshop` (`PSD`) | قراءة ترويسة فوتوشوب الثنائية (الأبعاد، عدد القنوات، ونظام الألوان `CMYK/RGB/Spot`) |
| `%!PS` | `application/postscript` (`EPS`) | توجيه آمن لبيانات تعريف ملفات المطابع |
| `PK\x03\x04` | `application/zip` أو `DOCX/XLSX/PPTX` | فحص حدود `Zip Bomb` أولاً قبل قراءة `word/document.xml` أو `xl/sharedStrings.xml` |
| `SQLite format 3\x00` | `application/vnd.sqlite3` | تسجيل قاعدة البيانات دون تنفيذ استعلامات عشوائية |
| **`MZ` · `\x7fELF` · `#!`** | ملفات تنفيذية (`EXE / DLL / ELF / SH`) | 🛑 **`status = "blocked"` فوراً** — لا تُنفَّذ ولا تُحلَّل أبداً (تسجّل `sha256:` و`metadata` فقط) |

---

## 🌍 3. المستخرجات حسب الفئة وملاحظات التراخيص التجارية لعام 2026

| الفئة | الأداة المطبّقة في `new-cela` | ملاحظة الترخيص التجاري والدقة العربية |
|---|---|---|
| **1. نصوص وكود (`TXT, MD, CSV, TSV, JSON, EML`)** | `detect_and_decode_text` + `stdlib csv/json/email` | ✅ يدعم `UTF-8` و`UTF-16` ويكتشف تلقائياً الترميز العربي القديم **`Windows-1256` (`cp1256`)** بحساب كثافة الحروف العربية لمنع تشوّه الحروف |
| **2. ملفات `PDF`** | **`pypdf`** (مع كشف PDF الممسوح ضوئياً `scanned_pdf_needs_ocr`) | ✅ **`pypdf` بترخيص BSD حر تجارياً 100%** (تجنّبنا `PyMuPDF` لأن ترخيصه **AGPL** يفرض فتح الكود المصدري أو شراء ترخيص تجاري مكلف!) |
| **3. ملفات `Word / Excel / PowerPoint` (`DOCX, XLSX, PPTX`)** | `_extract_openxml_document` (`zipfile` + `ElementTree` من المكتبة القياسية) | ✅ **0 تبعيات خارجية إضافية**، مع فحص مسبق لقنابل الضغط (`_check_zip_safety`) ومنع `XXE` |
| **4. الصور وتصميمات المطابع (`PNG, JPG, TIFF, PSD, SVG`)** | **`Pillow`** + محلل ترويسة **`PSD` (`8BPS`)** + معقّم **`SVG`** | ✅ يستخرج الأبعاد، نظام الألوان (`CMYK / RGB / L`)، عدد القنوات (لفصل ألوان السلك سكرين)، والـ `DPI` (يحذّر `low_print_dpi` إذا قلّت عن `300 DPI`) |
| **5. OCR العربي (`صور + PDF ممسوح`)** | **`Tesseract OCR` (`ara+eng`)** في [`src/ocr/engine.py`](../src/ocr/engine.py) | ✅ ترخيص **Apache-2.0** مجاني وتجاري 100% |
| **6. ملفات غير مدعومة أو تالفة** | `metadata_fallback` | ✅ لا يُرفض ولا يسقط النظام أبداً ← يعود بحالة `status = "metadata_only"` مع بصمة `sha256:` والـ `mime` والحجم |

---

## 🛡️ 4. الضوابط الأمنية الإلزامية (Mandatory Security Controls)

1. **الحماية ضد قنابل الضغط (`Zip Bomb` — `_check_zip_safety`):**
   - سقف حجم الملف المرفوع (`max_file_size_bytes = 50 MB`).
   - سقف الحجم الإجمالي بعد الفك (`max_uncompressed_bytes = 100 MB`).
   - سقف عدد الملفات داخل الأرشيف (`max_archive_entries = 500`).
   - سقف نسبة الضغط (`max_compression_ratio = 100:1`).
   - سقف عمق المجلدات (`max_directory_depth = 8`).
   - رفض أي مسار داخل الأرشيف يحتوي على `..` أو يبدأ بـ `/` (`archive_path_traversal`).
2. **تعقيم `SVG` و`HTML` و`XML` (`sanitize_svg_or_html`):**
   - إزالة تعريفات الكيانات الخارجية `<!ENTITY ... SYSTEM/PUBLIC>` (منع **XXE / SSRF**).
   - إزالة وسوم `<script>` وسمات الأحداث الخبيثة (`onload=`, `onerror=`, `onclick=`, `javascript:`) لمنع **XSS**.
3. **تعقيم أسماء ومسارات الملفات (`sanitize_filename` + `guard_path`):**
   - تجريد أي مسار نسبي أو محارف تحكم قبل أي عملية قرص.
4. **منع استنزاف الذاكرة (`Streaming SHA-256`) ومنع تكرار المعالجة (`Idempotency`):**
   - تُقرأ الملفات في كتل `64KB` لحساب `sha256:`، ويحفظ `IngestionPipeline` النتيجة ببصمة المحتوى لتفادي إعادة تحليل نفس الملف.

---

## 📋 5. عقد المخرج الموحد (Unified JSON Output Schema)

كل استدعاء لـ `ingest_file(path)` أو `IngestionPipeline().process(path)` يعيد الكائن الموحد التالي:

```json
{
  "id": "sha256:...",
  "name": "design_cmyk.psd",
  "mime": "image/vnd.adobe.photoshop",
  "size": 58,
  "status": "extracted",
  "parser": "psd_header_parser",
  "language": null,
  "text": "PSD 2480x3508 | Color: CMYK | Channels: 5",
  "pages": 1,
  "tables": [],
  "images": [
    {
      "width": 2480,
      "height": 3508,
      "color_mode": "CMYK",
      "channels": 5
    }
  ],
  "metadata": {
    "author": null,
    "created": null,
    "psd_version": 1,
    "channels": 5,
    "width": 2480,
    "height": 3508,
    "bit_depth": 8,
    "color_mode": "CMYK"
  },
  "warnings": []
}
```
