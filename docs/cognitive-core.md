# Celia Cognitive Core — المرحلة الأولى ومهمة القبول

**تاريخ التحقق:** 2026-10-23
**النطاق:** حالة تشغيلية وأهداف وسلسلة أدلة، مع الحد الأدنى من نموذج العالم والدورة الذي تتطلبه مهمة الإصلاح الحقيقية.

> هذه بنية هندسية للحالة والانتباه والقياس. ليست وعياً فينومينولوجياً، ولا تجربة ذاتية، ولا نموذجاً «يفهم» المستودع لمجرد أنه يحفظ بصماته.

## 1) ما بُني فعلاً

| الملف | المسؤولية المنفّذة |
|---|---|
| `src/cognitive/state.py` | `CognitiveState`، انتقالات `before/after` وبصماتها، receipts، وcheckpoints مختومة |
| `src/cognitive/goals.py` | هدف رئيسي/فرعي، أولوية وميزانية، ومعايير `command_exit` / `file_changed` / `engine_checks` |
| `src/cognitive/store.py` | أحداث JSONL مترابطة بـSHA-256، فحص السلسلة، وحفظ/تحميل checkpoint ذرّياً |
| `src/cognitive/world.py` | لقطة `git sha` وبصمات ملفات ونتائج أوامر، ثم `WorldDiff` بين لقطتين |
| `src/cognitive/loop.py` | دورة إصلاح محدودة: ملاحظة ← انتباه ← تقييم ← قرار ← بوابة ← فعل ← قياس ← تعلّم ← checkpoint |
| `src/cognitive/report.py` | تقرير عربي مشتق من الحالة: ما تم، ما بقي، لماذا، وما لم يُتحقق |
| `src/core/engine.py` | إرفاق حالة وهدف وسلسلة أدلة وتقرير بنتيجة `AgentEngine` دون تغيير مصدر `verified` |
| `src/tools/builtin.py` | `replace_text` بكتابة ذرّية وبصمة قبل/بعد، و`run_command` بقياس `returncode/stdout/stderr` و`cwd` محصور |

أصبح `TaskResult` يحمل الحقول الاختيارية `cognitive` و`cognitive_report` و`evidence_head` و`checkpoint_id`. وجود هذه الحقول لا يضيف نجاحاً: `TaskResult.verified` ما زال يُحسب من شروط `_verify`، و`CognitiveResult.verified` خاصية مشتقة من حالة `done` واجتياز المعايير الإلزامية.

## 2) قواعد الدورة التي تمنع «تجميل» النتيجة

1. **الهدف قابل للتنفيذ:** لا يكفي وصف «أصلح الاختبار»؛ يوجد أمر تحقق ورمز خروج متوقع ومسار يجب أن تتغير بصمته.
2. **الفعل منظّم ومحدود:** الخطة من 1 إلى 3 عمليات `replace_text`. لا تُنفّذ شيفرة واردة في رد النموذج أو في ملف.
3. **البوابة قبل الأثر:** `fs.write` و`shell.exec` يمران بـ`PolicyEngine`. الرفض هو الافتراضي عند غياب الموافقة.
4. **الملاحظة ليست نجاحاً:** فشل `pytest` يُحفظ برمز خروج ومخرجات؛ `Evidence.ok=True` لأمرٍ فاشل تعني أن *الملاحظة صالحة*، لا أن الاختبار ناجح.
5. **لا خطوة بلا أثر:** نجاح أداة الكتابة لا يكفي. تلتقط الدورة الملف بعدها وترفض الخطوة إن لم تتغير البصمة.
6. **النجاح بعد القياس:** لا تنتقل الحالة إلى `done` إلا بعد اجتياز كل معيار إلزامي. الاختبار الناجح قبل الإصلاح لا يُنسب إلى الفعل.
7. **الدرس من النتيجة:** النص المحفوظ في `MemoryStore` يتضمن الحالة ورموز الأوامر والملفات المتغيرة والمعايير الباقية، لا وصفاً ذاتياً حراً.
8. **ميزانية محفوظة:** `tool_calls` و`completed_steps` داخل checkpoint؛ الاستئناف لا يمنح ميزانية جديدة ولا يعيد الفعل المكتمل.

## 3) مهمة القبول ونتيجتها

المهمة المنفّذة في الاختبارات: مستودع Git مؤقت فيه `app.py` واختبار جمع فاشل. الخطة تستبدل `return a - b` بـ`return a + b` فقط.

| شرط القبول | الدليل في الاختبار |
|---|---|
| ① تغيّر ملف فعلاً | `WorldDiff.changed == app.py` وتغيّرت البايتات |
| ② اختبار فاشل صار ناجحاً | `baseline.returncode != 0` ثم `final.returncode == 0` |
| ③ بصمة الدليل تطابق النهائي | `artifact.sha256 == sha256(app.py bytes)` |
| ④ لا كتابة خارج الجذر | خطة `../outside.py` تُرفض قبل أي أمر، والملف الخارجي لا يتغير |
| ⑤ الاستئناف لا يعيد المنفّذ | checkpoint بعد `act+measure` يؤدي إلى `run_command` فقط؛ لا `replace_text` ثانيةً |
| ⑥ حتمية القرار | الحالة والخطة نفسهما تعطيان `next_action` وبصمة الانتقال نفسيهما |

