# منصات CI/CD المجانية — مقارنة مُتحقَّقة وتوصية لـ `new-cela`

> **مرجع دائم.** تاريخ التحقق: 2026-10-23 (America/Los_Angeles).
> الدفعة الخامسة من قائمة المستخدم. الهدف: «الموارد المجانية فقط في البداية».
> ⚠️ الحدود تتغيّر — راجع المصادر الرسمية قبل أي قرار مالي.

---

## 🎯 الخلاصة أولاً (حسب وضعك)

| السؤال | الإجابة المُتحقَّقة |
|---|---|
| أين الكود؟ | GitHub — مستودع **public** (`private:false`) |
| كم الدقائق المتاحة؟ | **غير محدودة** للمستودعات العامة على runners القياسية |
| هل رسوم self-hosted سارية؟ | ❌ **لا — أُجّلت لأجل غير مسمى** |
| التوصية | ✅ **GitHub Actions** كما هو، مع تحسينات الأمان أدناه |
| هل نحتاج منصة بديلة؟ | ❌ لا حالياً. CircleCI/Woodpecker كخطة طوارئ فقط لو تحوّل المستودع لـ private |

> 🔑 **اكتشاف استراتيجي:** CodeQL **مجاني للمستودعات العامة** ويدعم
> Python + GitHub Actions workflows. هذا يوفّر طبقة SAST مستقلة عن
> قيد `403` الذي تواجهه واجهة Security في هذه الجلسة.

---

## 1. حسم التعارض: رسوم self-hosted runners

كانت المصادر تتعارض: مصدر يقول «$0.002/دقيقة منذ 1 مارس 2026» وآخر
يقول «أُجّلت لأجل غير مسمى». **التحقق من المصدر الرسمي حسمها**:

| التاريخ | الحدث |
|---|---|
| 16 ديسمبر 2025 | GitHub أعلن رسم $0.002/دقيقة على self-hosted + خفض hosted حتى 39% |
| خلال 48 ساعة | **تراجع GitHub** بعد اعتراض المجتمع: «نُرجّئ تغيير التسعير... لإعادة التقييم» |
| 1 يناير 2026 | خفض أسعار hosted runners **ساري** (Linux 2-core: $0.008 → $0.006) |
| 1 مارس 2026 | مرّ الموعد **بدون تفعيل** الرسم |
| حتى أكتوبر 2026 | **لا يزال مؤجّلاً** — docs.github.com يصف self-hosted بأنه مجاني |

