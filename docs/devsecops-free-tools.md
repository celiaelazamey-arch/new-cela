# أدوات الجودة والأمان والاختبار والنشر المجانية (14 فئة) — تحقق 2026 وتقييم `new-cela`

> **مرجع دائم داخل النظام.** تاريخ التحقق الفعلي: 2026-10-24 (Africa/Cairo).
> يستجيب لطلب المستخدم: *«تحقق من حدود الطبقة المجانية الحالية لكل أداة»* عبر **14 فئة**،
> ويفصل بين ما يزال مجانياً فعلاً في أكتوبر 2026 وما أُلغيت أو قُلِّصت طبقته المجانية،
> ويربط كل فئة بما هو مُنفَّذ فعلياً في `new-cela`.

---

## 🚨 0. أهم 7 تصحيحات ومفاجآت كشفها التحقق الفعلي لعام 2026

قبل إضافة أي أداة من القوائم المتداولة في المعرفة العامة، كشف التحقق من صفحات التسعير الرسمية في 2026 تغييرات جوهرية تحميك من الاعتماد على أداة لم تعد مجانية أو تخالف الاستخدام التجاري:

| الأداة | ما تقوله المعرفة العامة القديمة | الحقيقة المُتحقَّقة في أكتوبر 2026 | المصدر |
|---|---|---|---|
| **1. Fly.io** | «طبقة مجانية بـ 3 آلات صغيرة (256MB)» | ❌ **أُلغيت الطبقة المجانية تماماً للحسابات الجديدة** منذ 7 أكتوبر 2024 (مجرد تجربة 7 أيام أو ساعتين تشغيل ثم فوترة فورية ببطاقة ائتمان، ورُفع سعر الذاكرة في **1 أكتوبر 2026** إلى $6/GB شهرياً). | [Fly Docs](https://fly.io/docs/about/cost-management/) · [AgentDeals 2026](https://agentdeals.dev/hosting-free-tier-comparison-2026) |
| **2. Railway** | «$5 رصيد مجاني شهري» | ❌ **لا توجد طبقة مجانية تُبقي خدمة شغّالة طوال الشهر**؛ يمنح $5 لمرة واحدة (30 يوماً) ثم **$1/شهر فقط** (لا يكفي لتشغيل مستمر)، وخطة Hobby تبدأ من $5/شهر. | [SnapDeploy 2026](https://snapdeploy.dev/state-of-free-hosting) · [SaasPricePulse](https://www.saaspricepulse.com/blog/railway-pricing-history) |
| **3. Render** | «100 GB باندويدث مجاني + قاعدة بيانات 90 يوماً» | ⚠️ **خُفِّض الباندويدث المجاني في 23 أبريل 2026 من 100 GB إلى 5 GB/شهر فقط!** والخدمة المجانية تنام بعد 15 دقيقة خمول (~دقيقة للاستيقاظ)، وقاعدة Postgres المجانية تنتهي وتُحذف بعد **30 يوماً**. | [AgentDeals 2026](https://agentdeals.dev/hosting-free-tier-comparison-2026) · [Render Articles](https://render.com/articles/platforms-with-a-real-free-tier-for-developers-in-2026) |
| **4. Vercel (Hobby)** | «مجاني بـ 100 GB باندويدث لأي موقع» | ⚠️ **يحظر الاستخدام التجاري صراحةً!** حتى صفحة هبوط لمنتج مدفوع أو إعلان يُعدّ استخداماً تجارياً ويستلزم خطة Pro بـ **$20/مقعد/شهر**. | [PromptStoProduct 2026](https://www.promptstoproduct.com/vercel-free-tier-limits) · [AgentDeals](https://agentdeals.dev/hosting-free-tier-comparison-2026) |
| **5. Netlify (Free)** | «100 GB باندويدث و300 دقيقة بناء» | ⚠️ تحوّل إلى **نظام نقاط (300 credits/شهر)**: كل 1 GB يستهلك 20 نقطة وكل نشر إنتاجي 15 نقطة ⇐ **الباندويدث الفعلي ~15 GB فقط** ويتوقف الموقع عند نفاد النقاط. (لكنه يسمح بالاستخدام التجاري). | [GuptaDeepak 2026](https://guptadeepak.com/tools/top-5-static-site-hosting-jamstack-platforms-2026/) · [Tech-Insider](https://tech-insider.org/vercel-vs-netlify-vs-cloudflare-pages-2026/) |
| **6. CodeClimate** | «مجاني للمشاريع مفتوحة المصدر» | ❌ **أوقف الحسابات المجانية للمشاريع مفتوحة المصدر** وتحوّل إلى **Qlty Software**. البديل المجاني الأقوى للمستودعات العامة هو **SonarQube Cloud (SonarCloud سابقاً)** أو **CodeQL**. | [GitHub Issue #3670](https://github.com/adobe-Consulting-Services/acs-aem-commons/issues/3670) |
| **7. Cloudflare Pages** | «استضافة ملفات ثابتة» | ✅ **الفائز الحقيقي في 2026:** باندويدث ثابت **غير محدود مجاناً**، 500 بناء/شهر، 100,000 طلب يومياً على Workers بلا Cold Start، **ويسمح بالاستخدام التجاري مجاناً بلا بطاقة!** | [SnapDeploy 2026](https://snapdeploy.dev/blog/free-cloud-deployment-platforms-2026-comparison) |

---

## 📊 التقييم الشامل للفئات الـ 14 على `new-cela` وحدودها في 2026

### 1. جودة الكود وتحليله (Code Quality & SAST)

| الأداة | حد الطبقة المجانية في 2026 | الحالة في `new-cela` | القرار الهندسي |
|---|---|---|---|
| **SonarCloud (SonarQube Cloud)** | ✅ **غير محدود** للمستودعات العامة (Public/OSS) · وللخاص: حتى **50,000 سطر كود و5 مستخدمين** مع تحليل PR بلا بطاقة [SonarSource](https://www.sonarsource.com/products/sonarqube/cloud/signup-free/) | 🟡 يغطيه حالياً **CodeQL + ruff + 7 حواجز محلية** | متاح للإضافة الاختيارية؛ CodeQL المدمج في `.github/workflows/security-scan.yml` يغني عنه حالياً بلا حساب خارجي |
| **CodeClimate** | ❌ لم يعد يقدّم حسابات مجانية (تحوّل لـ Qlty) | ➖ مستبعد | لا تستخدمه |
| **Codacy / DeepSource** | مجاني للمستودعات العامة؛ محدود للمقاعد الخاصة | ➖ غير مطلوب | يكرر عمل `ruff` + `CodeQL` |
| **Bandit (Python)** | ✅ مفتوح المصدر ومجاني 100% (PyCQA) | ✅ يغطيه `CodeQL (python)` + `policy_guard.py` (فحص `shell=True` و`create_subprocess_shell`) | مغطّى بالكامل |
| **Brakeman / gosec / cargo-audit / cargo-deny** | مجانية (مفتوحة المصدر) | ➖ لا تنطبق | مخصّصة لـ Ruby / Go / Rust، بينما مشروعنا **Python 3.11** (ومقابله لدينا هو **`pip-audit`** الصارم في CI) |

---

### 2. Coverage وتقارير الاختبار (Test Coverage & Reporting)

| الأداة | حد الطبقة المجانية في 2026 | الحالة في `new-cela` | القرار الهندسي |
|---|---|---|---|
| **pytest-cov (محلي في CI)** | ✅ مجاني 100% بلا حدود ولا طرف ثالث | ✅ **مُنفَّذ في `quality-gate.yml`** | يفرض `--cov-fail-under=60` (التغطية الفعلية **86.6%** عبر **492 اختباراً**) دون تسريب الكود لتطبيق خارجي |
| **Codecov / Coveralls** | ✅ مجاني للمستودعات العامة · مدفوع للخاص ($10/مستخدم/شهر في Codecov بعد 5 مستخدمين) [Slashdot](https://slashdot.org/software/p/SonarQube-Cloud/alternatives) | 🟡 اختياري | لا حاجة لمنح OAuth token خارجي طالما بوابة `pytest-cov` تمنع تراجع التغطية في كل PR |
| **Allure / JUnit reports** | ✅ مفتوحة المصدر ومجانية | 🟡 اختياري | مفيد فقط عند تضخم الاختبارات فوق 2,000 اختبار |

---

### 3 و4. اختبارات E2E والواجهات وAPI والأداء (E2E, UI, API & Load Testing)

| الأداة | حد الطبقة المجانية في 2026 | الحالة في `new-cela` | القرار الهندسي |
|---|---|---|---|
| **Playwright** | ✅ مفتوح المصدر ومجاني 100% | ✅ معلن في `requirements-browser.txt` (اختياري) | معزول خارج النواة لتجنب ثغرات `aiohttp` وتوفير وقت CI (البند 14) |
| **Lighthouse CI / axe-core** | ✅ مجاني ومفتوح المصدر | ⏳ عند إطلاق صفحة الهبوط | يُضاف عند بناء واجهة الويب التسويقية |
| **Percy / Chromatic** | طبقة مجانية محدودة (5,000 لقطة/شهر في Percy/Chromatic) | ➖ غير مطلوب حالياً | الواجهة الحالية Streamlit للقراءة فقط (`tests/test_dashboard.py` يختبر طبقة البيانات بـ 98% تغطية) |
| **Schemathesis / Hurl / Locust / k6** | ✅ مفتوحة المصدر؛ و**Grafana Cloud** يمنح **100,000 تنفيذ API و10,000 تنفيذ متصفح شهرياً مجاناً** عبر k6 [CloudMonitoring 2026](https://cloudmonitoringplatforms.com/best-synthetic-monitoring-tools) | ⏳ عند فتح `Verified Agent API` (المرحلة 3 من خطة الربح) | نستخدم `Schemathesis` + `Locust`/`k6` عند إطلاق واجهة الـ API المدفوعة |

---

### 5 و6. أمان إضافي وإدارة التبعيات (AppSec & Supply Chain)

| الأداة | حد الطبقة المجانية في 2026 | الحالة في `new-cela` | القرار الهندسي |
|---|---|---|---|
| **Dependabot** | ✅ مجاني 100% ومدمج في GitHub | ✅ **أُضيف الآن في `.github/dependabot.yml`** | يفحص `pip` و`github-actions` أسبوعياً بسقف 5 PRs (الأولوية #1 في البند 13) |
| **Renovate** | ✅ مجاني ومفتوح المصدر | 🟡 بديل مستقبلي | `Dependabot` المدمج يكفي حالياً بلا تطبيق خارجي |
| **pip-audit + CycloneDX SBOM** | ✅ مجاني 100% بلا سقف | ✅ **مُنفَّذ في `quality-gate.yml`** | يفحص النواة بصرامة (0 ثغرات) ويولّد `sbom-cyclonedx.json` في كل بناء |
| **Snyk (Free)** | ⚠️ سقف شهري محدود: **200 فحص SCA + 100 فحص SAST + 100 حاويات + 300 IaC شهرياً** [G2 2026](https://www.g2.com/products/snyk/pricing) · [ItsFree](https://itsfree.dev/) | ➖ لا حاجة له | `CodeQL` + `pip-audit` + `dependency-review-action` لدينا تعطينا نفس الحماية **بلا حد شهري!** |
| **detect-secrets / git-secrets / Gitleaks** | ✅ مفتوحة المصدر | ✅ **مُنفَّذ عبر `scripts/secret_scan.py` و`scripts/pii_scan.py`** | مخصّصان لأنماط مفاتيح Gemini/Groq/GitHub والبيانات الشخصية المصرية (قانون 151/2020) |
| **Hadolint / Trivy / Grype / Checkov** | ✅ مفتوحة المصدر | ➖ مؤجلة | لا نستخدم Docker أو Terraform حالياً؛ تُضاف عند بناء صورة `Enterprise On-Prem` |
| **OpenSSF Scorecard / Cosign** | ✅ مجانية للمستودعات العامة | ⏳ المرحلة القادمة | हमने أنجزنا أهم متطلبات Scorecard بالفعل: **تثبيت كل الـ Actions بـ SHA (`action_pin_guard.py`)** + **`permissions: contents: read`** + **CodeQL** + **Dependabot** |

---

### 7، 8، 10. جودة الرسائل، التوثيق، والتنسيق (Governance & CODEOWNERS)

| الأداة | حد الطبقة المجانية في 2026 | الحالة في `new-cela` | القرار الهندسي |
|---|---|---|---|
| **pre-commit framework** | ✅ مجاني 100% | ✅ **مُنفَّذ في `.pre-commit-config.yaml`** | يشغّل 7 حواجز محلية مطابقة تماماً لـ CI (البند 7 و13) |
| **CODEOWNERS** | ✅ مدمج مجاناً في GitHub | ✅ **أُضيف الآن في `.github/CODEOWNERS`** | يحمي المسارات الحساسة والخطوط الحمراء (`policy.py`, `quota.py`, `path_guard.py`, `prompt_guard.py`, `sandbox.py`, `.github/`, `CLAUDE.md`) |
| **markdownlint / lychee / typos** | ✅ مجانية ومفتوحة المصدر | 🟡 اختياري | فاحص `spec_check.py` يتحقق بالفعل من وجود وسلامة المراجع العشرة الإلزامية |

---

### 9. النشر والبنية التحتية (Hosting & Deployment — توصية 2026 المُتحقَّقة)

بناءً على التحقق الفعلي في القسم 0 أعلاه، هذه هي التوصية الحاسمة لـ `new-cela`:

1. **لصفحة الهبوط والتوثيق التجاري (`Waitlist` وجدول الأسعار):**
   - ✅ **استخدم Cloudflare Pages (مجاناً):** باندويدث غير محدود، يسمح بالاستخدام التجاري صراحةً، ولا يحتاج بطاقة ائتمان.
   - ❌ **تجنّب Vercel Hobby** للنشاط التجاري (مخالف لشروط الاستخدام)، وانتبه أن **Netlify Free** محدود بـ 300 نقطة (~15 GB).
2. **للوحة المتابعة (`src/dashboard/app.py` — Streamlit):**
   - ✅ **Streamlit Community Cloud** (مجاني للمستودعات العامة بنمط قراءة فقط) أو **تشغيل محلي** (`python -m src.dashboard`).
3. **لتشغيل الوكيل الخلفي (Backend / Worker):**
   - ✅ حالياً: **GitHub Actions** (دقائق غير محدودة للمستودع العام — التكلفة **0$**).
   - 💡 عند الانتقال لسيرفر خاص مدفوع مستقبلاً (بعد أول 10 عملاء يدفعون): VPS مباشر (مثل Hetzner بـ ~€4/شهر) أو **Google Cloud Run** (مليونا طلب مجاني شهرياً) أوفر وأضمن من **Fly.io** (الذي ألغى المجاني) ومن **Render** (الذي خفّض الباندويدث إلى 5 GB وينام كل 15 دقيقة).

---

### 11. مراقبة ما بعد النشر (Post-Deployment Observability — حدود 2026)

| المنصة | الطبقة المجانية في أكتوبر 2026 | متى نربطها بـ `new-cela`؟ |
|---|---|---|
| **التتبع المحلي الحالي (`src/core/telemetry.py` + `src/awareness/metrics.py`)** | ✅ **0$ للأبد** — يحفظ تقارير JSON ذرّية منقّحة من الأسرار ويحسب `MTTD/MTTR` ومعايرة `Brier/ECE` | **يعمل الآن** في كل مهمة دون إرسال بيانات لخادم خارجي (يتوافق مع قانون مصر 151/2020!) |
| **Sentry (Developer Free)** | ✅ **5,000 خطأ/شهر + 5M Spans + 50 Replays + 5 GB Logs** لمستخدم واحد (يسقط الزائد ولا يفوتر تلقائياً) [AgentDeals Sentry](https://agentdeals.dev/vendor/sentry) · [WhichDevTool](https://whichdevtool.com/tools/sentry/) | عند إطلاق واجهة SaaS العامة (مع تفعيل فلترة البيانات الشخصية قبل الإرسال) |
| **Grafana Cloud (Free)** | ✅ **10,000 سلسلة مقاييس + 50 GB Logs + 50 GB Traces** لـ 3 مستخدمين (احتفاظ 14 يوماً) [AgentDeals Grafana](https://agentdeals.dev/vendor/grafana-cloud) | الخيار الأول للـ Metrics والـ Tracing عند توسع الفريق |
| **UptimeRobot (Free)** | ✅ **50 مراقباً (Monitors) بفاصل 5 دقائق** + صفحة حالة عامة مجاناً بلا بطاقة [ItsFree](https://itsfree.dev/) | فور نشر صفحة الهبوط أو الـ API |

---

### 12. الأدوات الخاصة بـ AI وـ Agents — وما نُفِّذ الآن في `new-cela`

| متطلب البند 12 | الأدوات الخارجية | ما يملكه `new-cela` فعلياً الآن (0$ وبدون بطء في CI) |
|---|---|---|
| **1. فحص Prompt Injection على مدخلات الأدوات** | Lakera / LLM Guard | ✅ **نُفِّذ برمجياً الآن في [`src/core/prompt_guard.py`](../src/core/prompt_guard.py):** يفحص مدخلات ومخرجات الأدوات بزمن خطي `O(n)` (**صفر Regex** منعاً لثغرات CodeQL ReDoS) ويكتشف: (أ) عبارات إلغاء التعليمات بالعربية والإنجليزية (`ignore previous instructions` / `تجاهل التعليمات السابقة`)، (ب) محاولات سرقة الـ System Prompt ومفاتيح `.env`، (ج) محاولات تصعيد الصلاحيات (`shell=True` / `allow_paid=True`)، و(د) المحارف الخفية وBidi Overrides (`strip_invisible_chars`). |
| **2. Evals واختبار الـ Prompts والـ RAG** | promptfoo / DeepEval / Ragas | ✅ لدينا **اختبارات القبول المعرفية** (`tests/test_cognitive_acceptance.py`) + **معايرة الثقة التاريخية** (`src/awareness/calibration.py` — Brier Score & ECE) + **اختبارات استرجاع الذاكرة والمعرفة باقتباسات الأسطر** (`tests/test_memory.py` و`scripts/e2e_knowledge_quantum.py`). |
| **3. Red-Teaming واختبارات الحقن الهجومية** | Garak / PyRIT | ✅ لدينا **85 اختباراً هجومياً وفوضوياً مدمجاً في `pytest`:** `tests/test_path_guard.py` (46 اختبار اختراق مسارات وتزوير سجلات) + `tests/test_chaos_awareness.py` (29 اختبار فوضى وحقن أعطال) + `tests/test_prompt_guard.py` (اختبارات حقن التوجيه والتهريب الخفي). |
| **4. Gitleaks على ملفات Prompts وConfig** | Gitleaks | ✅ `scripts/secret_scan.py` + `scripts/pii_scan.py` يفحصان كل ملف متتبّع في المستودع عند كل Commit وPush. |
| **5. حد تكلفة API في CI (Budget Cap)** | — | ✅ `QuotaGuard` (`src/core/quota.py`) + `build_plan_guard` (`src/core/pricing.py`) + حارس الخط الأحمر `scripts/policy_guard.py` (`allow_paid=False` دائماً). |

---

### 13 و14. تطبيق قاعدة «أداة واحدة كل مرة + قياس زمن الـ Pipeline»

التزاماً بالبندين 13 و14 (**«أضف أداة واحدة كل مرة ← قِس زمن الـ pipeline ← أبقِ الفحوصات الثقيلة خارج المسار الحرج»**):

1. **ما أُضيف في هذه الدفعة (أثره على زمن الـ Pipeline = 0 ثانية إضافية!):**
   - **`.github/dependabot.yml`** (الأولوية #1 في البند 13): يعمل على خوادم GitHub أسبوعياً خارج `Quality Gate`.
   - **`.github/CODEOWNERS`** (البند 10): يُفرض من GitHub عند مراجعة الـ PR بلا أي وقت حوسبة.
   - **`src/core/prompt_guard.py` + `tests/test_prompt_guard.py`** (البند 12): يعمل بزمن `< 5ms` داخل `pytest`.
2. **زمن الـ Pipeline الحالي المُقاس (`Quality Gate`):**
   - إجمالي الزمن على GitHub Actions: **~1 دقيقة و10 ثوانٍ** فقط (شاملاً تثبيت الحزم، 7 حواجز أمان ومواصفة، `ruff`، تدقيق `pip-audit` الصارم، **492 اختبار `pytest` بتغطية 86.6%**، وتوليد `CycloneDX SBOM`).
3. **الفحوص الثقيلة المعزولة عمداً:**
   - `CodeQL (SAST)` مفصول في سير عمل مستقل (`.github/workflows/security-scan.yml`) يعمل بالتوازي أو أسبوعياً (`cron`).
   - أتمتة المتصفح (`Playwright` / `browser-use`) معزولة في `requirements-browser.txt` ولا تُثبَّت إلا عند طلب نمط `browser` صراحةً.
