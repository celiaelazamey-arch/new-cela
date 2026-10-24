# الإطار التنظيمي والامتثال — new-cela

> **مرجع دائم داخل النظام.** تاريخ التحقق: 2026-10-23 (America/Los_Angeles)
> المصادر: بحث ويب أُجري في الجلسة + المصادر الرسمية للاتحاد الأوروبي حين توفرت.
> مرتبط بـ [`security-threat-model.md`](security-threat-model.md) و[`SECURITY.md`](../SECURITY.md).

> ⚠️ **هذا توثيق هندسي، وليس استشارة قانونية.** التواريخ والمتطلبات الواردة هنا
> مُتحقَّق منها قدر الإمكان، لكن القرار القانوني النهائي يجب أن يراجعه مختصّ،
> خاصةً في ظل اختلاف المصادر حول بعض المواعيد (موضّح أدناه بصراحة).

---

## 🚨 0. الأهم أولاً: ما يخصّك شخصياً الآن

المشروع مُسجَّل ومُشغَّل من **مصر (الجيزة)**، ما يجعل **قانون حماية البيانات
الشخصية المصري 151/2020** هو الأثر الأقرب زمنياً، لا EU AI Act.

| العنصر | القيمة |
|---|---|
| اللائحة التنفيذية | قرار وزاري **816/2025**، صدرت 1 نوفمبر 2025، سارية 2 نوفمبر 2025 |
| فترة السماح | سنة واحدة من الإصدار |
| **انتهاء فترة السماح** | **31 أكتوبر 2026** (بعض المصادر: 1 نوفمبر 2026) |
| **الأيام المتبقية في 23 أكتوبر 2026** | **8 أيام** ⏳ |
| العقوبات | جنائية + غرامات؛ المعالجة بلا ترخيص بعد انقضاء المهلة جريمة بموجب المادة 45 |

