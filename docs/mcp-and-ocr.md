# MCP و OCR — ما بُني، وكيف يُختبر، وما لم يُتحقَّق منه

هذا المستند يشرح الطبقتين الجديدتين بنفس القاعدة المتبعة في المشروع:
**لا ادّعاء قدرة بلا دليل، وكل حدّ مكتوب.**

---

## 1) MCP — Model Context Protocol

### ما هو
بروتوكول مفتوح (Anthropic) يسمح لخادم خارجي بالإعلان عن أدوات وتنفيذها عبر
JSON-RPC 2.0. اتصالنا **stdio**: نبدأ عملية، نكتب طلبات على stdin، ونقرأ ردوداً
من stdout.

### التنفيذ

| الملف | المسؤولية |
|---|---|
| `src/mcp/protocol.py` | بناء/تحليل الرسائل، أخطاء نوعية، استخراج الأدوات والنصوص |
| `src/mcp/client.py` | تشغيل العملية، مصافحة، `tools/list`، `tools/call`، مهل، إغلاق آمن |
| `src/mcp/config.py` | قراءة `mcp.json`، تحقق صارم، `enabled: false` افتراضياً |
| `src/mcp/bridge.py` | تحويل كل أداة إلى `ToolSpec` باسم `mcp__{server}__{tool}` |
| `src/mcp/cli.py` | `servers` / `tools` / `call` — مع `--yes` إلزامي للتشغيل |

### لماذا لا نستخدم حزمة `mcp` الجاهزة؟
- صفر اعتماديات جديدة في نظام يلتزم بالتكلفة الصفرية والبساطة.
- سطح صغير مُختبَر بالكامل: كل رفض له نوع خطأ معروف.
- **التحكم في البيئة**: لا نمرّر `GEMINI_API_KEY` أو أي سرّ إلى خادم خارجي.

### الضوابط الأمنية (وكلٌّ منها مُختبَر)

| الضابط | الاختبار |
|---|---|
| كل أداة بعيدة خطرها `execute` ونطاقها `mcp.call` | `test_bridge_registers_tools` |
| بلا نطاق `mcp.call` ⇒ رفض قبل أي تنفيذ | `test_bridge_call_denied_without_scope` |
| الميزانية تُحتسب وتوقف النداءات الزائدة | `test_bridge_quota_stops_calls` |
| خطأ الخادم نتيجة لا انهيار | `test_bridge_remote_error_is_a_result_not_a_crash` |
| مهلة صريحة بدل تعليق | `test_timeout_does_not_hang` |
| انهيار الخادم يُكتشف ويُبلَّغ | `test_call_after_close_is_rejected` |
| **عزل البيئة**: لا تسريب مفاتيح | `test_secrets_are_not_passed_to_server` + E2E |
| `enabled: false` افتراضياً | `test_disabled_by_default` |
| `--yes` إلزامي لتشغيل عملية | `test_start_mcp_servers_requires_explicit_yes` |

### الاستخدام

```bash
cp mcp.example.json mcp.json     # ثم عدّل الأمر
python -m src.mcp.cli servers --config mcp.json
python -m src.mcp.cli tools --config mcp.json --server files --yes

# داخل مهمة الوكيل: تُسجَّل الأدوات في السجل نفسه
python -m src.main --task "اقرأ الملف البعيد" --mcp-config mcp.json \
  --mcp-server files --yes --grant mcp.call
```

### الحد المعلن
نُنفّذ مجموعة فرعية: `initialize` / `notifications/initialized` / `tools/list` /
`tools/call`. **لا** resources ولا prompts ولا subscriptions ولا sampling.
خوادم MCP الشائعة عبر `npx`/`uvx` تحتاج Node/uv وشبكة على جهازك — لم نختبرها هنا؛
اختبرنا خادماً حقيقياً يعمل كعملية فرعية عبر stdio (42 اختباراً).

---

## 2) OCR — نص من الصور والمستندات الممسوحة

### التنفيذ

