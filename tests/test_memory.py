"""
اختبارات الذاكرة الاستراتيجية (Strategic Memory)
================================================
تتحقق من: التطبيع العربي، الترتيب، حدود الحجم، الاستمرارية على القرص،
التسامح مع الملفات التالفة، الربط بمحرك التخطيط، ووسائط CLI — بلا أي شبكة.
"""
import asyncio
from pathlib import Path

import pytest

from src.core.memory import (
    MAX_CONTENT_CHARS,
    MemoryRetriever,
    MemoryStore,
    build_context_block,
    tokenize,
)
from src.core.schemas import MemoryKind, StepRecord, StepStatus

# ================= التطبيع والتقطيع =================

def test_tokenize_normalizes_arabic_variants():
    """تطبيع الألف/الياء/التاء المربوطة والتشكيل يجعل الصيغ المختلفة كلمة واحدة."""
    assert tokenize("أحمد") == tokenize("احمد") == ["احمد"]
    assert tokenize("المهمّة") == tokenize("المهمه") == ["مهم"]
    assert "تكاليف" in tokenize("التكاليف")


def test_tokenize_drops_stopwords_and_short_tokens():
    """كلمات الوقف والرموز لا تدخل الفهرس — وإلا طغت على التشابه."""
    assert tokenize("من في هذا ذلك إلى الى أي") == []
    assert tokenize("!!! ،،، ...") == []
    assert tokenize("") == []


# ================= التخزين الأساسي =================

def test_memory_deduplicates_identical_entries():
    """نفس المحتوى ونفس النوع لا يُخزَّن مرتين؛ والنص الفارغ مرفوض."""
    store = MemoryStore()
    assert store.add(content="درس مهم عن المشروع", kind=MemoryKind.LESSON) is not None
    assert store.add(content="  درس مهم   عن المشروع ", kind=MemoryKind.LESSON) is None
    assert store.add(content="   ") is None
    assert len(store) == 1

    # نفس النص بنوع مختلف ذكرى مستقلة (الدرس والفشل لهما قيمة مختلفة)
    assert store.add(content="درس مهم عن المشروع", kind=MemoryKind.FAILURE) is not None
    assert len(store) == 2


def test_memory_truncates_oversized_content():
    """المحتوى الطويل يُقتطع — حماية لحجم الـ prompt والملف."""
    store = MemoryStore()
    entry = store.add(content="ا" * (MAX_CONTENT_CHARS + 500))
    assert entry is not None
    assert len(entry.content) == MAX_CONTENT_CHARS


def test_memory_step_record_mapping():
    """سجل الخطوة يُحوَّل لنوع صحيح: نجاح ← SUCCESS، استنفاد ← FAILURE."""
    store = MemoryStore()

    done = StepRecord(index=1, description="جمع المصادر", status=StepStatus.DONE, result="3 مصادر", score=9)
    failed = StepRecord(
        index=2,
        description="تحليل البيانات",
        status=StepStatus.FAILED,
        attempts=2,
        score=3,
        critiques=["[3/10] ناقص"],
    )

    assert store.add_step_record(done, task="مهمة").kind is MemoryKind.SUCCESS
    failure_entry = store.add_step_record(failed, task="مهمة")
    assert failure_entry.kind is MemoryKind.FAILURE
    assert "3/10" in failure_entry.content
    assert failure_entry.score == 3

    # خطوة قيد التنفيذ لا تُخزَّن
    assert store.add_step_record(StepRecord(index=3, description="قيد العمل")) is None


# ================= الاسترجاع والترتيب =================

