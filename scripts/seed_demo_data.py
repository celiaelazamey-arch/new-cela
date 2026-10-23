"""
توليد بيانات عرض للوحة المتابعة (Demo Seed)
===========================================
يُنشئ ذاكرة استراتيجية وسجل تشغيلات **واقعيين** عبر نفس الوحدات الحقيقية
(`MemoryStore` + `RunArchive`)، حتى تُجرَّب اللوحة بلا مفاتيح API ولا شبكة.

    python scripts/seed_demo_data.py
    python -m src.dashboard --runs-dir .agent_runs/demo --memory .agent_memory/demo/memory.jsonl

⚠️ البيانات توضيحية فقط: تُكتب داخل مجلدات `demo/` ولا تمسّ سجلك الحقيقي.
"""
from __future__ import annotations

import argparse
import sys
from pathlib import Path

# السماح بالتشغيل المباشر: python scripts/seed_demo_data.py
_REPO_ROOT = Path(__file__).resolve().parents[1]
if str(_REPO_ROOT) not in sys.path:
    sys.path.insert(0, str(_REPO_ROOT))

from src.core.memory import MemoryStore
from src.core.schemas import MemoryKind, StepRecord, StepStatus
from src.core.telemetry import RunArchive

MEMORIES: list[dict] = [
    {
        "content": "الاستضافة الشهرية أعلى بند في تكاليف المشروع — راجع الخطة قبل التوسّع",
        "kind": MemoryKind.LESSON,
        "task": "حلّل تكاليف مشروعي",
        "score": 9,
    },
    {
        "content": (
            "فشلت خطوة جلب أسعار الذهب بعد محاولتين: المصدر يحجب الطلبات المتكررة "
            "— التصحيح المقترح: استخدم مصدراً بديلاً مع تأخير بين الطلبات"
        ),
        "kind": MemoryKind.FAILURE,
        "task": "ما سعر الذهب اليوم",
        "score": 3,
    },
    {
        "content": "تقسيم التحليل إلى خطوات صغيرة رفع جودة النتيجة من 6 إلى 9",
        "kind": MemoryKind.SUCCESS,
        "task": "حلّل بيانات المبيعات",
        "score": 9,
    },
    {
        "content": "عند تحليل السوق: ابدأ بالمصادر المحلية قبل البحث على الويب لتوفير الوقت",
        "kind": MemoryKind.LESSON,
        "task": "تحليل السوق",
        "score": 8,
    },
    {
        "content": "مهمة «تحليل السوق»: نجحت 2/3 خطوة. الخلاصة: التحليل مكتمل ناقص مصدر واحد",
        "kind": MemoryKind.LESSON,
        "task": "تحليل السوق",
        "score": 7,
    },
    {
        "content": "python 3.11 هو الإصدار المتاح في بيئة التشغيل الحالية",
        "kind": MemoryKind.FACT,
        "task": "تحقّق من البيئة",
        "score": 0,
    },
]


def _seed_success_run(memory: MemoryStore, archive: RunArchive) -> None:
    """تشغيل ناجح بالكامل استخدم الذاكرة فعلاً."""
    query = "حلّل تكاليف المشروع واقترح خطوات التوفير"
    session = archive.start(query)
    session.record_recall(memory.search(query, top_k=3))
    session.set_planned_steps(3)

    session.record_step(
        StepRecord(
            index=1,
            description="اجمع بنود التكلفة الشهرية من الملفات المحلية",
            status=StepStatus.DONE,
            result="تم جمع 12 بنداً: استضافة، نطاق، أدوات، اشتراكات، تسويق…",
            attempts=1,
            score=9,
            critiques=["[9/10] جمع شامل ومنظّم"],
        )
    )
    session.record_step(
        StepRecord(
            index=2,
            description="رتّب البنود تنازلياً حسب الأثر المالي",
            status=StepStatus.DONE,
            result="الاستضافة 41%، التسويق 22%، الأدوات 15%، اشتراكات 12%، أخرى 10%",
            attempts=2,
            score=8,
            critiques=["[6/10] ترتيب أولي بلا نسب", "[8/10] أُضيفت النسب"],
            suggestion="أضف النسب لكل بند",
        )
    )
    session.record_step(
        StepRecord(
            index=3,
            description="اقترح خطوات توفير ملموسة",
            status=StepStatus.DONE,
            result=(
                "3 خطوات: خطة استضافة سنوية (−18%)، توحيد الاشتراكات (−9%)، "
                "مراجعة الأدوات ربع سنوياً"
            ),
            attempts=1,
            score=9,
            critiques=["[9/10] اقتراحات قابلة للقياس"],
        )
    )
    session.set_memories_written(3)
    session.finish(
        "تحليل التكاليف: أعلى بند هو الاستضافة (41%). أبرز خطوة توفير: التحوّل لخطة "
        "سنوية يوفّر ~18%، ثم توحيد الاشتراكات (~9%)."
    )