**لماذا 31 أكتوبر وليس 1 نوفمبر؟** المصادر اختلفت: PDPC وبعض مكاتب المحاماة
تذكر 31 أكتوبر 2026، وأخرى تحسب سنة كاملة من 1 نوفمبر 2025 فتصل إلى 1 نوفمبر
2026. نصيحة المصادر العملية: **اعتبر 31 أكتوبر هو موعدك الداخلي** (الأكثر تحفظاً
وأماناً) [7](https://sog.eg/en/blog/egypt-pdpl-website-compliance-checklist).

> 📌 **ملاحظة جاهزية مهمة:** بوابة PDPC الإلكترونية للتراخيص كانت غير متاحة من
> الموقع الرسمي في تحقق 10 سبتمبر 2026، ثم أُبلغ عن فتحها في 11 أكتوبر 2026
> [13](https://dataprotection.gi/jurisdictions/egypt/). أي أن آلية التقديم نفسها
> كانت في طور التشغيل حتى قبل الموعد بأسابيع — خطّط على هذا الأساس.

---

## 1. EU AI Act — Digital Omnibus (Reg. (EU) 2026/1744)

### الحالة المؤكَّدة

| العنصر | القيمة | التحقق |
|---|---|---|
| النشر في الجريدة الرسمية | 24 يوليو 2026 | ✅ مصادر متعددة |
| **دخول حيز التنفيذ** | **27 يوليو 2026** | ✅ [المفوضية الأوروبية](https://digital-strategy.ec.europa.eu/en/news/ai-omnibus-enters-force) |
| Annex III (توظيف/تعليم/ائتمان/إنفاذ قانون) | 2 أغسطس 2026 ← **2 ديسمبر 2027** | ✅ تأجيل 16 شهراً |
| Annex I (ذكاء مدمج في منتجات) | ← **2 أغسطس 2028** | ✅ |
| طبيعة التغيير | **تأجيل، لا إلغاء** — الهيكل الأساسي لم يتغير | ✅ [CSA](https://labs.cloudsecurityalliance.org/research/csa-research-note-eu-ai-act-high-risk-deadline-omnibus-20260/) |

**تصحيح على ما ورد:** ورد أن Annex I كان موعده 2 أغسطس 2027 ثم تأجّل — التأجيل
صحيح، وعدد من المصادر يذكر الموعد الأصلي 2 أغسطس 2027 (وليس 2026) لأن Annex I
كان أصلاً على جدول مختلف عن Annex III. الفرق لا يغيّر النتيجة: **2 أغسطس 2028**.

### ما لم يتأجل — سارٍ فعلاً الآن (من 2 أغسطس 2026)

- **التزامات الشفافية (المادة 50):** سارية من 2 أغسطس 2026، لم تتأجل
  [11](https://www.softwareseni.com/eu-ai-act-article-50-watermarking-august-december-2026-deadlines/).
- **صلاحيات إنفاذ GPAI / المادة 101:** من 2 أغسطس 2026، سارية
  [14](https://www.digitalapplied.com/blog/eu-ai-august-2026-transparency-obligations-agency-checklist).
- **التزامات مزوّدي GPAI (المواد 51–56):** سارية منذ 2 أغسطس 2025.
- **الممارسات المحظورة (المادة 5):** سارية منذ 2 فبراير 2025.

### ✅ حلّ التناقض الذي أشرتَ إليه (watermarking: أغسطس أم ديسمبر 2026؟)

**ليس تناقضاً — هما موعدان لالتزامين مختلفين:**

| الالتزام | الموعد | لمن |
|---|---|---|
| **المادة 50 كاملة** (الإفصاح عند التفاعل مع AI، وسم deepfakes ونصوص المصلحة العامة — 50(1)، 50(3)، 50(4)) | **2 أغسطس 2026** — ساري | كل الأنظمة، جديدة أو قائمة |
| **المادة 50(2)** — الوسم القابل للقراءة آلياً (machine-readable marking) | **2 ديسمبر 2026** | **فقط** الأنظمة المطروحة في السوق **قبل** 2 أغسطس 2026 (فترة سماح 4 أشهر) |
| **المادة 50(2)** لأنظمة طُرحت **في/بعد** 2 أغسطس 2026 | **من يوم الطرح** — بلا سماح | الأنظمة الجديدة |

المصدر الحاسم: [المفوضية الأوروبية — أسئلة المادة 50](https://digital-strategy.ec.europa.eu/en/faqs/transparency-obligations-under-article-50-ai-act)
تنص صراحةً على أن فترة السماح محدودة بالأنظمة المطروحة قبل 2 أغسطس 2026
وبخصوص التزام الوسم فقط [1](https://digital-strategy.ec.europa.eu/en/faqs/transparency-obligations-under-article-50-ai-act).
وتأكيد إضافي: [4](https://www.paulweiss.com/insights/client-memos/eu-finalises-transparency-rules-for-ai-generated-content)،
[6](https://usercentrics.com/knowledge-hub/eu-ai-act-high-risk-delay-article-50-transparency-consent/).

**عقوبة عدم الامتثال للمادة 50:** حتى **15 مليون يورو أو 3% من حجم الأعمال
العالمي**، أيّهما أعلى [15](https://www.cooley.com/news/insight/2026/07/eu-ai-act-transparency-obligations-take-effect-2-august-2026).

**إضافات لم تكن في ملخصك:**
- حظران جديدان (توليد صور حميمة غير توافقية | مواد CSAM): **يسريان من 2 ديسمبر 2026**
  [10](https://www.paninnovation.com/en/blog/eu-ai-act-2026-what-changed/)
  (وليسا بلا موعد) [12](https://searchsynth.io/insights/eu-ai-act-digital-omnibus).
- **2 فبراير 2027:** موعد interoperability لاكتشاف العلامات المائية
  [3](https://www.faegredrinker.com/en/insights/publications/2026/7/eu-ai-act-commission-confirms-transparency-code-of-practice-as-adequate-and-publishes-final-version-of-its-guidelines-on-transparency-obligations).
- **2 أغسطس 2027:** بيئة تجريبية تنظيمية وطنية واحدة على الأقل (كانت 2 أغسطس 2026)
  [10](https://sakaradigital.com/blog/eu-ai-act-high-risk-deferral-december-2027-pharma/).
- المادة 4 (AI literacy) تحوّلت إلى «التزام بذل جهد» (obligation of effort)
  [19](https://spicyadvisory.com/blog/digital-omnibus-ai-act-2026-what-changed).
- المفوضية نشرت الإرشادات النهائية للمادة 50 في **20 يوليو 2026**
  [15](https://www.cooley.com/news/insight/2026/07/eu-ai-act-transparency-obligations-take-effect-2-august-2026).

---

## 2. مصر — قانون حماية البيانات الشخصية 151/2020

| المتطلب | التفصيل | المصدر |
|---|---|---|
| اللائحة التنفيذية | قرار 816/2025، 1 نوفمبر 2025، سارية 2 نوفمبر 2025 | [5](https://shalakany.com/wp-content/uploads/2025/12/New-Executive-Regulations-for-Egypts-Personal-Data-Protection-Law-.pdf) |
| فترة السماح / الإنفاذ | 31 أكتوبر 2026 (أو 1 نوفمبر 2026 حسب الحساب) | [1](https://www.bakermckenzie.com/en/insight/publications/2026/10/egypt-personal-data-protection-law-update)، [11](https://www.recordinglaw.com/world-laws/world-data-privacy-laws/egypt-data-privacy-laws/) |
| ترخيص/تصريح PDPC | مطلوب قبل المعالجة؛ ترخيص 3 سنوات، تصريح حتى سنة، رسوم حسب حجم السجلات | [19](https://www.mondaq.com/compliance/1845112/from-compliance-to-capital-how-egypts-pdpl-shapes-data-valuation) |
| أنواع التصاريح | تسويق إلكتروني مباشر · بيانات حساسة · نقل خارج مصر | كما ورد + [18](https://www.shandpartners.com/insights/briefings/telecoms-media-technology/the-issuance-of-the-executive-regulations-of-the-data-protection-law-and-the-establishment-of-the-data-protection-centre/) |
| **الإبلاغ عن الاختراق** | **72 ساعة** للـ PDPC (فوري إن مسّ الأمن القومي) | [1](https://www.kennedyslaw.com/en/thought-leadership/article/2026/egypt-s-personal-data-protection-law-the-compliance-countdown-has-begun/) |
| **إخطار الأفراد** | **3 أيام عمل من تاريخ إبلاغ PDPC** (لا من تاريخ اكتشاف الاختراق) | [2](https://www.recordinglaw.com/world-laws/world-data-privacy-laws/egypt-data-privacy-laws/) — تصحيح دقيق |
| سجل إلكتروني آمن | لتوثيق الاختراق: وقت العلم/الإبلاغ، الطبيعة، السجلات المتأثرة، الضرر، التدابير | [17 PDF — المادة 5](https://www.thesedonaconference.org/sites/default/files/meeting_paper/7.4%20Egypt%20Personal%20Data%20Protection%20Law%20-%20Executive%20Regulations.pdf) |
| **النقل خارج مصر** | محظور إلا بترخيص PDPC + مستوى حماية كافٍ + موافقة صريحة + تقييم أثر | [14](https://2713ae74.delivery.rocketcdn.me/wp-content/uploads/2026/04/CA_Egyptian-Personal-Data-Protection-Law.pdf)، [12](https://www.legal500.com/guides/chapter/egypt-data-protection-cybersecurity/) |
| مسؤول حماية بيانات (DPO) | إلزامي مسجَّل داخل كل متحكّم/معالج اعتباري | [9](https://research.lawlab.africa/egypt/) |
| النطاق خارج الحدود | يسري على من يعالج بيانات أشخاص داخل مصر **ولو من خارجها** | [1](https://www.kennedyslaw.com/en/thought-leadership/article/2026/egypt-s-personal-data-protection-law-the-compliance-countdown-has-begun/) |
| عقوبات مالية | غرامات إدارية حتى 3 ملايين جنيه لمخالفات الإبلاغ | [8](https://privacypulse.co/egypt-data-protection-law-compliance-guide-for-executive-decree-816/) |

> 💡 **نقطة حرجة على هذا المشروع بالتحديد:** النظام يستخدم **GitHub Actions
> (خوادم خارج مصر)** ومزوّدي **Gemini / Groq (خارج مصر)**. أي بيانات شخصية تدخل
> في `task` أو تُستخرج من المستندات ثم تُرسل لهؤلاء المزوّدين = **نقل عبر الحدود**
> بمفهوم المادة 14، ويحتاج ترخيصاً. راجع القسم 5.

---

## 3. أطر أخرى — بعد التحقق

### EU Cyber Resilience Act (Reg. (EU) 2024/2847) — ✅ تحقّق

| العنصر | القيمة |
|---|---|
| دخول حيز التنفيذ | 10 ديسمبر 2024 |
| **التزامات الإبلاغ (المادة 14)** | **11 سبتمبر 2026 — سارية بالفعل** (أنت قلت «~سبتمبر 2026» ✅) |
| التطبيق الكامل | **11 ديسمبر 2027** (أنت قلت «~ديسمبر 2027» ✅) |
| ساعات الإبلاغ | إنذار مبكر **24 ساعة** → إشعار **72 ساعة** → تقرير نهائي **14 يوماً** (ثغرة) أو شهر (حادث) |

المصدر الرسمي: [المفوضية — CRA](https://digital-strategy.ec.europa.eu/en/policies/cyber-resilience-act)
و[صفحة الإبلاغ](https://digital-strategy.ec.europa.eu/en/policies/cra-reporting).

⚠️ **فخّ المنتجات القديمة:** الإبلاغ يسري على **كل** المنتجات الرقمية المتاحة في
السوق، **بما فيها المطروحة قبل 11 ديسمبر 2027** — أي لا يمكن تأجيله
[4](https://www.kirkland.com/publications/kirkland-alert/2026/09/the-eu-cyber-resilience-act)،
[9](https://developer.espressif.com/blog/2026/09/esp32-cra-obligations-and-deadlines/).
✅ **لكن:** القائمون على البرمجيات مفتوحة المصدر (open-source stewards) يخضعون
للالتزام (المادة 24(3)) **من 11 ديسمبر 2027** لا من سبتمبر 2026
[3](https://digital-strategy.ec.europa.eu/en/policies/cra-reporting) — وهذا يخفّف
الأثر الفوري على هذا المشروع مفتوح المصدر.

### NIS2 (التوجيه (EU) 2022/2555) — ✅ تحقّق مع تصحيح

- موعد النقل الوطني: **18 أكتوبر 2024**، لكن **19 دولة من 27 تأخّرت**؛
  المفوضية أرسلت آراءً معلّلة لـ 19 دولة، وأحالت **أيرلندا وإسبانيا وفرنسا وهولندا
  إلى محكمة العدل في 8 يوليو 2026** [7](https://www.easy8.com/blog/nis2-dora)،
  [3](https://riskpublishing.com/dora-vs-nis2-how-eu-cyber-resilience-regulation/).
- حتى Q1 2026: **14 من 27** دولة فقط نفّذت بالكامل؛ وفي تحقق 4 أغسطس 2026
  إسبانيا **لم** تنفّذ بعد [9](https://nis-solutions.eu/countries/spain/).
- المغزى: قولك «مطبّق في دول EU» صحيح **جزئياً** — التطبيق متفاوت حسب الدولة،
  وقد اقترحت المفوضية تعديلات عليه في 20 ياناير 2026.
- إبلاغ الحوادث: إنذار مبكر **24 ساعة** → إشعار **72 ساعة** → نهائي **شهر**
  [1](https://www.nis-2-templates.com/vs-dora/).

### DORA (Reg. (EU) 2022/2554) — ✅ تحقّق مع إضافة

- ساري فعلاً منذ **17 يناير 2025** ✅ (كما ذكرت).
- **إضافة مهمة:** الإبلاغ الأولي خلال **4 ساعات** من تصنيف الحادث كجسيم
  (وبحد أقصى 24 ساعة من العلم) — أشدّ من NIS2 — ثم 72 ساعة ثم شهر
  [1](https://www.nis-2-templates.com/vs-dora/)،
  [8](https://www.cloudsoul.net/resources/blog/nis2-financial-sector-dora-relationship-banking/).
- للكيانات المالية و21 فئة؛ **لا ينطبق مباشرة** على هذا المشروع حالياً.

### GDPR

- سارٍ، ويمتد لأي نظام يخدم مقيمين في EU ✅. لا يزال الإطار المرجعي العام
  لتقليل البيانات، والموافقة، والاحتفاظ، والنقل.

---

## 4. تصنيف النظام (Risk Tier)

| السؤال | الإجابة على `new-cela` |
|---|---|
| هل هو **GPAI provider**؟ | ❌ لا — يستهلك نماذج Gemini/Groq، لا يدرّب ولا يوفّر نموذجاً عاماً |
| هل هو **provider** لنظام توليدي؟ | ⚠️ محتمل — يولّد نصوصاً عبر الوكيل؛ التزام المادة 50(2) قد يقع على المزوّد الأساس (Google/Groq) |
| هل هو **deployer**؟ | ✅ نعم بالتأكيد — التزام **المادة 50(1)**: إفصاح واضح عند التفاعل مع AI |
| هل يدخل **Annex III** (توظيف/تعليم/ائتمان/إنفاذ قانون)؟ | ❌ حالياً لا — وكيل أتمتة عام. **لكن** لو استُخدم يوماً في التوظيف أو تقييم الائتمان ⇒ يدخل Annex III ⇒ **2 ديسمبر 2027** |
| هل هو منتج رقمي تحت CRA؟ | ⚠️ مفتوح المصدر ⇒ استثناء جزئي حتى 11 ديسمبر 2027؛ يحتاج تقييماً قانونياً |
| هل يعالج بيانات شخصية داخل مصر؟ | ⚠️ **محتمل بقوة** — انظر القسم 5 |

---

## 5. Checklist — الانعكاس العملي على نظامك

| # | المطلب | الحالة | الدليل / الإجراء |
|---|---|---|---|
| 1 | تصنيف حسب risk tier | ✅ تم | القسم 4 أعلاه |
| 2 | إفصاح عند التفاعل مع AI (م 50(1)) | ⚠️ جزئي | يوجد `### 🤖 نتيجة الوكيل المستقل` في تعليق الـ Issue — **لكن لا يوجد إفصاح صريح للمستخدم النهائي في CLI/اللوحة** |
| 3 | وسم المحتوى المولّد (م 50(2)) | ❌ | يُنفَّذ — انظر `AI_DISCLOSURE.md` |
| 4 | سجل معالجة البيانات | ❌ | المستندات لا تتضمن سجل معالجة؛ يلزم إنشاؤه قبل 31 أكتوبر |
| 5 | سجل الموافقات | ❌ | لا يوجد `(consent)` صريح في الكود؛ `PolicyEngine` يسجّل موافقات **الأدوات** لا موافقة **صاحب البيانات** — فرق جوهري |
| 6 | سياسة احتفاظ | ⚠️ جزئي | `retention-days: 7` للـ artifacts + `max_entries=500` للتدقيق، لكن بلا سياسة معلنة وموثّقة |
| 7 | خطة استجابة للحوادث (72 ساعة) | ✅ أُضيفت | [`incident-response.md`](incident-response.md) |
| 8 | منع تسريب بيانات شخصية | ✅ أُضيف | `scripts/pii_scan.py` + خطوة في CI |
| 9 | مراجعة النقل عبر الحدود | ❌ | **أولوية قصوى** — GitHub Actions + Gemini/Groq خارج مصر. يلزم قرار: ترخيص PDPC أو استبعاد البيانات الشخصية من المهام |
| 10 | SBOM | ⚠️ | `pip-audit` + `dependency_guard` يُغطّيان جزئياً؛ لا SBOM مستقل بعد |
| 11 | إدارة الثغرات | ✅ | `pip-audit` **صارم** على النواة (0 متأثرة) + informational للمتصفح الاختياري + `secret_scan` + `pii_scan` |
| 12 | DPO / مسؤول حماية بيانات | ❌ | يلزم تعيينه (قد يكون المالك نفسه في المشاريع الصغيرة) |
| 13 | تدوير المفاتيح | ⚠️ | مفاتيح في GitHub Secrets ✅، بلا سياسة تدوير موثّقة |

---

## 6. خلاصة الأولويات القانونية

| الأولوية | الإجراء | الموعد |
|---|---|---|
| 🔴 1 | **قرار النقل عبر الحدود** — إما ترخيص PDPC أو ضمان خلوّ المهام من البيانات الشخصية | **خلال 8 أيام** (31 أكتوبر 2026) |
| 🔴 2 | إفصاح AI صريح + وسم المحتوى المولّد (المادة 50) | **ساري الآن**، و2 ديسمبر 2026 للوسم |
| 🟠 3 | سجل معالجة + موافقات + سياسة احتفاظ + تعيين DPO | قبل 31 أكتوبر 2026 |
| 🟠 4 | SBOM + تدوير مفاتيح | قبل 11 ديسمبر 2027 (CRA) |
| 🟡 5 | مراقبة Annex III لو تغيّر استخدام النظام | 2 ديسمبر 2027 |

> البنود المعلّمة ❌/⚠️ مسجّلة هنا ولم يُنفَّذ منها بعد ما هو خارج نطاق هذه
> المهمة، التزاماً بقاعدة «لا دخول في مهمة قبل تأكيد سابقتها».
