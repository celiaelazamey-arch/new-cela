# خطة تنفيذية لإكمال البنود الناقصة

> كُتبت 2026-10-24 بعد المهمة 11. **خطة، لا تنفيذ** — لا يبدأ أي بند قبل تأكيد
> المستخدم، وبندٌ واحد في المرة (`CLAUDE.md` §1). كل بند له: الفجوة بدليلها،
> الملفات، الخطوات، **معيار القبول القابل للتشغيل**، التكلفة (كلها صفر)، ومن
> يملك القرار. الترتيب حسب (الأثر ÷ الكلفة) ثم التبعيات.

## 0. جرد الفجوات (من الكود، لا من الذاكرة)

| # | الفجوة | الدليل |
|---|---|---|
| A1 | `Arbiter` غير مُسلَّك في أي مسار تشغيل | `grep -rn arbiter src` ⇒ لا استخدام خارج `src/core/arbiter.py` |
| A2 | `compute_metrics`/`promotion_check` بلا واجهة (لا CLI ولا لوحة) | لا استخدام خارج `src/awareness/metrics.py`؛ `main.py` بلا `--metrics` |
| A3 | `Incident` نموذج في الذاكرة فقط — لا سجل دائم ولا من يُنشئه | لا `IncidentLedger`؛ `cognitive/loop.py` لا يسجّل أعطالاً |
| A4 | لا مسار لتسجيل **حكم بشري** (`source="human"`, `reversed_by_human`) | الحقول موجودة؛ لا CLI يكتبها |
| A5 | لا أداة تُعلن `reversible=True` ⇒ L2 عملياً = L1 | `replace_text` يحسب `before_hash` لكن لا يحفظ نسخة قبلية (`builtin.py:189-215`) |
| A6 | `BASELINE_VERIFIED_AT` يُحدَّث يدوياً | `self_model.py:30` ثابت نصي |
| A7 | التلخيص الدوري للذاكرة + التناقض الدلالي | `memory.py` يملك TTL وتناقضاً نصياً فقط |
| A8 | chaos على مستوى العملية (قتل subprocess، مهلة، امتلاء قرص) | `tests/test_chaos_awareness.py` وحدات فقط |
| B1 | PR #8 (4 تنبيهات CodeQL) مفتوح، غير مدموج | `gh pr view 8` ⇒ MERGEABLE، كل الفحوص خضراء |
| B2 | لا lockfile ⇒ SBOM بلا إصدارات مثبَّتة | `ls requirements*.lock` فارغ |
| B3 | نتائج CodeQL غير قابلة للقراءة من الجلسة (403) | `security-scan.yml:58` يرفع SARIF فقط ولا يطبع ملخصاً |
| B4 | Branch protection على `main` | يحتاج مالك المستودع (API ⇒ 403) |
| B5 | قرار PDPC للنقل عبر الحدود — **31 أكتوبر 2026** | قرار قانوني بشري |
| C1 | `README.md:407` يذكر `--tests 243` (الفعلي 432) | نص قديم |

---

## المرحلة 1 — إغلاق ما هو جاهز (يوم واحد)

### 1.1 دمج PR #8 ثم مزامنة برانش الجلسة
- **الخطوات:** مراجعة diff الـ10 ملفات (خاصة `src/core/path_guard.py`) ← `gh pr merge 8 --squash` ← `git merge origin/main` في برانش الجلسة (لا تبديل برانش) ← تشغيل البوابات كاملة.
- **القبول:** `main` يحتوي `path_guard.py`؛ برانش الجلسة لا يتأخر عن `main`؛ **432+ pytest ✅** · CI ✅.
- **المالك:** الوكيل بتفويض صريح للدمج.

### 1.2 ملخص CodeQL مقروء في سجل الـ job (يتجاوز 403 بلا صلاحية)
- **الملفات:** `.github/workflows/security-scan.yml` + `scripts/sarif_summary.py` (جديد، بلا تبعيات).
- **الخطوات:** إضافة `output: sarif-results` لخطوة `analyze` + `upload: always` ← خطوة تالية تشغّل `sarif_summary.py` تطبع (القاعدة · الخطورة · الملف:السطر) وتُفشل الـ job عند `error`-level ← تثبيت أي action جديد بـ SHA (`action_pin_guard`).
- **القبول:** `gh run view --log` يُظهر الجدول؛ اختبار يُغذّي SARIF وهمياً بتنبيه High ⇒ EXIT 1، وبدونه ⇒ 0 (الاتجاهان).
- **لماذا الآن:** يحوّل «لا أستطيع القراءة» إلى «أقرأ من سجل CI» — مجاناً.

### 1.3 تصحيح README (`--tests 432`) — مع 1.1 في الالتزام نفسه.

---

## المرحلة 2 — تسليك ما بُني في المهمة 11 (3–4 أيام، بند/يوم)