def _seed_partial_failure_run(memory: MemoryStore, archive: RunArchive) -> None:
    """تشغيل فشل في خطوة بعد استنفاد المحاولات — يُظهر الدرس التصحيحي."""
    query = "ما سعر الذهب اليوم في القاهرة"
    session = archive.start(query)
    session.record_recall(memory.search(query, top_k=3))
    session.set_planned_steps(2)

    session.record_step(
        StepRecord(
            index=1,
            description="ابحث عن سعر الذهب من مصدر موثوق",
            status=StepStatus.FAILED,
            result="تعذّر الوصول: 429 Too Many Requests من المصدر",
            attempts=3,
            score=3,
            critiques=["[4/10] لا يوجد سعر", "[3/10] نفس المصدر", "[3/10] حجب متكرر"],
            suggestion="استخدم مصدراً بديلاً مع تأخير بين الطلبات",
        )
    )
    session.record_step(
        StepRecord(
            index=2,
            description="احسب السعر بالجنيه المصري",
            status=StepStatus.PENDING,
            attempts=0,
        )
    )
    session.set_memories_written(2)
    session.finish(
        "لم أتمكّن من جلب سعر الذهب: المصدر حجب الطلبات المتكررة. "
        "الدرس المسجَّل: استخدم مصدراً بديلاً مع تأخير بين الطلبات."
    )


def _seed_running_run(memory: MemoryStore, archive: RunArchive) -> None:
    """تشغيل ما زال «قيد التنفيذ» — لعرض المتابعة الحية للتقدّم."""
    session = archive.start("حلّل بيانات المبيعات للربع الأخير")
    session.record_recall(memory.search("تحليل بيانات المبيعات", top_k=2))
    session.set_planned_steps(4)

    session.record_step(
        StepRecord(
            index=1,
            description="اقرأ ملف المبيعات وافحص جودة البيانات",
            status=StepStatus.DONE,
            result="1,842 صفاً، 3 قيم مفقودة في عمود المنطقة",
            attempts=1,
            score=8,
            critiques=["[8/10] جودة مقبولة مع ملاحظة"],
        )
    )
    session.record_step(
        StepRecord(
            index=2,
            description="احسب الإيراد حسب المنطقة والمنتج",
            status=StepStatus.IN_PROGRESS,
            attempts=1,
        )
    )


def seed(memory_path: Path, runs_dir: Path) -> tuple[int, int]:
    """يولّد البيانات ويعيد (عدد الذكريات، عدد التشغيلات)."""
    memory = MemoryStore(path=memory_path)
    memory.clear()
    for item in MEMORIES:
        memory.add(**item)

    archive = RunArchive(path=runs_dir, keep=50)
    for stale in runs_dir.glob("*.json"):
        stale.unlink(missing_ok=True)

    _seed_success_run(memory, archive)
    _seed_partial_failure_run(memory, archive)
    _seed_running_run(memory, archive)

    return len(memory), 3


def main() -> int:
    parser = argparse.ArgumentParser(description="توليد بيانات عرض للوحة المتابعة")
    parser.add_argument("--memory-path", default=".agent_memory/demo/memory.jsonl")
    parser.add_argument("--runs-dir", default=".agent_runs/demo")
    args = parser.parse_args()

    memory_path = Path(args.memory_path)
    runs_dir = Path(args.runs_dir)
    memories, runs = seed(memory_path, runs_dir)

    print(f"✅ {memories} ذكرى في {memory_path}")
    print(f"✅ {runs} تشغيلاً في {runs_dir}")
    print("\nللعرض:")
    print(f"  python -m src.dashboard --runs-dir {runs_dir} --memory {memory_path}")
    print("  (أو عبر متغيرات البيئة: AGENT_RUNS_PATH / AGENT_MEMORY_PATH)")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
