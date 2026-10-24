# الوعي الوظيفي — 12 سؤالاً مُقيَّمة على الكود الفعلي

> **مرجع دائم داخل النظام.** زُرع في 2026-10-24 (المهمة 11) من دفعة المستخدم،
> وقُيِّم كل بند على الكود **بأدلة ملف:سطر**، ثم سُدَّت الفجوات القابلة للتنفيذ
> بميزانية صفر. الحالة قبل/بعد مذكورة بصراحة؛ ما بقي ناقصاً مكتوب لا مخفي.
> اختبارات الضغط: [`tests/test_chaos_awareness.py`](../tests/test_chaos_awareness.py) (29 اختباراً).

| الحالة | المعنى |
|---|---|
| ✅ | محقَّق في الكود ومُختبَر |
| 🆕 | أُضيف في المهمة 11 |
| ⚠️ | جزئي — ما ينقص مكتوب |
| 📖 | مفاهيمي/توجيهي — لا يحتاج كوداً |

---

## 1. ما الفرق بين المراقبة (monitoring) والوعي الوظيفي؟ 📖 → ✅

**الجواب:** monitoring يجمع أرقاماً وينبّه. الوعي يربط الأرقام **بنموذج للنظام**
ويقرر ويتعلم. **الاختبار:** هل يشرح النظام لماذا حدث العطل ويقترح إصلاحاً؟

**على الكود:**
- monitoring = `src/core/telemetry.py` (سجل تشغيلات JSON) + `src/dashboard/`.
- الربط بنموذج = `SelfModel.drift()` (`src/awareness/self_model.py:161`) يقارن ما
  يدّعيه النموذج بما على القرص ويُخرج `DriftFinding` **بسبب مكتوب** («الدليل يشير
  إلى X وهو غير موجود»)، لا مجرد تنبيه.
- القرار = `CalibrationLedger.gate()` (`src/awareness/calibration.py:321`) يحوّل
  القياس إلى proceed/escalate/refuse.
- التعلم = السجل نفسه يُغذّي `effective_confidence()` في المرات التالية.

## 2. من أين أبدأ فعلياً؟ 📖 ✅

**الجواب:** observability موحّدة ← service catalog آلي ← ذاكرة incidents. لا
agents مستقلة قبل قياس وتحقق.

**على الكود:** هذا الترتيب هو ما حدث فعلاً في تاريخ المستودع: `telemetry.py`
(الالتزام `847a214`) و`policy.py`/`quota.py` (`eaabccb`) سبقا أي استقلالية، والافتراضي
حتى اليوم `L1` + `DenyAll` + `{fs.read}`. الـ catalog = `default_self_model()`
بقائمة القدرات ودليل كل واحدة. ذاكرة الأعطال = `Incident` في
`src/awareness/metrics.py:31` (🆕).

## 3. كيف يعرف النظام حدود قدرته؟ ⚠️ → ✅ 🆕

**الجواب:** capability registry (ما يستطيع | ما لا يستطيع) + قياس دقة تاريخي لكل
مهمة + رفض أو تصعيد تحت عتبة ثقة محددة.

| الجزء | قبل | بعد |
|---|---|---|
| registry | ✅ `Capability(status=verified/declared/absent)` | كما هو + `last_verified_at`/`ttl_days` |
| دقة تاريخية لكل مهمة | ❌ الثقة استدلالية فقط (`metacognition.estimate`) | 🆕 `CalibrationLedger.accuracy(task_kind)` + `summary().by_kind` |
| رفض/تصعيد تحت عتبة | ❌ `is_low` بلا أثر | 🆕 `gate()`: `< 0.25` ⇒ **refuse**، `< 0.5` ⇒ **escalate**، وإلا proceed — كل حكم بسبب |

مُسلَّك في المحرك: `AgentEngine._calibrate()` (`src/core/engine.py:429`) — الحكم
يظهر في `TaskResult.awareness` وتُسجَّل النتيجة بمصدر `verifier`.

## 4. كيف أمنع النظام من الهلوسة في تقييم نفسه؟ ✅ (+🆕 drift)

**الجواب:** verifier مستقل | اختبارات قابلة للتنفيذ بدل الادعاء | مقارنة
الـ self-model بالواقع آلياً (drift detection).

- verifier مستقل: `src/cognitive/loop.py` + سجل hash-chained في `src/cognitive/store.py`
  + شروط القبول في `AgentEngine._verify` — كانت موجودة.
- «صحيح» في سجل المعايرة **لا يأتي من النموذج**: `CalibrationRecord.source ∈ {verifier, human, self}`
  والمحرك يسجّل `verifier` فقط.
- 🆕 drift: `SelfModel.drift(root)` يفحص كل مسار في دليل عنصر «verified»؛ اختبار
  `test_default_self_model_has_no_drift_against_real_repo` يحرس المستودع نفسه
  (9 مسارات مُستشهَد بها).

## 5. كيف تُبنى الذاكرة دون أن تتضخم؟ ⚠️ → ✅ 🆕

**الجواب:** 3 طبقات ← قصيرة (جلسة) | episodic (حدث ← فعل ← نتيجة) | دلالية
(قواعد ملخصة). تلخيص دوري + TTL + إزالة المتناقض.