### 2.1 `replace_text` قابل للتراجع ⇒ L2 يصبح حقيقياً (A5)
- **الملفات:** `src/tools/builtin.py`, `src/tools/registry.py`, `src/core/policy.py` (لا تغيير في الافتراضيات).
- **الخطوات:** قبل الكتابة يُحفظ `<file>.<sha8>.bak` داخل `.agent_runs/backups/` (مستبعد من git) ← الأداة تُعلن `reversible=True` في `authorize()` ← أداة جديدة `undo_replace(backup_id)` بنطاق `fs.write` ← `auto_actions` يُطبع في `TaskResult.awareness` تحت «نُفِّذ تلقائياً».
- **القبول:** اختبار L2: تعديل ثم `undo` يعيد البصمة الأصلية بالضبط؛ اختبار L1: السلوك لم يتغيّر (ASK)؛ `policy_guard` ✅.
- **خط أحمر:** `fs.delete` يبقى غير قابل للتراجع مهما كان.

### 2.2 سجل الأعطال + MTTD/MTTR من التشغيل الفعلي (A3)
- **الملفات:** `src/awareness/incidents.py` (جديد: `IncidentLedger` JSONL بنمط `CalibrationLedger`)، `src/cognitive/loop.py`, `src/core/engine.py`.
- **الخطوات:** عند فشل verifier أو `tool_failures>0` ⇒ `Incident(occurred_at=بداية الخطوة, detected_at=الآن, detected_by="system")` ← عند نجاح إعادة المحاولة ⇒ `resolved_at` ← `diagnosis_correct` يبقى `None` حتى يُحكم عليه (2.3).
- **القبول:** اختبار قبول يحقن فشلاً ثم إصلاحاً ⇒ `mttd>0`, `mttr>0`، والسجل التالف يُحصى لا يُسقط.

### 2.3 واجهة الحكم البشري (A4)
- **الملفات:** `src/awareness/cli.py` (جديد) + إدخال في `main.py`.
- **الأوامر:** `python -m src.awareness.cli reverse <run_id> --note "..."` ⇒ `record(source="human", reversed_by_human=True)` · `diagnose <incident_id> --correct/--wrong`.
- **القبول:** بعد `reverse` يرتفع `reversal_rate`، و`promotion_check(L2)` يفشل ببند «القرارات المعكوسة» — مُختبَر.

### 2.4 `--metrics` في CLI + تبويب في اللوحة (A2)
- **الملفات:** `src/main.py`, `src/dashboard/data.py` (طبقة بيانات قابلة للاختبار), `src/dashboard/app.py`.
- **الخطوات:** `python -m src.main --metrics [--json]` يطبع `MetricsSnapshot.render()` + `promotion_check` لكل مستوى ← اللوحة تعرض نفس البيانات من `data.py` (بلا Streamlit في الاختبار).
- **القبول:** اختبار على مجلد تشغيلات مُولَّد بـ `seed_demo_data.py` ⇒ الأرقام تطابق الحساب اليدوي.

### 2.5 تحديث آلي لتاريخ التحقق (A6)
- **الملفات:** `scripts/self_model_refresh.py` (جديد)، `quality-gate.yml`، `src/awareness/self_model.py`.
- **الخطوات:** CI بعد نجاح pytest يكتب `.agent_self_model/verified.json` {sha, at, tests} كـ artifact ← محلياً `python -m src.awareness.report --refresh` يقرأ آخر نتيجة pytest (junit) ويستدعي `mark_verified` للعناصر التي تغطيها اختباراتها (خريطة قدرة→ملف اختبار صريحة) ← `BASELINE_VERIFIED_AT` يبقى كحد أدنى لا كمصدر وحيد.
- **القبول:** بعد تشغيل ناجح `coverage()` = الكامل؛ بعد تعطيل اختبار قدرة ⇒ تلك القدرة لا تُختم.
- **قيد صريح:** artifact الـ CI لا يُكتب في المستودع (قاعدة «لا مخرجات مولَّدة في git»).

### 2.6 تسليك `Arbiter` حيث يوجد تعارض فعلي (A1)
- **الموضع الوحيد الحالي للتعارض:** Planner (يقترح خطوة) × Critic (يرفضها) في `src/core/planner.py`.
- **الخطوات:** عند `attempts ≥ 2` مع اختلاف ⇒ `Arbiter(owners={"step_outcome": "critic"})` يحسم ويسجّل `Ruling` في الذاكرة (`LESSON`) ← في وضع التصعيد تُعلَّم الخطوة `UNVERIFIED` بدل التخمين.
- **القبول:** اختبار يُجبر التعارض ⇒ الذاكرة تحتوي سبب الترجيح؛ لا تغيير في مسار الإجماع.

---

## المرحلة 3 — سلسلة التوريد (يومان)

