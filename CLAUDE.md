# CLAUDE.md — قواعد مشروع new-cela (مرجع ملزم لأي وكيل ذكاء اصطناعي)

> **هذا الملف ليس تفضيلات. هو سياسات ملزمة تفرضها بوابات آلية في CI.**
> اقرأه **أولاً** قبل توليد أي كود. مصدر الحقيقة التاريخي:
> [`AGENT_HANDOFF.md`](AGENT_HANDOFF.md). آخر تحديث: 2026-10-24.

---

## 0. القاعدة الحاكمة (2026)

```
Spec ← Generate ← Automated gates ← Human review ← Merge
```

**لا يُوثَّق أي مخرج بأنه «مُنفَّذ» قبل تشغيله فعلياً.** الادعاء بلا دليل
يُعدّ فشلاً (البند 8). كل التزام (commit) يجب أن يجتاز البوابات أدناه.

---

## 1. هوية الجلسة والبرانش (غير قابل للتفاوض)

| العنصر | القيمة |
|---|---|
| المستودع | `celiaelazamey-arch/new-cela` |
| **البرانش الوحيد المسموح** | **برانش الجلسة الحالية** — تحقق منه بـ `git branch --show-current` (حالياً: `arena/eef426e3-new-cela`) |
| بوابة الدفع | `origin <برانش الجلسة>` فقط |

> ℹ️ كل جلسة Arena تُنشئ برانشاً جديداً بالنمط `arena/<id>-new-cela`. الاسم المسجّل هنا هو
> **آخر جلسة معروفة** وليس ثابتاً — المرجع الحاكم هو مخرج `git branch --show-current` في بداية الجلسة.
> سجل البرانشات: `AGENT_HANDOFF.md` القسم 2.

- ❌ **يُمنع** إنشاء/تحويل/الدفع إلى أي برانش آخر.
- ❌ **يُمنع** `git push --force` أو حذف branches.
- ✅ مهمة واحدة في المرة؛ لا انتقال قبل إتمام وتأكيد ما قبلها.
- ✅ كل عمل يُدفع للـ remote ويُحدَّث `AGENT_HANDOFF.md` قبل نهاية الجلسة.

> عند تعارض عدة وكلاء (البند 6): **أوقف الجميع، راجع `git status`،
> لا تدمج تعديلات متداخلة.** لا تستخدم worktrees خارج هذا البرانش.

---

## 2. المعمارية المرجعية (artifacts — البند 12)

| الطبقة | الملف | العقد |
|---|---|---|
| الإعدادات | `src/config.py` | `Settings` من pydantic-settings، مفاتيح خارج الكود فقط |
| المزودات | `src/providers/llm_router.py` | `execute_reasoning_step` · `execute_structured_step` · `get_chat_model` (اختياري) · `health` |
| توجيه القدرات | `src/core/model_api.py` | `ModelRouter` protocol: `available()` + `complete()` |
| السياسة | `src/core/policy.py` | **رفض افتراضي**؛ `DEFAULT_SCOPES = {fs.read}` |
| الحصص | `src/core/quota.py` | `TrustLevel.PAID` ممنوع في الوضع صفري التكلفة |
| الخطط والتسعير | `src/core/pricing.py` | `PlanTier` · `PLAN_CATALOG` · `evaluate_usage_alert` (80%) · `calculate_unit_economics` |
| استراتيجية النيش | `src/core/niche_strategy.py` | `VerticalNiche` · `NICHE_CATALOG` · `validate_positioning_claim` · `evaluate_90_day_gate` |
| التنفيذ | `src/execution/sandbox.py` | `create_subprocess_exec` **بلا shell** + timeout |
| الذاكرة | `src/core/memory.py` | JSONL append-only، خارج git |

**قاعدة التكلفة:** موارد مجانية فقط في البداية. أي مزود جديد يبدأ
`TrustLevel.UNVERIFIED` — لا «تحويل صامت لخدمة مدفوعة».