| الطبقة | أين | TTL |
|---|---|---|
| قصيرة | `CognitiveState` + `WorkspaceState` — تعيش بعمر التشغيل ولا تُحفظ في الذاكرة الدائمة عمداً | — |
| episodic | `MemoryStore` بأنواع `success/failure/fact` | 🆕 `prune_expired(ttl_days)` (`src/core/memory.py:446`) |
| semantic | `MemoryStore` بنوع `lesson` (`SEMANTIC_KINDS`) | لا تنتهي؛ تُنقض صراحةً |

- 🆕 `layers()` يعرض التوزيع. 🆕 `remove_contradictions()` (`memory.py:477`) يحذف
  `failure` له `success` أحدث لنفس (المهمة، المحتوى).
- **ناقص بصراحة:** «تلخيص دوري» يحتاج نموذجاً؛ لم يُنفَّذ. التناقض الدلالي (لا
  النصي) كذلك.

## 6. متى أسمح بالاستقلالية؟ ⚠️ → ✅ 🆕

**الجواب:** تدريجياً L0 يقترح فقط · L1 ينفّذ بعد موافقة · L2 ينفّذ الآمن القابل
للتراجع ويُبلغ · L3 مستقل في نطاق محدد مع kill switch. **الترقية بالمقاييس لا
بالانطباع.**

🆕 `AutonomyLevel` (`src/core/policy.py:66`) + `KillSwitch` (`policy.py:86`) داخل
`PolicyEngine.evaluate()` (`policy.py:294`) — الترتيب: kill switch ← النطاق ← القراءة ← المستوى ← حد الموافقات.

| المستوى | السلوك المُختبَر |
|---|---|
| L0 | write/execute ⇒ `DENY` حتى مع `AutoApprove` |
| L1 (**الافتراضي — لم يتغيّر**) | `ASK` + `DenyAll` + `{fs.read}` |
| L2 | `reversible=True` **يعلنه المستدعي** وليس من `IRREVERSIBLE_SCOPES` ⇒ `ALLOW` ويُسجَّل في `auto_actions` للإبلاغ |
| L3 | `autonomy_scopes` ∩ الممنوح − اللارجعي ⇒ `ALLOW`؛ **البناء بلا kill switch يرفع `ValueError`** |

kill switch: `engage()` برمجياً، أو `NEW_CELA_KILL_SWITCH=1`، أو ملف
`.agent_kill_switch` في جذر مسموح — يُفحص **قبل كل قرار** ويوقف حتى القراءة.
الترقية: `promotion_check()` (`src/awareness/metrics.py:198`) بعتبات `PROMOTION_CRITERIA`
(مثلاً L2 يحتاج ≥30 سجلاً، دقة ≥85%، ECE ≤0.10، معكوس ≤10%، تغطية ≥70%، صفر متقادم/انجراف).
«لا بيانات» تُعدّ فشلاً لا نجاحاً.

## 7. ما مقاييس النجاح؟ ❌ → ✅ 🆕

| المقياس | أين يُحسب |
|---|---|
| MTTD / MTTR | `Incident.time_to_detect/resolve` ← `compute_metrics()` (`metrics.py:107`) |
| نسبة الحوادث المكتشفة ذاتياً | `Incident.detected_by == "system"` أو `CalibrationRecord.detected_by_system` |
| معايرة الثقة | `brier_score`, `expected_calibration_error`, `calibration_bins` (`calibration.py`) |
| نسبة القرارات المعكوسة بشرياً | `CalibrationRecord.reversed_by_human` ← `reversal_rate()` |
| تغطية الـ self-model | `SelfModel.coverage()` = (مُتحقَّق **وغير متقادم**) / الكل |

كلها من ملفات JSONL محلية. أي حقل بلا بيانات = `None` ويُعرض «—»، لا صفر مزيّف.

## 8. ما أخطر فخ؟ ❌ → ✅ 🆕

**الجواب:** self-model قديم يُنتج قرارات خاطئة بثقة عالية. **الحل:** تحديث آلي +
تاريخ آخر تحقق لكل عنصر + تنبيه عند التقادم.

- 🆕 `Capability.last_verified_at` + `ttl_days` (افتراضي 30) + `is_stale()` (`self_model.py:74`).
- 🆕 `default_self_model()` يختم كل «verified» بـ `BASELINE_VERIFIED_AT`
  (= آخر تحقق كامل مُسجَّل، يُحدَّث يدوياً مع كل تحقق فعلي — لا تلقائياً بلا دليل).
- 🆕 `describe()` يُنزل المتقادم من «✅ مُتحقَّق» إلى «⏳ تقادم تحققها» ويطبع التغطية.
- 🆕 `mark_verified(name)` لا يُستدعى إلا بعد تشغيل فعلي.
- الاختبار: `test_stale_self_model_is_detected_and_demoted_in_report` — بعد 45 يوماً
  التغطية **تنهار إلى 0%** بصدق بدل أن تبقى 62% زوراً.

## 9. كيف أختبر «وعيه» بالضغط؟ ❌ → ✅ 🆕