توجد أيضاً اختبارات لرفض الموافقة، وبصمة القراءة المتقادمة، وعدد مواضع الاستبدال، والتلاعب بالسجل/checkpoint، وإعادة التخطيط مرة واحدة كحد أقصى، وميزانية نداءات الهدف، ورفض الاختبار الناجح أصلاً كإصلاح.

**تفصيل حقيقي ظهر أثناء القبول:** أدى استبدال بالحجم نفسه داخل الثانية نفسها إلى قبول Python لملك `pyc` قديم. لذلك تضمن `replace_text` تقدّم `mtime` عند هذه الحالة؛ لم يُعالج الفشل بتعديل الاختبار أو تجاهل رمز خروجه.

## 4) استخدام مسار الإصلاح

```python
import asyncio
from pathlib import Path

from src.cognitive.goals import Goal
from src.cognitive.loop import CognitiveLoop, RepairAction, RepairPlan
from src.cognitive.store import CheckpointStore, EvidenceStore
from src.core.memory import MemoryStore
from src.core.policy import AutoApprove, PolicyEngine
from src.core.quota import QuotaGuard
from src.tools.builtin import build_default_registry
from src.tools.registry import ToolContext

repo = Path("/path/to/isolated/repo").resolve()
policy = PolicyEngine(
    allowed_roots=[repo],
    granted_scopes={"fs.read", "fs.write", "shell.exec"},
    # موافقة آلية صريحة للتشغيل غير التفاعلي/الاختبار فقط.
    # استخدم معالج موافقة تفاعلياً مناسباً في الإنتاج، أو اترك DenyAll.
    approval_handler=AutoApprove(),
)
quota = QuotaGuard(max_tool_calls=20)
registry = build_default_registry(ToolContext(policy=policy, quota=quota))
loop = CognitiveLoop(
    registry,
    store=EvidenceStore(repo / ".agent_cognitive" / "events.jsonl"),
    checkpoints=CheckpointStore(repo / ".agent_cognitive" / "checkpoints"),
    memory=MemoryStore(),
)
goal = Goal.for_repair(
    "أصلح اختبار الجمع",
    test_command="pytest -q test_app.py",
    target_path="app.py",
)
plan = RepairPlan(steps=[
    RepairAction(path="app.py", old="return a - b", new="return a + b")
])

result = asyncio.run(loop.repair(goal, plan, run_id="repair-001"))
print(result.verified)
print(result.report)

# بعد إعادة بناء CognitiveLoop على المخزنين نفسيهما:
# resumed = asyncio.run(loop.resume("repair-001"))
```

`CognitiveLoop` **لا يولّد هذه الخطة من عيب مجهول**. يمكن حقن `replanner` موثوق يعيد `RepairPlan` بعد فشل القياس؛ سيُستدعى مرة واحدة كحد أقصى، وتُراجع خطته ومساراتها وميزانيتها بالطريقة نفسها.

## 5) الاستمرارية وسلامة الدليل

- كل انتقال يحمل صورتي الحالة وبصمتيهما، وكل حدث يشمل بصمة الحدث السابق.
- إعادة تحميل JSONL ترفض تعديل الحمولة أو كسر الترتيب أو السلسلة.
- ملف checkpoint لا يُقبل إذا تغيّر محتواه دون تحديث بصمته، ويجب أن يكون `event_head` موجوداً في السلسلة الحالية.
- الكتابة الذرّية لـcheckpoint تتم بملف مؤقت ثم `os.replace`؛ أحداث السجل تُفرّغ بـ`flush + fsync`.
- الاستئناف يبدأ من **آخر checkpoint محفوظ**. كما في أي نظام ملفات، الأثر الذي حدث في فجوة الانهيار قبل حفظ checkpoint قد يحتاج مصالحة؛ لا تدّعي هذه الطبقة exactly-once transaction موزعة.
- السلسلة تكشف التعديل الجزئي والحذف وإعادة الترتيب. ليست مقاومة لخصم يستطيع إعادة كتابة السجل والـcheckpoint وكل البصمات من البداية؛ لا يوجد توقيع خارجي أو ختم زمني موثوق في هذه المرحلة.

## 6) حدود صريحة وما لم يُبنَ

- لا تشخيص تلقائي عام للشيفرة، ولا ضمان أن الخطة المُدخلة صحيحة؛ الاختبار هو الذي يفصل.
- لا استئناف لخطوات `Planner` القديمة داخل `AgentEngine`: ربط المحرك العام يحفظ بداية/نهاية التشغيل، بينما الاستئناف على مستوى الأفعال يخص `CognitiveLoop` وreceipts.
- لا تعدد وكلاء، ولا تحديث أوزان؛ «التعلّم» هنا دروس `MemoryStore` فقط.
- لا حساب Brier/ECE بعد؛ رقم الثقة مؤشر أدلة داخلي، وليس احتمال نجاح مُعايَراً.
- لقطة العالم محدودة بالمسارات المعلنة وأوامر التحقق؛ ليست فهماً شاملاً للبيئة.
- `.agent_cognitive/` مستثنى من Git، لكنه قد يحتوي نصوص المهمة والمخرجات؛ تعامل معه كبيانات تشغيل حساسة.

## 7) التحقق المنفّذ في هذه البيئة

```text
41 اختباراً جديداً في tests/test_cognitive*.py: ناجحة
المجموعة الكاملة: 366 passed, 4 skipped
python -m compileall -q src tests: ناجح
ruff check .: ناجح
```

التخطي يخص اختبارات Qiskit الاختيارية عند غياب الحزمة؛ لا يوجد تخطٍّ داخل اختبارات القبول الإدراكية.