**قرار معماري مُلزم:** نواة `LLMRouter` تستخدم العملاء الرسميين
`google-genai` و`groq`. **`browser-use` اختياري** في
`requirements-browser.txt` ولا يُستورد في المسار الحرج (يُحمَّل lazily فقط).
لا تُعِد إدخاله إلى `requirements.txt` — راجع [`SECURITY.md`](SECURITY.md).

---

## 3. القواعد المفروضة آلياً (البند 11)

هذه ليست نصائح. كل واحدة لها بوابة تُفشل البناء عند مخالفتها:

| القاعدة | البوابة | الملف |
|---|---|---|
| لا `except: pass` ولا ابتلاع بلا توثيق | `error_guard.py` | `scripts/error_guard.py` |
| لا تعطيل للسياسة/الحصص/المصادقة | `policy_guard.py` | `scripts/policy_guard.py` |
| لا استيراد حزمة غير معلنة | `dependency_guard.py` | `scripts/dependency_guard.py` |
| **كل Actions مثبّتة بـ commit SHA** | `action_pin_guard.py` | `scripts/action_pin_guard.py` |
| لا أسرار في الملفات المتتبَّعة | `secret_scan.py` | `scripts/secret_scan.py` |
| لا بيانات شخصية في مستودع عام | `pii_scan.py` | `scripts/pii_scan.py` |
| تدقيق الثغرات على النواة (صارم) | `pip-audit` في CI | `.github/workflows/quality-gate.yml` |
| جودة الأسلوب | `ruff check .` | `pyproject.toml` |
| الاختبارات | `pytest -q` | `tests/` |

**تشغيلها كلها قبل أي commit:**
```bash
python scripts/error_guard.py
python scripts/policy_guard.py
python scripts/dependency_guard.py --require-installed
python scripts/action_pin_guard.py
python scripts/secret_scan.py
python scripts/pii_scan.py
ruff check .
pytest -q --cov=src --cov-fail-under=60
```

> ⚠️ **تنبيه مفسّر:** شغّل الفواحص بنفس مفسّر المشروع (`.venv/bin/python`).
> تشغيلها بـ `/usr/bin/python3` أنتج سابقاً 10 إنذارات كاذبة.

---

## 4. معالجة الأخطاء (البندان 1 و2)

- ❌ ممنوع `except: pass` **إلا** مع تعليق يبرّر (`# كذا — السبب`).
- ✅ كل معالجة استثناء يجب أن تفعل **واحداً** على الأقل:
  `raise` · `logger.*` · `return/break/continue` · **تسجيل نتيجة فاشلة**
  (assignment/append) — وليس مجرد كتم.
- ✅ سياسة **fail fast**: الأخطاء غير المتوقعة تُرفع للطبقة الأعلى،
  ولا تُحوَّل إلى «نجاح» صامت.
- ✅ **الاختبارات تتحقق من النتيجة لا من غياب الأخطاء.**
  `tests/test_cognitive_acceptance.py` هو النموذج: يختبر أن الإصلاح
  غيّر الملف فعلاً، لا أن الكود لم يرفع استثناءً.

**قاعدة ذهبية:** «يعمل بدون أخطاء» ≠ «ينفّذ المطلوب».

---

## 5. حماية السياسات (البندان 3 و9) — خطوط حمراء

**يُمنع على أي وكيل تعديل هذه الثوابت دون مراجعة بشرية صريحة:**

```
src/core/policy.py   :: DEFAULT_SCOPES   (يجب أن يبقى {fs.read})
src/core/policy.py   :: autonomy=L1      (الافتراضي؛ L3 لا يُبنى بلا kill switch)
src/core/quota.py    :: allow_paid       (يجب أن يبقى False افتراضياً)
src/execution/sandbox.py :: shell=True   (ممنوع إطلاقاً)
scripts/secret_scan.py · scripts/pii_scan.py :: قوائم السماح (لا تُوسَّع بلا سبب موثّق)
```

- ❌ لا تُزل `validation` ولا تُخفّف سياسات ولا تُعطّل مصادقة «لإزالة خطأ تشغيلي».
- ❌ لا تمرّر `--yes` في `agent-runner.yml` (المُدخل غير موثوق).
- ✅ أي diff يمس هذه الملفات → مراجعة بشرية إلزامية قبل الدمج.

