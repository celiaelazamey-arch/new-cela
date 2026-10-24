# دستور التشغيل الهندسي والتجاري (18 قاعدة) والقاعدة الذهبية — تقييم وأدلة `new-cela`

> **القاعدة الأهم (الحاكمة لكل قرار في المشروع):**
> **«بسيط ← يعمل ← يُقاس ← يُباع ← ثم يتوسع»**
> *(Simple → Works → Measured → Sold → Scales)*
>
> الوحدة البرمجية المنفِّذة: [`src/core/playbook.py`](../src/core/playbook.py) · سجل الإصدارات ودراسة الحالة: [`CHANGELOG.md`](../CHANGELOG.md).

---

## 📊 مصفوفة القواعد الـ 18 وتطبيقها الفعلي في `new-cela` (18/18 مُنفَّذة بأدلة كود)

| # | القاعدة | ما تطلبه القاعدة | الدليل الفعلي في `new-cela` (`ملف:آلية`) | الحالة |
|---|---|---|---|---|
| **1** | **ركّز** | مشروع رئيسي واحد (`new-cela`) · نيش واحد · شريحة واحدة · مشكلة واحدة | التركيز الحصري على نيش **`print_prepress` (مطبعة الملابس السلك سكرين وأرشيف الأفلام)** في [`src/screen_print/engine.py`](../src/screen_print/engine.py) و[`src/core/niche_strategy.py`](../src/core/niche_strategy.py) | ✅ مُنفَّذ |
| **2** | **عميل يدفع قبل الميزات** | 3–10 مستخدمين يدفعون يدوياً قبل بناء بوابة الدفع · بيع المشكلة المحلولة لا التقنية | دالة `evaluate_golden_rule_stage` في [`src/core/playbook.py`](../src/core/playbook.py) تمنع الانتقال إلى `ready_for_payment_gateway=True` قبل وجود `>= 3` عملاء يدفعون يدوياً، وتمنع `SCALES` قبل `>= 10` عملاء | ✅ مُنفَّذ |
| **3** | **لا تثق بمخرج AI قبل تشغيله** | `Generate ← Run ← Test ← Review ← Commit` · لا تقبل «تم الاختبار» دون دليل | مفروض في [`CLAUDE.md`](../CLAUDE.md) (القسم 1) و[`scripts/spec_check.py`](../scripts/spec_check.py) (`ORIGINAL_REQUIREMENTS`) | ✅ مُنفَّذ |
| **4** | **CI من اليوم الأول** | lint + test + secret scan + dependency scan على كل PR · تثبيت Actions بـ SHA · `permissions: contents: read` | [`.github/workflows/quality-gate.yml`](../.github/workflows/quality-gate.yml) + [`.github/workflows/security-scan.yml`](../.github/workflows/security-scan.yml) + حارس [`scripts/action_pin_guard.py`](../scripts/action_pin_guard.py) (12/12 SHA مثبّتة) | ✅ مُنفَّذ |
| **5** | **الأسرار** | لا مفاتيح في الكود أو prompts أو logs · فحص محلي (`pre-commit`) + CI · تنقيح السجلات | [`scripts/secret_scan.py`](../scripts/secret_scan.py) + [`src/core/telemetry.py`](../src/core/telemetry.py) (`redact_secrets`) + [`src/core/prompt_guard.py`](../src/core/prompt_guard.py) + [`.pre-commit-config.yaml`](../.pre-commit-config.yaml) | ✅ مُنفَّذ |
| **6** | **صغّر الـ commits والتغييرات** | commit لكل فكرة · رفض أي تعديل خارج النطاق · **backups قبل العمليات الكبيرة** | دالة `create_verified_backup` في [`src/core/playbook.py`](../src/core/playbook.py) تنشئ نسخة احتياطية موثّقة ببصمة `SHA-256` قبل العمليات الكبيرة | ✅ مُنفَّذ |
| **7** | **صلاحيات أقل (Least Privilege)** | قراءة فقط افتراضياً · موافقة بشرية للعمليات المدمرة · kill switch + budget cap | `DEFAULT_SCOPES = {fs.read}` في [`src/core/policy.py`](../src/core/policy.py) + `AutonomyLevel.L1` و`KillSwitch` + `QuotaGuard` في [`src/core/quota.py`](../src/core/quota.py) (محروسة بـ `policy_guard.py`) | ✅ مُنفَّذ |
| **8** | **قس قبل أن تحسّن** | metrics + logs منظمة JSON · أول رقمين: نسبة نجاح المهام وتكلفة المهمة · evals ثابتة | [`src/core/telemetry.py`](../src/core/telemetry.py) + [`src/awareness/metrics.py`](../src/awareness/metrics.py) + [`src/awareness/calibration.py`](../src/awareness/calibration.py) + [`tests/test_cognitive_acceptance.py`](../tests/test_cognitive_acceptance.py) | ✅ مُنفَّذ |
| **9** | **تحكم في تكلفة AI** | model router (رخيص للبسيط / قوي للصعب) · حدود لكل خطة · لا «غير محدود» · حساب هامش كل مستخدم | `recommend_model_tier` و`build_plan_guard` و`calculate_unit_economics` في [`src/core/pricing.py`](../src/core/pricing.py) | ✅ مُنفَّذ |
| **10** | **تفادي الاعتماد على مزود واحد** | fallback بين نماذج ومزودين · طبقة abstraction للـ LLM · بيانات قابلة للتصدير دائماً | `LLMRouter` (تبديل تلقائي Gemini ↔ Groq) في [`src/providers/llm_router.py`](../src/providers/llm_router.py) + بروتوكول `ModelRouter` في [`src/core/model_api.py`](../src/core/model_api.py) + تخزين وتصدير `JSONL`/`JSON` | ✅ مُنفَّذ |
| **11** | **الخصوصية والامتثال** | الحد الأدنى من البيانات · قانون حماية البيانات المصري 151/2020 (إنفاذ **31 أكتوبر 2026**) + EU AI Act / GDPR | [`docs/compliance-framework.md`](compliance-framework.md) + [`AI_DISCLOSURE.md`](../AI_DISCLOSURE.md) + فاحص البيانات الشخصية المصرية [`scripts/pii_scan.py`](../scripts/pii_scan.py) | ✅ مُنفَّذ |
| **12** | **اعتماديات وSupply Chain** | التحقق من أي حزمة قبل `install` · تحديث آلي (Dependabot) · أقل عدد ممكن من التبعيات | [`scripts/dependency_guard.py`](../scripts/dependency_guard.py) + [`.github/dependabot.yml`](../.github/dependabot.yml) + عزل `browser-use` وتوليد `CycloneDX SBOM` | ✅ مُنفَّذ |
| **13** | **وثّق القرارات** | ملف spec + ملف قواعد يقرأه الـ agent · مراجع ثابتة تمنع context drift | [`CLAUDE.md`](../CLAUDE.md) + [`AGENT_HANDOFF.md`](../AGENT_HANDOFF.md) + حارس انحراف المنطق [`scripts/spec_check.py`](../scripts/spec_check.py) | ✅ مُنفَّذ |
| **14** | **العربية ميزة** | RTL · UTF-8 صريح · لهجات · سياق محلي · دفع محلي (`Fawry` \| `InstaPay` \| `Paymob`) · تسعير بالجنيه | [`src/core/niche_strategy.py`](../src/core/niche_strategy.py) (`LocalPaymentMethod`) + [`src/core/pricing.py`](../src/core/pricing.py) (`monthly_price_egp`) + واجهة وتقارير عربية بالكامل | ✅ مُنفَّذ |
| **15** | **بنية مجانية بذكاء** | public repos (CI غير محدود) · `concurrency` + `paths` · تخزين JSONL محلي · لا ترقية مدفوعة قبل إثبات القيمة | `concurrency` و`paths-ignore` في `quality-gate.yml` + `allow_paid=False` في `quota.py` + تخزين JSONL محلي في `memory.py` و`screen_print/engine.py` | ✅ مُنفَّذ |
| **16** | **اختبار الفشل** | اختبر سقوط الشبكة/المزود/الملفات · timeouts + retries + circuit breakers · **backup قابل للاستعادة (جرّب الاستعادة فعلياً)** | [`tests/test_chaos_awareness.py`](../tests/test_chaos_awareness.py) (29 اختبار فوضى) + `CircuitBreaker` في `llm_router.py` + **`restore_and_verify_backup`** في [`src/core/playbook.py`](../src/core/playbook.py) | ✅ مُنفَّذ |
| **17** | **ابنِ علناً** | README قوي + demo قصير + `CHANGELOG.md` + دراسة حالة من أول عميل | [`README.md`](../README.md) + [`CHANGELOG.md`](../CHANGELOG.md) (يتضمن دراسة حالة العميل رقم #0: مطبعة السلك سكرين) + أوامر `--plans` و`--niches` | ✅ مُنفَّذ |
| **18** | **الصحة والاستمرارية** | مهمة رئيسية واحدة يومياً · أسبوع مراجعة كل 4 أسابيع · المشروع الذي لا يتقدم 30 يوماً ← أوقفه أو جمّده | `STALENESS_DAYS = 30` في [`src/awareness/self_model.py`](../src/awareness/self_model.py) + دالة `evaluate_30_day_activity_gate` في [`src/core/playbook.py`](../src/core/playbook.py) | ✅ مُنفَّذ |

---

## 🔄 القاعدة الأهم في التطبيق العملي: «بسيط ← يعمل ← يُقاس ← يُباع ← ثم يتوسع»

تفرض دالة `evaluate_golden_rule_stage` في [`src/core/playbook.py`](../src/core/playbook.py) عدم تخطّي أي مرحلة من المراحل الخمس:

1. **`SIMPLE` (بسيط):** كود بسيط يركّز على مشكلة واحدة في نيش واحد (`print_prepress`: مطبعة السلك سكرين وأرشيف الأفلام).
2. **`WORKS` (يعمل):** كل ميزة تعمل فعلياً وتجتاز حواجز الأمان الـ 7 و500+ اختبار `pytest` على `GitHub Actions`.
3. **`MEASURED` (يُقاس):** قياس أول رقمين في كل مهمة: **نسبة نجاح المهام (`task_success_rate`)** و**تكلفة المهمة (`cost_per_task`)** عبر تقارير JSON المنظمة ومعايرة `Brier/ECE`.
4. **`SOLD` (يُباع يدوياً):** بيع المشكلة المحلولة لـ **3 إلى 10 مطابع ملابس يدفعون يدوياً** (عبر `InstaPay` أو `Fawry` أو تحويل مباشر) مع خدمة إعداد مُدارة، قبل بناء أي بوابة دفع آلية معقدة.
5. **`SCALES` (ثم يتوسع):** بعد وصول عدد العملاء الدافعين إلى **10 عملاء** بنسبة نجاح **>= 85–90%** و**هامش ربح موجب لكل عميل**، يُفتح باب التوسع وأتمتة الدفع.