@pytest.fixture()
def seeded_store() -> MemoryStore:
    store = MemoryStore()
    store.add(
        content="تحليل تكاليف المشروع أظهر أن أعلى بند هو الاستضافة الشهرية",
        kind=MemoryKind.SUCCESS,
        task="تحليل تكاليف مشروعي",
        score=8,
    )
    store.add(
        content="فشلت خطوة جلب أسعار الذهب لأن المصدر حجب الطلبات المتكررة",
        kind=MemoryKind.FAILURE,
        task="سعر الذهب اليوم",
        score=3,
    )
    store.add(content="وصفة كعك بالشوكولاتة سهلة وسريعة", kind=MemoryKind.FACT, task="وصفة طبخ")
    return store


def test_search_returns_most_relevant_first(seeded_store):
    """الاستعلام يُطابق الذكرى الصحيحة، والنتائج مرتبة تنازلياً بالدرجة."""
    gold = seeded_store.search("ما سعر الذهب اليوم", top_k=3)
    assert gold and "الذهب" in gold[0].entry.content
    assert gold[0].entry.kind is MemoryKind.FAILURE
    assert gold[0].matched  # تفكيك المطابقة متاح للتدقيق
    assert [hit.score for hit in gold] == sorted((hit.score for hit in gold), reverse=True)

    costs = seeded_store.search("حلّل تكاليف مشروعي", top_k=3)
    assert costs and "تكاليف" in costs[0].entry.content


def test_search_excludes_unrelated_queries(seeded_store):
    """استعلام بلا أي تطابق معجمي لا يُعيد شيئاً (لا حقن ذاكرة عشوائي)."""
    assert seeded_store.search("الطقس في طوكيو غداً") == []
    assert seeded_store.search("") == []
    assert seeded_store.search("   ") == []


def test_search_respects_top_k_and_kind_filter(seeded_store):
    """حد top_k مطبَّق، ويمكن حصر الاسترجاع في نوع واحد."""
    all_hits = seeded_store.search("تحليل المشروع والذهب والكعك", top_k=1)
    assert len(all_hits) == 1

    only_failures = seeded_store.search("المشروع الذهب الكعك", top_k=5, kinds=[MemoryKind.FAILURE])
    assert [hit.entry.kind for hit in only_failures] == [MemoryKind.FAILURE]


def test_lesson_outweighs_fact_for_same_text():
    """نفس المحتوى: الدرس يتقدّم على المعلومة — أولوية التعلّم من التجربة."""
    store = MemoryStore()
    store.add(content="مصدر أسعار الذهب يحجب الطلبات المتكررة", kind=MemoryKind.FACT)
    store.add(content="مصدر أسعار الذهب يحجب الطلبات المتكررة", kind=MemoryKind.LESSON)

    hits = store.search("أسعار الذهب")
    assert [hit.entry.kind for hit in hits] == [MemoryKind.LESSON, MemoryKind.FACT]


# ================= كتلة الحقن =================

def test_context_block_is_bounded_and_labelled(seeded_store):
    """كتلة الحقن تحمل العنوان والنوع والتقييم ولا تتجاوز الحد المطلوب."""
    hits = seeded_store.search("تكاليف المشروع", top_k=2)
    block = build_context_block(hits, max_chars=300)
    assert "ذاكرة استراتيجية" in block
    assert "[success]" in block
    assert len(block) <= 300

    # حتى لو كانت أول ذكرى أطول من الحد تماماً
    long_entry = MemoryStore()
    long_entry.add(content="طويل " * 200)
    tiny = build_context_block(long_entry.search("طويل"), max_chars=80)
    assert len(tiny) <= 80


def test_context_block_empty_when_no_hits(seeded_store):
    """لا نتائج ⇒ نص فارغ تماماً (لا كتلة ذاكرة بلا محتوى في الـ prompt)."""
    assert build_context_block([]) == ""
    assert seeded_store.context_for("الطقس في طوكيو") == ""


# ================= الاستمرارية على القرص =================