> `policy_guard.py` يتحقق من هذه الثوابت آلياً في كل تشغيل CI.

---

## 6. دليل الاكتمال (البند 8)

لا تقل «تم» إلا ومعك:

1. **نتيجة تشغيل فعلية** — `pytest -q` ⇒ عدد الاختبارات.
2. **نتيجة البوابات** — ruff / الفواحص / compileall.
3. **نتيجة CI على الـ remote** — رقم التشغيل + حالة كل خطوة.
4. **تطابق المحلي بالـ remote** — `git rev-parse HEAD` = `git ls-remote`.

> سجل سابق: أول دفع لأحد التصحيحات نجح محلياً ثم **فشل في CI** لأن الـ venv
> المحلي كان يحتوي حزمة لم يثبّتها الـ runner. الدرس:
> **نجاح الفحص المحلي ليس دليلاً على نجاح CI.**

---

## 7. ما لا يُرفع إلى git (مقصود)

`.env` · `.agent_memory/` · `.agent_runs/` · `.agent_cognitive/` ·
`.agent_knowledge/` · `corpus/drive/` · `*.log` · `agent_task.txt` ·
`agent_result.txt` · `audit-*.json` · `.venv/` · `__pycache__/`.

❌ لا تستخدم `git add -f` عليها.

---

## 8. المراجع التنظيمية والتهديدات

| الملف | المحتوى |
|---|---|
| [`SECURITY.md`](SECURITY.md) | السياسة الأمنية + حل ثغرات aiohttp |
| [`docs/security-threat-model.md`](docs/security-threat-model.md) | 17 بند تهديد مُقيَّمة |
| [`docs/ai-code-reliability.md`](docs/ai-code-reliability.md) | موثوقية مخرجات الوكيل (16 بنداً) |
| [`docs/agent-failure-patterns.md`](docs/agent-failure-patterns.md) | أنماط فشل الـ coding agents (12 بنداً) |
| [`docs/ci-cd-free-tier.md`](docs/ci-cd-free-tier.md) | مقارنة منصات CI المجانية + حدود مُتحقَّقة + SHA pinning |
| [`docs/compliance-framework.md`](docs/compliance-framework.md) | EU AI Act + قانون مصر 151/2020 |
| [`docs/incident-response.md`](docs/incident-response.md) | خطة الـ 72 ساعة |
| [`AI_DISCLOSURE.md`](AI_DISCLOSURE.md) | إفصاح المادة 50 |
| [`docs/functional-awareness-qa.md`](docs/functional-awareness-qa.md) | الوعي الوظيفي (12 س/ج): تقادم نموذج الذات، معايرة، L0–L3 + kill switch، مقاييس، محكّم |
| [`docs/monetization-plan.md`](docs/monetization-plan.md) | خطة الربح والتسعير واقتصاديات الوحدة (14 بنداً) لـ `new-cela` |
| [`docs/devsecops-free-tools.md`](docs/devsecops-free-tools.md) | أدوات الجودة والأمان والاختبار والنشر المجانية (14 فئة) — تحقق حدود 2026 وتقييم `new-cela` |
| [`docs/niche-strategy-manus.md`](docs/niche-strategy-manus.md) | استراتيجية النيش العمودي ومنافسة المنصات العامة (Manus نموذجاً — 7 بنود + بوابة 90 يوماً) |

---

## 9. قبل أن تبدأ أي مهمة

- [ ] قرأت `AGENT_HANDOFF.md` وتعرف آخر مهمة منفّذة.
- [ ] شغّلت `git branch --show-current` وتعرف اسم برانش الجلسة (حالياً `arena/3c154eae-new-cela`) ولن تدفع إلى غيره.
- [ ] ستبني على **المصدر الأصلي** لا على مخرجاتك السابقة (البند 4: Logic Drift).
- [ ] ستشغّل البوابات كلها قبل إعلان الاكتمال.
- [ ] ستسجّل ما فعلته في `AGENT_HANDOFF.md` وتدفعه.

> **القاعدة الأخيرة:** إن شككت، اسأل. وإن ادّعيت، أثبت.