### 3.1 lockfile + SBOM بإصدارات مثبَّتة (B2)
- **الأداة:** `pip-compile` (pip-tools) — مجاني، يُثبَّت من `requirements-ci.txt`.
- **الخطوات:** `requirements.lock` للنواة + `requirements-ci.lock` ← CI يثبّت من الـ lock ← `pip-audit -r requirements.lock` ← SBOM يُولَّد من الـ lock ⇒ إصدارات حقيقية ← Dependabot/تجديد أسبوعي عبر job مجدول يفتح PR (لا دمج آلي).
- **القبول:** SBOM يحتوي `version` لكل مكوّن؛ `dependency_guard --require-installed` ✅؛ CI ✅.
- **مخاطرة مسجّلة:** تضارب `browser-use` (pin حصري لـ aiohttp) — يبقى في ملف lock منفصل اختياري.

### 3.2 chaos على مستوى العملية (A8)
- **الملفات:** `tests/test_chaos_process.py` (جديد)، `src/execution/sandbox.py` (إن لزم).
- **السيناريوهات:** (أ) subprocess يتجاوز المهلة ⇒ `kill` + `Incident` + لا تعليق؛ (ب) `ENOSPC` محاكى بـ `monkeypatch` على `open` أثناء `compact()`/`_append` ⇒ تحذير مُسجَّل، الذاكرة الحية سليمة، لا استثناء؛ (ج) ملف checkpoint مقطوع في منتصفه ⇒ الاستئناف يرفض بصدق ولا يدّعي.
- **القبول:** كل سيناريو يجيب الأسئلة الثلاثة (اكتُشف؟ كم استغرق؟ شُخِّص؟) بتوكيدات صريحة.

---

## المرحلة 4 — ما يحتاج نموذجاً (مؤجَّل حتى تتوافر بيانات)

### 4.1 تلخيص دوري للذاكرة (A7)
- **الشرط المسبق:** ≥ 200 ذكرى حدثية فعلية؛ قبلها التلخيص بلا قيمة مقيسة.
- **التصميم:** job أسبوعي (`--learn` موجود أصلاً) يأخذ ذكريات نفس المهمة ⇒ يطلب من المزود **المجاني المُتحقَّق** تلخيصاً إلى `LESSON` واحد ⇒ يُحفظ **بجانب** الأصل ولا يحذفه حتى يمرّ TTL ⇒ `provider_unverified` يمنع التشغيل.
- **القبول:** الاسترجاع بعد التلخيص يُعيد الدرس أولاً (اختبار بـ router وهمي)، وعدد الذكريات لا يزيد عن `max_entries`.

### 4.2 التناقض الدلالي (A7)
- بالترتيب الأرخص: (1) تطبيع نصي + Jaccard على tokens ≥ 0.8 بين `failure`/`success` لنفس المهمة ⇒ توسيع `remove_contradictions` (بلا نموذج)؛ (2) النموذج فقط للحالات 0.5–0.8 كتصنيف ثنائي مع تسجيل القرار في الذاكرة.
- **القبول:** مجموعة حالات ذهبية صغيرة (10 أزواج) بدقة ≥ 9/10 للمرحلة (1).

---

## بنود خارج الكود — تحتاج قراراً بشرياً (لا يملكها الوكيل)

| البند | الموعد | ما يحتاجه منك |
|---|---|---|
| **B5 PDPC** — النقل عبر الحدود | **31 أكتوبر 2026 (7 أيام)** | قرار: (أ) طلب ترخيص، أو (ب) استبعاد البيانات الشخصية من أي مزامنة Drive/مزود خارجي. الخيار (ب) يمكن للوكيل تنفيذه تقنياً في يوم (تشديد `pii_scan` ليعمل على مسار المزامنة وقت التشغيل لا على الكود فقط). |
| **B4 Branch protection** على `main` | — | من الإعدادات: require PR + status checks (`quality`, `CodeQL`) + no force-push. يستغرق دقيقتين من المالك. |
| صلاحية قراءة تنبيهات CodeQL (اختياري) | — | إن مُنحت تُلغي الحاجة لـ 1.2؛ إن لم تُمنح فـ 1.2 يكفي. |

---

## ترتيب التنفيذ المقترح (مهمة واحدة في المرة)

```
12: 1.1 + 1.3   دمج PR #8 + README           ← جاهز، أقل مخاطرة
13: 1.2         ملخص CodeQL في سجل CI        ← يفكّ قيد 403 نهائياً
14: 2.1         replace_text قابل للتراجع    ← يجعل L2 حقيقياً
15: 2.2 + 2.3   سجل الأعطال + الحكم البشري   ← MTTD/MTTR من بيانات حقيقية
16: 2.4         --metrics + اللوحة
17: 2.5         تحديث التحقق آلياً
18: 3.1         lockfile + SBOM
19: 2.6         تسليك Arbiter
20: 3.2         chaos العملية
21+: 4.x        عند توافر البيانات
```

**قاعدة ثابتة لكل بند:** Spec (هذه الوثيقة) ← Generate ← البوابات الـ7 + pytest
← CI على الـ remote برقم تشغيل ← تسجيل في `AGENT_HANDOFF.md` ← تأكيدك قبل التالي.