`tests/test_chaos_awareness.py` — 29 اختبار حقن أعطال، كل واحد يجيب: هل اكتشف؟
كم استغرق؟ هل شخّص صحيحاً؟

| الحقن | ما يُقاس |
|---|---|
| نموذج ذات تحقّق قبل 45 يوماً | اكتُشف التقادم، التغطية 0%، التقرير يُنزل العنصر |
| دليل يشير إلى ملف محذوف | `DriftFinding(missing_evidence)` **يذكر اسم الملف** |
| سطران تالفان في سجل المعايرة | يُحصيان (2) ويُعرضان، لا انهيار |
| ثقة 0.9 بسجل 1/10 | البوابة ترفض/تُصعّد؛ الفعلية ≈ 0.3 |
| verifier مُضلِّل (ثقة بلا تاريخ) | تصعيد لا proceed |
| kill switch في منتصف التشغيل | الطلب التالي يُرفض **بالسبب**، حتى القراءة، ومُدوَّن في التدقيق |
| L3 بلا kill switch | `ValueError` عند البناء |
| عطل بثلاثة أزمنة | MTTD=120ث، MTTR=600ث، اكتشاف ذاتي 50%، تشخيص 100% |
| وكيلان متعارضان | المالك يفوز؛ الدليل قبل الثقة؛ التعادل ⇒ تصعيد |
| سجل معايرة يرفع استثناء داخل المحرك | المهمة تكتمل والبوابة تغيب بصمت مُسجَّل |

**ناقص بصراحة:** لا توجد سيناريوهات chaos على مستوى العملية (قتل subprocess،
امتلاء قرص) — تحتاج بيئة أكبر من وحدات الاختبار.

## 10. كيف أطبقه بميزانية مجانية؟ ✅

logs منظمة JSON (`telemetry.py`, `AuditLog`, `CalibrationLedger` — كلها JSONL/JSON
ذرّي) · ذاكرة محلية (`MemoryStore` معجمي؛ لا FAISS/SQLite لأن الحجم لا يبرّرهما
بعد — مُسجَّل كقرار لا كنسيان) · GitHub Actions كـ verifier (19 خطوة في
`quality-gate.yml`) · agents بصلاحيات قراءة فقط أولاً (`DEFAULT_SCOPES={fs.read}` —
خط أحمر يحرسه `scripts/policy_guard.py`). لا خدمة مدفوعة.

## 11. ماذا أفعل عند تعارض قرارات agents؟ ❌ → ✅ 🆕

🆕 `src/core/arbiter.py`: `Arbiter(owners={decision_key: agent}, recorder=...)`.
القواعد بالترتيب: (1) **مالك واحد لكل قرار** يفوز؛ (2) بلا مالك ⇒ **الدليل قبل
الثقة**، وعند تعادل الأدلة فارق ثقة ≥ `MIN_MARGIN=0.15`؛ (3) التعادل ⇒ **تصعيد**
(`choice=None`) — لا تخمين. كل `Ruling` يحمل `rule` و`reason` ويُمرَّر إلى
`recorder` (مثلاً `MemoryStore.add(..., LESSON)`) فيُحفظ **سبب الترجيح في الذاكرة**.

## 12. هل هذا «وعي» حقيقي؟ ✅

لا دليل على ذلك، ولا ندّعيه. موثّق حرفياً قبل هذه الدفعة في
[`docs/research/awareness.md`](research/awareness.md) (§1: وظيفي ✅ / فينومينولوجي ⛔)،
وفي `DISCLAIMER` داخل كل `IntrospectionReport`، وفي عنصر `absent` صريح في
`default_self_model()`: «وعي فينومينولوجي أو تجربة ذاتية — لا يوجد ما يبرّر ادّعاءه».
نهتم بالنتائج القابلة للقياس (القسم 7) لا بالتسمية.

---

## الخلاصة: قبل/بعد المهمة 11

| # | قبل | بعد |
|---|---|---|
| 1, 2, 10, 12 | ✅ | ✅ (موثَّق بالأدلة) |
| 3 | ⚠️ ثقة استدلالية | ✅ سجل معايرة + بوابة refuse/escalate مُسلَّكة في المحرك |
| 4 | ✅ verifier | ✅ + drift detection آلي |
| 5 | ⚠️ طبقة واحدة | ✅ طبقتان مخزَّنتان + TTL + إزالة تناقض (التلخيص الدوري ناقص) |
| 6 | ⚠️ L0/L1 ضمنياً | ✅ L0–L3 صريحة + kill switch + ترقية بالأرقام |
| 7 | ❌ | ✅ MTTD/MTTR/Brier/ECE/معكوس/تغطية |
| 8 | ❌ | ✅ TTL + تاريخ تحقق + تنبيه تقادم |
| 9 | ❌ | ✅ 29 اختبار حقن |
| 11 | ❌ | ✅ arbiter بثلاث قواعد + تسجيل السبب |

**ما لم يتغيّر عمداً (خطوط حمراء):** `DEFAULT_SCOPES={fs.read}`، `DenyAll`
افتراضي، المستوى الافتراضي `L1`، سقف الثقة 0.90.