**الحكم:** الرسم **مؤجَّل لا مُلغى** — قد يعود بصيغة معدّلة. لكنه **غير سارٍ الآن**،
والمستودعات العامة مستثناة على أي حال. [Changelog](https://github.blog/changelog/2025-12-16-coming-soon-simpler-pricing-and-a-better-experience-for-github-actions/) · [Billing docs](https://docs.github.com/en/actions/concepts/billing-and-usage)

---

## 2. جدول المقارنة (مُتحقَّق 2026-10-23)

| المنصة | الطبقة المجانية | ملاحظات محسومة |
|---|---|---|
| **GitHub Actions** ✅ | **غير محدود** لـ public · 2,000 دقيقة/شهر لـ private | 500 MB artifacts · 10 GB cache · 20 job متوازي (Free) · job واحد ≤ 6 ساعات |
| GitLab CI | 400 دقيقة compute/شهر · 5 مستخدمين | storage: **تعارض** بين 10 GiB عام و5 GB للمشروع. self-hosted مجاني بلا حدود |
| CircleCI | 30,000 credits/شهر · 5 مستخدمين | ⚠️ **تغيّرت في 3 سبتمبر 2026**؛ التحويل يختلف: ~6,000 دقيقة (small Docker) أو ~3,000 (medium). **لا macOS** على Free. Credits لا تترحّل |
| Woodpecker CI | مفتوح المصدر Apache 2.0 | مجاني بلا حد دقائق — تدفع السيرفر فقط. يحتاج استضافة (تخالف «بلا بنية تحتية» حالياً) |
| Google Cloud Build | طبقة مجانية سخية | مناسب للنشر على GCP فقط — لا حاجة له الآن |
| Azure DevOps / Harness | طبقة مجانية سخية | خارج النطاق الحالي |
| Bitbucket / Buildkite | محدودة | خارج النطاق |
| Bitrise / Codemagic / Appcircle | طبقات محدودة | CI موبايل — المشروع Python، غير مطلوب |

> ⚠️ **تصحيح للمصدر الأصلي:** «2,000 دقيقة/شهر» تنطبق على **private** فقط.
> للمستودع العام (حالتك) الدقائق **غير محدودة** — فهذا القيد لا يخصّك عملياً.

---

## 3. أهم فحوصات CI المجانية — حالتها في `new-cela`

| الفئة | الأداة المذكورة | حالتنا | المُنفَّذ |
|---|---|---|---|
| Secrets | Gitleaks / TruffleHog | ✅ بديل محلي | `scripts/secret_scan.py` (6 أنماط، يفشل البناء) |
| Dependencies | Dependabot / OSV / pip-audit / npm audit | ✅ | `pip-audit` **صارم** على النواة + `dependency_guard` |
| SAST | Semgrep / **CodeQL** | ✅ أُضيف | `.github/workflows/security-scan.yml` — CodeQL لـ Python + Actions |
| Containers/IaC | Trivy | ➖ | لا Docker حالياً — مؤجّل |
| Lint + types | ruff / mypy / tsc | ✅ جزئي | `ruff check .` · mypy مقترح |
| Tests | pytest + coverage threshold | ✅ أُضيف | **399 اختباراً** + `--cov-fail-under=60` (الفعلي **85%**) |
| Build check | failing pipeline | ✅ | كل خطوة تُفشل البناء عند الخطأ |
| **SBOM** | Syft / CycloneDX | ✅ أُضيف | `cyclonedx-py` → CycloneDX 1.6 كـ artifact |
| Dependency Review | dependency-review-action | ✅ أُضيف | يمنع تبعية عالية الخطورة في الـ PR |
| PII | (إضافة محلية) | ✅ | `scripts/pii_scan.py` — قانون 151/2020 |

### 🔁 خطأ اكتُشف بالتشغيل (قاعدة «لا تثق قبل التنفيذ»)

أمر `cyclonedx-py requirements -r requirements.txt` **فشل** (`unrecognized arguments: -r`)
لأن الصيغة تغيّرت في الإصدار 7.x إلى **positional argument**. جُرّبت الصيغة
الصحيحة محلياً قبل تثبيتها في CI ⇒ `CycloneDX 1.6`، 9 مكوّنات ✅.

**والتحذيرات كشفت فجوة حقيقية:** معظم التبعيات «بلا إصدار مثبَّت» في SBOM
لأننا نستخدم `>=` بلا lockfile. هذا **يُبرّر أولوية الـ lockfile** (البند 2 المتبقي).

---

## 4. تحسينات نُفِّذت فعلياً في هذه الدفعة

### أ. تثبيت كل Actions بـ commit SHA (قاعدة supply chain)

| Action | الإصدار | SHA |
|---|---|---|
| `actions/checkout` | v6 | `d23441a48e516b6c34aea4fa41551a30e30af803` |
| `actions/setup-python` | v6 | `ece7cb06caefa5fff74198d8649806c4678c61a1` |
| `actions/cache` | v5 | `caa296126883cff596d87d8935842f9db880ef25` |
| `actions/upload-artifact` | v6 | `b7c566a772e6b6bfb58ed0dc250532a479d7789f` |
| `github/codeql-action` | v4.39.1 | `9b3254085494b55a0f7c4c2b3d2d52cc712ce370` |
| `actions/dependency-review-action` | v5.0.0 | `a1d282b36b6f3519aa1f3fc636f609c47dddb294` |

**12 مرجع `uses:` في 3 workflows — كلها مثبّتة**، و`scripts/action_pin_guard.py`
يفشل البناء لو رجع أي tag متحرك (اختُبر بالاتجاهين).

> 📌 تحقق من [GitHub Docs: Secure use reference](https://docs.github.com/en/actions/reference/security/secure-use):
> «Pin actions to a full-length commit SHA... currently the only way to use an
> action as an immutable release.»

### ب. صلاحيات الأقل امتيازاً

- `permissions: contents: read` افتراضي في كل workflows.
- `security-events: write` **لـ job CodeQL فقط**، لا على مستوى الـ workflow.
- `agent-runner`: `issues: write` فقط للتعليق (كما كان).

### ج. توفير الدقائق (نصيحة البند 9)

- `concurrency` في `quality-gate` و`security-scan` ⇒ إلغاء التشغيلات المتكررة.
- `paths` filters ⇒ لا تعمل البوابة عند تغيير توثيق فقط.
- `agent-runner` فيه `concurrency` أصلاً + المتصفح يُثبَّت عند طلبه فقط.

### د. SAST أسبوعي

`schedule: '17 3 * * 1'` — CodeQL يعمل أسبوعياً حتى بلا pushes.

---

## 5. القالب الأدنى المطبَّق (ترتيب الفحوصات)

الترتيب الموصى به: `lint ← test ← secret ← dependency ← SAST ← build ← deploy`.
ترتيبنا الفعلي (مكيَّف مع كون المشروع Python بلا deploy):

```
1. compileall          ← فحص نحوي (أرخص أولاً — fail fast)
2. ruff               ← lint
3. secret_scan        ← أسرار
4. pii_scan           ← بيانات شخصية (قانون مصري)
5. dependency_guard   ← حزم غير معلنة
6. error_guard        ← كتم الأخطاء
7. policy_guard       ← تعطيل الحماية
8. spec_check         ← انحراف المنطق
9. action_pin_guard   ← supply chain
10. pip-audit strict  ← ثغرات التبعيات (النواة)
11. health            ← جاهزية بنيوية
12. pytest + coverage ← اختبارات (85% ≥ 60%)
13. SBOM              ← artifact
─── بالتوازي في workflow منفصل ───
    CodeQL (python + actions) · Dependency Review (على PR فقط)
```

> **المنطق:** الأرخص والأسرع أولاً ⇒ إذا فشل `ruff` لا نهدر دقائق على `pip-audit`.

---

## 6. قواعد عامة — الحالة

| القاعدة | الحالة |
|---|---|
| Branch protection يمنع merge بلا CI | ⚠️ **403** — إجراء يدوي لمالك المستودع |
| تثبيت Actions بـ SHA | ✅ **مُنفَّذ** + حارس آلي |
| `permissions` بالأقل امتيازاً | ✅ **مُنفَّذ** |
| لا أسرار في logs | ✅ `logger` لا يطبع المفاتيح + `sanitize_output.py` |
| استخدام secrets المنصة | ✅ `secrets.GEMINI_API_KEY` / `GROQ_API_KEY` |

---

## 7. ما تبقّى (بموافقة)

| الأولوية | الإجراء | السبب |
|---|---|---|
| 🟠 | **lockfile** (`uv.lock` / `pip-compile`) | SBOM أظهر تبعيات بلا إصدار مثبَّت |
| 🟠 | mypy للأنواع | استكمال «lint + types» |
| 🟡 | Semgrep OSS كطبقة SAST ثانية | تنويع عن CodeQL |
| 🟡 | Trivy عند إدخال Docker | لا حاويات حالياً |
| 🔴 | Branch protection (يدوي) | قيد 403 خارج الجلسة |

---

## المصادر

- [GitHub Changelog — Update to GitHub Actions pricing (التأجيل)](https://github.blog/changelog/2025-12-16-coming-soon-simpler-pricing-and-a-better-experience-for-github-actions/)
- [GitHub Docs — Billing and usage (public free)](https://docs.github.com/en/actions/concepts/billing-and-usage)
- [GitHub Docs — Secure use reference (SHA pinning)](https://docs.github.com/en/actions/reference/security/secure-use)
- [GitHub Docs — CodeQL supported languages](https://docs.github.com/en/code-security/code-scanning/introduction-to-code-scanning/about-code-scanning-with-codeql)
- [CI/CD Free Tier Comparison 2026 — AgentDeals](https://agentdeals.dev/cicd-free-tier-comparison-2026)
- [GitLab CI free tier explained 2026](https://cicdcalculator.com/gitlab-ci-free-tier)
- [GitHub Actions free tier limits 2026](https://cicdcalculator.com/github-actions-free-tier)