def test_memory_persists_across_instances(tmp_path: Path):
    """الذكريات تبقى بعد إعادة التشغيل — جوهر الذاكرة طويلة المدى."""
    path = tmp_path / "nested" / "memory.jsonl"

    first = MemoryStore(path=path)
    first.add(content="درس مستفاد من الجلسة الأولى", kind=MemoryKind.LESSON, task="مهمة", score=7)
    first.add(content="خطوة ناجحة في تحليل البيانات", kind=MemoryKind.SUCCESS, score=9)
    assert path.exists()

    second = MemoryStore(path=path)
    assert len(second) == 2
    kinds = {entry.kind for entry in second.all()}
    assert kinds == {MemoryKind.LESSON, MemoryKind.SUCCESS}
    assert second.all()[0].score == 7


def test_memory_load_tolerates_corrupt_lines(tmp_path: Path):
    """سطر تالف أو كائن ناقص لا يُسقط الذاكرة كلها."""
    path = tmp_path / "memory.jsonl"
    path.write_text(
        '{"kind": "lesson", "content": "ذكرى سليمة"}\n'
        "هذا ليس JSON\n"
        "\n"
        '{"kind": "fact"}\n',
        encoding="utf-8",
    )

    store = MemoryStore(path=path)
    assert len(store) == 1
    assert store.all()[0].id  # البصمة أُعيد توليدها للسطر القديم
    assert store.stats()["skipped_lines"] == 2


def test_memory_max_entries_keeps_newest(tmp_path: Path):
    """الحد الأقصى يُبقي الأحدث فقط، والضغط يعكس ذلك على الملف."""
    path = tmp_path / "memory.jsonl"
    store = MemoryStore(path=path, max_entries=3)
    for index in range(6):
        store.add(content=f"ذكرى رقم {index}")

    assert len(store) == 3
    assert [entry.content for entry in store.all()] == ["ذكرى رقم 3", "ذكرى رقم 4", "ذكرى رقم 5"]

    store.maybe_compact()
    reloaded = MemoryStore(path=path, max_entries=3)
    assert [entry.content for entry in reloaded.all()] == [
        "ذكرى رقم 3",
        "ذكرى رقم 4",
        "ذكرى رقم 5",
    ]


def test_memory_clear_removes_entries_and_file(tmp_path: Path):
    """المسح عملية صريحة تحذف الملف أيضاً — لا بقايا على القرص."""
    path = tmp_path / "memory.jsonl"
    store = MemoryStore(path=path)
    store.add(content="ذكرى")
    assert path.exists()

    store.clear()
    assert len(store) == 0
    assert not path.exists()


def test_in_memory_store_never_touches_disk(tmp_path: Path, monkeypatch):
    """`:memory:` لا يكتب أي ملف — مهم للاختبارات ولبيئات القراءة فقط."""
    monkeypatch.chdir(tmp_path)
    store = MemoryStore(path=":memory:")
    store.add(content="ذكرى")
    assert store.path is None
    assert not (tmp_path / ".agent_memory").exists()


# ================= الربط بمحرك التخطيط =================

class ScriptedRouter:
    """موجّه وهمي (مطابق لعقد LLMRouter) بلا أي اتصال شبكي."""

    def __init__(self, text_responses=None):
        from src.config import Settings

        self.text_responses = list(text_responses or [])
        self.prompts: list[str] = []
        self.settings = Settings(_env_file=None)

    async def execute_reasoning_step(self, prompt: str) -> str:
        self.prompts.append(prompt)
        return self.text_responses.pop(0) if self.text_responses else "FINAL ANSWER: رد افتراضي"

    async def execute_structured_step(self, prompt: str, output_format):
        self.prompts.append(prompt)