| الملف | المسؤولية |
|---|---|
| `src/ocr/engine.py` | محرّك Tesseract عبر CLI، معالجة مسبقة بـ Pillow، قراءة PDF عبر pypdf |
| `src/ocr/pipeline.py` | OCR ← فهرس معرفة بمرجع `ocr:file#Lx-Ly` |
| `src/ocr/cli.py` | `health` / `extract` مع `--ingest` و`--json` |
| `src/tools/builtin.py` | أداة `ocr_extract` (قراءة فقط، `fs.read`) |

### لماذا Tesseract لا حزمة OCR ثقيلة؟
مجاني بالكامل، مفتوح المصدر، ويدعم العربية (`ara`) — ويُستدعى كسطر أوامر فلا
يضيف تبعية Python. البديل (`easyocr`) يجرّ تورچ بمئات الميغابايتات.

### المعالجة المسبقة (حقيقية ومُختبَرة بـ Pillow)
رمادي ← سطوع تلقائي ← تكبير إلى العرض 1000px (بحد 4×) ← PNG مؤقت يُحذف بعد الاستخدام.
السبب: Tesseract ينهار على الصور الصغيرة أو منخفضة التباين.

### الخصوصية والأمان
- الملف يُقرأ فقط؛ **لا كتابة** على المصدر.
- المسار يمر بحصر الجذور المسموحة (`fs.read`) — `../` مرفوض.
- النص المستخرج **بيانات غير موثوقة**: قد يحتوي ما يشبه التعليمات (حقن). يُخزَّن
  كمرجع ويُعرض في كتلة سياق موثّقة، ولا يُنفَّذ أبداً.

### الاستخدام

```bash
python -m src.ocr.cli health
python -m src.ocr.cli extract scan.png --lang ara+eng
python -m src.ocr.cli extract docs/*.pdf --ingest .agent_knowledge/corpus.jsonl

# داخل مهمة: استخراج ثم فهرسة ثم استخدام كسياق
python -m src.main --task "لخّص الفاتورة" --ocr scan.png --knowledge .agent_knowledge/corpus.jsonl
```

### الحد المعلن (مهم)
1. **قراءة صور حقيقية غير مُتحقَّق منها في هذه البيئة**: `tesseract` غير مثبت هنا،
   فكل اختبارات مسار الصور تستخدم محرّكاً وهمياً (`StubBackend`) + معالجة صور
   حقيقية بـ Pillow. الادّعاء المُتحقَّق: التوجيه والمعالجة والفهرسة والتقارير.
2. **PDF نصي مُتحقَّق فعلاً** عبر `pypdf` على ملف PDF حقيقي مُولَّد في الاختبار.
3. **PDF ممسوح** (بلا طبقة نص): نُبلغ بوضوح ونطلب تحويله إلى صور — لا نخمّن.
4. جودة الاستخراج العربي تعتمد على حزمة `tesseract-ocr-ara` وعلى جودة الصورة؛ لا ضمان.

### كيف يبدو الحدّ في الواجهة؟

```text
$ python -m src.ocr.cli health
⚠️ لا محرّك OCR جاهز:
  ⛔ tesseract — apt install tesseract-ocr tesseract-ocr-ara
  ⛔ pypdf — pip install -r requirements-ocr.txt

OCR يتطلب Tesseract (مجاني) على الجهاز؛ وقراءة PDF النصية تحتاج pypdf.
```

---

## 3) الأدلة

```bash
.venv/bin/python -m pytest tests/test_mcp.py tests/test_ocr.py -q   # 86 اختباراً
.venv/bin/python scripts/e2e_knowledge_quantum.py                  # 30/30 حلقة
```

حلقات MCP/OCR في السكربت تُثبت: المصافحة، رفض النداء بلا نطاق، التنفيذ بموافقة
وميزانية، عزل البيئة، معالجة صورة حقيقية، فهرسة النص بمرجع، ورفض ملف مفقود
برسالة لا بانهيار.