def test_planner_injects_retrieved_memory_into_prompts():
    """الذكرى ذات الصلة تصل فعلياً إلى prompt المخطّط والمنفّذ."""
    from src.core.planner import Planner

    store = MemoryStore()
    store.add(
        content="مصدر أسعار الذهب يحجب الطلبات المتكررة — استخدم مصدراً بديلاً",
        kind=MemoryKind.LESSON,
        task="سعر الذهب",
        score=9,
    )

    router = ScriptedRouter(
        [
            '{"steps": ["اجلب السعر من مصدر بديل"]}',
            "السعر 2400 دولار",
            '{"passed": true, "score": 9, "feedback": "جيد"}',
            "FINAL ANSWER: السعر 2400 دولار",
        ]
    )
    planner = Planner(router=router, memory=store, remember=False)
    answer = asyncio.run(planner.solve("ما سعر الذهب اليوم؟"))

    assert answer == "السعر 2400 دولار"
    assert planner.last_hits, "لم تُسترجع أي ذكرى رغم وجود تطابق واضح"
    assert any("يحجب الطلبات المتكررة" in prompt for prompt in router.prompts)
    # الحقن يشمل المخطّط (أول prompt) والمنفّذ
    assert "يحجب الطلبات المتكررة" in router.prompts[0]


def test_planner_records_run_into_memory():
    """بعد المهمة: كل خطوة ناجحة تُحفظ + خلاصة المهمة (نوع SUCCESS)."""
    from src.core.planner import Planner

    store = MemoryStore()
    router = ScriptedRouter(
        [
            '{"steps": ["اجمع البيانات", "حلّل البيانات"]}',
            "بيانات خام",
            '{"passed": true, "score": 8, "feedback": "جيد"}',
            "تحليل نهائي",
            '{"passed": true, "score": 9, "feedback": "ممتاز"}',
            "FINAL ANSWER: خلاصة المهمة",
        ]
    )
    planner = Planner(router=router, memory=store)
    asyncio.run(planner.solve("حلّل بيانات المبيعات"))

    entries = store.all()
    assert len(entries) == 3  # خطوتان + خلاصة
    assert all(entry.kind is MemoryKind.SUCCESS for entry in entries)
    assert "خلاصة المهمة" in entries[-1].content
    assert "اجمع البيانات" in entries[0].content
    assert entries[0].task == "حلّل بيانات المبيعات"


def test_planner_records_failure_as_lesson():
    """فشل خطوة يُحفظ كـ FAILURE، وخلاصة المهمة تُصنَّف LESSON (درس مستفاد)."""
    from src.core.planner import Planner

    store = MemoryStore()
    router = ScriptedRouter(
        [
            '{"steps": ["خطوة صعبة"]}',
            "محاولة 1",
            '{"passed": false, "score": 2, "feedback": "ضعيف", "suggestion": "حسّن"}',
            "محاولة 2",
            '{"passed": false, "score": 3, "feedback": "لا يزال ضعيفاً", "suggestion": "حسّن"}',
            "FINAL ANSWER: ملخص يذكر الفشل",
        ]
    )
    planner = Planner(router=router, memory=store, max_retries=2)
    asyncio.run(planner.solve("مهمة صعبة"))

    kinds = [entry.kind for entry in store.all()]
    assert MemoryKind.FAILURE in kinds
    assert kinds[-1] is MemoryKind.LESSON
    failure_entry = next(entry for entry in store.all() if entry.kind is MemoryKind.FAILURE)
    assert failure_entry.score == 3
    assert "خطوة صعبة" in failure_entry.content
    assert "التصحيح المقترح: حسّن" in failure_entry.content


def test_planner_remembers_across_runs(tmp_path: Path):
    """الدورة الكاملة: مهمة أولى تُعلّم الوكيل، والمهمة الثانية ترى الدرس."""
    from src.core.planner import Planner

    path = tmp_path / "memory.jsonl"
    first_router = ScriptedRouter(
        [
            '{"steps": ["خطوة واحدة"]}',
            "نتيجة أولى",
            '{"passed": false, "score": 2, "feedback": "ناقص", "suggestion": "أضف مصادر"}',
            "نتيجة أولى",
            '{"passed": false, "score": 2, "feedback": "ناقص", "suggestion": "أضف مصادر"}',
            "FINAL ANSWER: محاولة غير مكتملة",
        ]
    )
    asyncio.run(Planner(router=first_router, memory=MemoryStore(path=path), max_retries=2).solve("تحليل السوق"))

    second_router = ScriptedRouter(
        [
            '{"steps": ["حلّل السوق بمصادر"]}',
            "تحليل مدعوم بالمصادر",
            '{"passed": true, "score": 9, "feedback": "جيد"}',
            "FINAL ANSWER: التحليل",
        ]
    )
    planner = Planner(router=second_router, memory=MemoryStore(path=path), remember=False)
    asyncio.run(planner.solve("تحليل السوق"))

    assert planner.last_hits, "المهمة الثانية لم تسترجع درس المهمة الأولى"
    assert any("أضف مصادر" in prompt for prompt in second_router.prompts)


def test_planner_survives_broken_memory():
    """أي عطل في الذاكرة لا يُسقط المهمة — الذاكرة مساعدة لا حرجة."""
    from src.core.planner import Planner

    class BrokenStore(MemoryStore):
        def search(self, *args, **kwargs):
            raise RuntimeError("فشل القرص")

    router = ScriptedRouter(
        [
            '{"steps": ["خطوة"]}',
            "نتيجة",
            '{"passed": true, "score": 9, "feedback": "جيد"}',
            "FINAL ANSWER: تم رغم العطل",
        ]
    )
    planner = Planner(router=router, memory=BrokenStore())
    answer = asyncio.run(planner.solve("مهمة"))

    assert answer == "تم رغم العطل"
    assert planner.last_hits == []


def test_planner_without_memory_has_no_retriever():
    """الافتراضي: لا ذاكرة ⇒ لا قراءة ولا كتابة على القرص."""
    from src.core.planner import Planner

    planner = Planner(router=ScriptedRouter(), remember=True)
    assert planner.memory is None
    assert planner.retriever is None


def test_retriever_exposes_store_for_writes(tmp_path: Path):
    """تمرير retriever (لا store) يبقى قابلاً للكتابة عبر `.store`."""
    from src.core.planner import Planner

    store = MemoryStore(path=tmp_path / "m.jsonl")
    router = ScriptedRouter(
        [
            '{"steps": ["خطوة"]}',
            "نتيجة",
            '{"passed": true, "score": 9, "feedback": "جيد"}',
            "FINAL ANSWER: تم",
        ]
    )
    planner = Planner(router=router, memory=MemoryRetriever(store))
    asyncio.run(planner.solve("مهمة"))

    assert planner.memory is store
    assert len(store) == 2


# ================= واجهة CLI =================

def test_cli_memory_flags_parse():
    """وسائط الذاكرة معرّفة فعلاً — يمنع AttributeError وقت التشغيل."""
    from src.main import build_parser

    args = build_parser().parse_args(
        ["--task", "مهمة", "--no-memory", "--memory-path", "/tmp/m.jsonl", "--memory-top-k", "2"]
    )
    assert args.no_memory is True
    assert args.memory_path == "/tmp/m.jsonl"
    assert args.memory_top_k == 2

    defaults = build_parser().parse_args(["--task", "مهمة"])
    assert defaults.no_memory is False
    assert defaults.memory_path is None
    assert defaults.memory_top_k is None


def test_build_memory_respects_flags(tmp_path: Path):
    """`--no-memory` يعطّل كل شيء، والمسار الممرَّر يُستخدم فعلاً."""
    from src.main import build_memory, build_parser

    disabled = build_parser().parse_args(["--task", "م", "--no-memory"])
    assert build_memory(disabled) is None

    path = tmp_path / "custom.jsonl"
    enabled = build_parser().parse_args(
        ["--task", "م", "--memory-path", str(path), "--memory-top-k", "3"]
    )
    retriever = build_memory(enabled)
    assert isinstance(retriever, MemoryRetriever)
    assert retriever.store.path == path
    assert retriever.top_k == 3
