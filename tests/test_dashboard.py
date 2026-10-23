"""
اختبارات لوحة المتابعة وسجل التشغيلات
=====================================
ثلاث طبقات:
  1. `telemetry` — الكتابة الذرّية، التنقيح، الحياة، الفشل، التقليم.
  2. `dashboard.data` — القراءة والتلخيص والاستعلام (بلا واجهة).
  3. `dashboard.app` — تشغيل الواجهة فعلياً عبر `streamlit.testing.AppTest`.

الاختبارات لا تحتاج شبكة ولا مفاتيح، وتكتب فقط في `tmp_path`.
"""
import asyncio
import json
from pathlib import Path

import pytest

from src.core.schemas import (
    MemoryKind,
    RunReport,
    RunStatus,
    StepRecord,
    StepStatus,
)
from src.core.telemetry import REDACTED, RunArchive, redact

# ================= أدوات مساعدة =================

class ScriptedRouter:
    """موجّه وهمي يرد بردود مبرمجة — بلا شبكة (نفس عقد LLMRouter)."""

    def __init__(self, text_responses=None, fail_at: int | None = None):
        from src.config import Settings

        self.text_responses = list(text_responses or [])
        self.fail_at = fail_at
        self.calls = 0
        self.settings = Settings(_env_file=None)

    async def execute_reasoning_step(self, prompt: str) -> str:
        self.calls += 1
        if self.fail_at is not None and self.calls >= self.fail_at:
            raise RuntimeError("كل المزودات فشلت: gemini:Error")
        return self.text_responses.pop(0) if self.text_responses else "FINAL ANSWER: رد"

    async def execute_structured_step(self, prompt: str, output_format):
        return None


HAPPY_PATH = [
    '{"steps": ["اجمع البيانات", "حلّل البيانات"]}',
    "بيانات خام من ثلاثة مصادر",
    '{"passed": true, "score": 8, "feedback": "جيد"}',
    "تحليل مدعوم بالأرقام",
    '{"passed": true, "score": 9, "feedback": "ممتاز"}',
    "FINAL ANSWER: الإجابة النهائية",
]


def make_report(**overrides) -> RunReport:
    base = {
        "id": "20260101T000000000000Z-abcdef",
        "task": "مهمة اختبار",
        "status": RunStatus.COMPLETED,
        "started_at": "2026-01-01T00:00:00+00:00",
        "finished_at": "2026-01-01T00:01:00+00:00",
        "duration_seconds": 60,
        "planned_steps": 2,
        "answer": "إجابة",
    }
    base.update(overrides)
    return RunReport(**base)


# ================= 1) التتبع (Telemetry) =================

def test_redact_hides_known_key_formats():
    """المفاتيح الشائعة تُحجب قبل الكتابة — لا أسرار في السجل."""
    samples = [
        "AIzaSyD-1234567890abcdefghijklmnopqrstu",
        "gsk_abcdefghijklmnopqrstuvwxyz123456",
        "sk-or-v1-abcdef1234567890abcdef",
        "Authorization: Bearer abcdef1234567890",
        "api_key=supersecretvalue",
        "password: hunter2hunter2",
    ]
    for sample in samples:
        cleaned = redact(f"قبل {sample} بعد")
        assert REDACTED in cleaned, sample
        assert sample not in cleaned, sample

    # النصوص العادية لا تُمس
    assert redact("تحليل تكاليف المشروع") == "تحليل تكاليف المشروع"
    assert redact("") == ""


def test_archive_writes_report_progressively(tmp_path: Path):
    """التقرير يُكتب بعد كل تحديث — أساس المتابعة الحية."""
    archive = RunArchive(path=tmp_path / "runs")
    session = archive.start("مهمة حية")

    live = archive.load(session.report.id)
    assert live is not None
    assert live.status is RunStatus.RUNNING
    assert live.progress == 0.0

    session.set_planned_steps(2)
    session.record_step(
        StepRecord(
            index=1,
            description="اجمع البيانات",
            status=StepStatus.DONE,
            result="بيانات مقروءة من مصادر موثوقة",
            attempts=1,
            score=8,
            critiques=["[8/10] جيد"],
        )
    )

    live = archive.load(session.report.id)
    assert len(live.steps) == 1
    assert live.steps[0].status is StepStatus.DONE
    assert live.steps[0].result_preview.startswith("بيانات مقروءة")
    assert live.progress == 0.5
    assert live.average_score == 8.0


def test_session_finish_and_fail_close_reports(tmp_path: Path):
    """النجاح يملأ الإجابة، والفشل يسجّل السبب — كلاهما يُغلق التقرير."""
    archive = RunArchive(path=tmp_path / "runs")

    ok = archive.start("ناجحة")
    ok.set_planned_steps(1)
    report = ok.finish("الإجابة النهائية")
    assert report.status is RunStatus.COMPLETED
    assert report.answer == "الإجابة النهائية"
    assert report.finished_at
    assert report.duration_seconds >= 0

    bad = archive.start("فاشلة")
    failed = bad.fail(RuntimeError("انقطاع الشبكة"))
    assert failed.status is RunStatus.FAILED
    assert "RuntimeError" in failed.error
    assert "انقطاع الشبكة" in failed.error


def test_step_records_are_upserted_by_index(tmp_path: Path):
    """إعادة تسجيل نفس رقم الخطوة تُحدّث صفها بدل تكراره."""
    archive = RunArchive(path=tmp_path / "runs")
    session = archive.start("مهمة")
    session.record_step(StepRecord(index=1, description="خطوة", status=StepStatus.IN_PROGRESS))
    session.record_step(StepRecord(index=1, description="خطوة", status=StepStatus.DONE, score=7))

    report = archive.load(session.report.id)
    assert len(report.steps) == 1
    assert report.steps[0].status is StepStatus.DONE


def test_archive_is_tolerant_to_corrupt_files(tmp_path: Path):
    """ملف تالف لا يُسقط القراءة ولا يمنع عرض بقية التشغيلات."""
    archive = RunArchive(path=tmp_path / "runs")
    good = archive.start("سليمة")
    good.set_planned_steps(1)
    good.finish("تم")

    (tmp_path / "runs" / "broken.json").write_text("{ليس JSON", encoding="utf-8")
    (tmp_path / "runs" / "20250101T000000Z-zzzzzz.json").write_text(
        json.dumps({"id": "x"}), encoding="utf-8"
    )  # ناقص الحقول الإلزامية

    reports = archive.list_recent()
    assert [report.id for report in reports] == [good.report.id]
    assert archive.load("broken") is None


def test_archive_prunes_old_runs(tmp_path: Path):
    """الاحتفاظ بحد أقصى — السجل لا ينمو بلا نهاية."""
    archive = RunArchive(path=tmp_path / "runs", keep=2)
    ids = []
    for index in range(4):
        session = archive.start(f"مهمة {index}")
        session.finish("تم")
        ids.append(session.report.id)

    remaining = sorted(path.stem for path in (tmp_path / "runs").glob("*.json"))
    assert remaining == sorted(ids[-2:]), "التقليم لم يُبقِ أحدث تشغيلين"

    # والترتيب المعروض يطابق ترتيب الإنشاء (لا ترتيب عشوائي)
    assert [report.id for report in archive.list_recent()] == ids[-2:][::-1]


def test_planner_writes_report_end_to_end(tmp_path: Path):
    """التكامل الحقيقي: Planner يكتب تقريراً كاملاً بعد المهمة."""
    from src.core.planner import Planner

    archive = RunArchive(path=tmp_path / "runs")
    planner = Planner(router=ScriptedRouter(HAPPY_PATH), telemetry=archive, remember=False)
    asyncio.run(planner.solve("مهمة تكامل"))

    report = archive.latest()
    assert report is not None
    assert report.status is RunStatus.COMPLETED
    assert report.task == "مهمة تكامل"
    assert report.planned_steps == 2
    assert len(report.steps) == 2
    assert report.steps_done == 2
    assert report.average_score == 8.5
    assert report.answer == "الإجابة النهائية"


def test_planner_marks_report_failed_and_reraises(tmp_path: Path):
    """فشل المزود: التقرير يُغلق كفاشل، والاستثناء يُرفَع كما هو للمتصل."""
    from src.core.planner import Planner

    archive = RunArchive(path=tmp_path / "runs")
    planner = Planner(router=ScriptedRouter(HAPPY_PATH, fail_at=2), telemetry=archive)

    with pytest.raises(RuntimeError):
        asyncio.run(planner.solve("مهمة تفشل"))

    report = archive.latest()
    assert report is not None
    assert report.status is RunStatus.FAILED
    assert "RuntimeError" in report.error


def test_planner_records_memory_hits_and_writes(tmp_path: Path):
    """التقرير يوثّق الذاكرة المسترجعة والمكتوبة — شفافية كاملة للوحة."""
    from src.core.memory import MemoryStore
    from src.core.planner import Planner

    memory = MemoryStore()
    memory.add(
        content="مصادر البيانات الموثوقة: الملفات المحلية أولاً",
        kind=MemoryKind.LESSON,
        task="جمع البيانات",
        score=9,
    )
    archive = RunArchive(path=tmp_path / "runs")
    planner = Planner(
        router=ScriptedRouter(HAPPY_PATH), memory=memory, telemetry=archive
    )
    asyncio.run(planner.solve("اجمع البيانات وحلّلها"))

    report = archive.latest()
    assert report.memory_hits, "التقرير لم يوثّق الذاكرة المسترجعة"
    assert report.memory_hits[0].kind is MemoryKind.LESSON
    assert report.memory_hits[0].matched
    assert report.memories_written == 3  # خطوتان + خلاصة


def test_planner_without_telemetry_writes_nothing(tmp_path: Path, monkeypatch):
    """الافتراضي: بلا telemetry لا يُكتب أي ملف."""
    from src.core.planner import Planner

    monkeypatch.chdir(tmp_path)
    planner = Planner(router=ScriptedRouter(HAPPY_PATH), remember=False)
    asyncio.run(planner.solve("مهمة"))

    assert planner.telemetry is None
    assert not (tmp_path / ".agent_runs").exists()


# ================= 2) طبقة بيانات اللوحة =================

def test_load_and_summarize_run(tmp_path: Path):
    """القراءة والتلخيص يعطيان أرقاماً صحيحة جاهزة للعرض."""
    from src.dashboard import data

    archive = RunArchive(path=tmp_path / "runs")
    session = archive.start("حلّل بيانات المبيعات")
    session.set_planned_steps(2)
    session.record_step(
        StepRecord(index=1, description="اجمع", status=StepStatus.DONE, score=9, attempts=1)
    )
    session.record_step(
        StepRecord(
            index=2,
            description="حلّل",
            status=StepStatus.FAILED,
            score=4,
            attempts=2,
            critiques=["[4/10] ناقص"],
            suggestion="أضف مصادر",
        )
    )
    session.finish("خلاصة")

    runs_dir = tmp_path / "runs"
    reports = data.load_runs(runs_dir)
    assert len(reports) == 1

    summary = data.summarize(reports[0])
    assert summary["status"] == "completed"
    assert summary["steps_done"] == 1
    assert summary["steps_failed"] == 1
    assert summary["average_score"] == 6.5
    assert summary["progress"] == 1.0
    assert summary["memory_hits"] == 0
    assert summary["task"] == "حلّل بيانات المبيعات"

    rows = data.step_rows(reports[0])
    assert rows[0]["الحالة"].startswith("✅")
    assert rows[1]["الحالة"].startswith("❌")
    assert rows[1]["التصحيح المقترح"] == "أضف مصادر"


def test_aggregate_and_empty_states(tmp_path: Path):
    """مؤشرات عامة صحيحة، وحالة فارغة آمنة."""
    from src.dashboard import data

    assert data.load_runs(tmp_path / "missing") == []
    assert data.latest_run(tmp_path / "missing") is None
    empty = data.aggregate([])
    assert empty["total"] == 0 and empty["success_rate"] == 0.0

    stats = data.aggregate(
        [
            make_report(status=RunStatus.COMPLETED),
            make_report(id="b", status=RunStatus.FAILED),
        ]
    )
    assert stats["completed"] == 1
    assert stats["success_rate"] == 50.0


def test_stale_running_report_is_flagged():
    """تشغيل «قيد التنفيذ» قديم ≠ حيّ — يُعرض كمنقطع بدل انتظار أبدي."""
    from src.dashboard import data

    old = make_report(status=RunStatus.RUNNING, started_at="2020-01-01T00:00:00+00:00")
    assert data.status_of(old) == "stale"
    assert data.status_text(old) == "منقطع"

    fresh = make_report(status=RunStatus.RUNNING)
    fresh.started_at = data.format_time("2020-01-01T00:00:00+00:00")  # غير قابل للتحليل
    assert data.is_stale(fresh) is False  # بلا وقت صالح لا ندّعي الانقطاع


def test_memory_overview_and_search(tmp_path: Path):
    """اللوحة تستعلم نفس محرك الذاكرة الحقيقي وتُظهر تطابقاً صحيحاً."""
    from src.core.memory import MemoryStore
    from src.dashboard import data

    path = tmp_path / "memory.jsonl"
    store = MemoryStore(path=path)
    store.add(content="الاستضافة الشهرية أعلى بند في تكاليف المشروع", kind=MemoryKind.SUCCESS, score=8)
    store.add(content="فشل جلب أسعار الذهب بسبب حجب المصدر", kind=MemoryKind.FAILURE, score=3)

    overview = data.memory_overview(path)
    assert overview["total"] == 2
    assert overview["counts"][MemoryKind.SUCCESS] == 1
    assert overview["counts"][MemoryKind.FAILURE] == 1

    hits = data.search_memory(path, "تكاليف المشروع", top_k=5)
    assert hits and "تكاليف" in hits[0].entry.content
    rows = data.hit_rows(hits)
    assert rows[0]["النوع"].startswith("✅")

    # الاستعلام الفارغ لا يفشل ولا يعيد شيئاً
    assert data.search_memory(path, "   ") == []
    # مسار غير موجود ⇒ مخزن فارغ بلا استثناء
    assert data.memory_overview(tmp_path / "nope.jsonl")["total"] == 0

    exported = json.loads(data.memory_export(path))
    assert len(exported) == 2
    assert len(data.recent_entries(path, limit=1)) == 1


def test_format_helpers():
    from src.dashboard import data

    assert data.format_duration(0) == "0 ث"
    assert data.format_duration(59.4) == "59 ث"
    assert data.format_duration(192) == "3 د 12 ث"
    assert data.format_duration(3900) == "1 س 05 د"
    assert data.format_time("2026-01-01T10:30:00+00:00") == "2026-01-01 10:30 UTC"
    assert data.format_time("غير صالح") == "غير صالح"


def test_dashboard_launcher_command():
    """مشغّل اللوحة يبني وسائط صحيحة (بلا تشغيل خادم)."""
    from src.dashboard.__main__ import build_command

    command, args = build_command(["--port", "8700"])
    assert args.port == 8700
    assert args.address == "0.0.0.0"
    assert command[:2] == ["streamlit", "run"]
    assert "--server.port=8700" in command
    assert "--server.address=0.0.0.0" in command
    assert "--server.enableXsrfProtection=false" in command
    assert command[2].endswith("app.py")


def test_dashboard_launcher_sets_source_paths(monkeypatch, tmp_path: Path):
    """وسائط المشغّل تحوّل مسارات المصدر إلى متغيرات بيئة تقرأها الواجهة."""
    from src.dashboard.__main__ import build_command

    monkeypatch.delenv("AGENT_RUNS_PATH", raising=False)
    monkeypatch.delenv("AGENT_MEMORY_PATH", raising=False)

    build_command(
        ["--port", "8701", "--runs-dir", str(tmp_path / "runs"), "--memory", str(tmp_path / "m.jsonl")]
    )
    import os

    assert os.environ["AGENT_RUNS_PATH"] == str(tmp_path / "runs")
    assert os.environ["AGENT_MEMORY_PATH"] == str(tmp_path / "m.jsonl")


# ================= 3) ابتلاع الواجهة فعلياً =================

def _frames(at):
    """يمرّر كل الجداول المعروضة كـ DataFrame حقيقي (لا نص مقتطع)."""
    return [frame.value for frame in at.dataframe if hasattr(frame.value, "columns")]


def _run_app(monkeypatch, runs_dir: Path, memory_path: Path):
    """يشغّل app.py داخل Streamlit TestRunner مع إعدادات معزولة."""
    from streamlit.testing.v1 import AppTest

    monkeypatch.setenv("AGENT_RUNS_PATH", str(runs_dir))
    monkeypatch.setenv("AGENT_MEMORY_PATH", str(memory_path))
    monkeypatch.setenv("GEMINI_API_KEY", "")
    monkeypatch.setenv("GROQ_API_KEY", "")

    app_path = Path(__file__).resolve().parents[1] / "src" / "dashboard" / "app.py"
    at = AppTest.from_file(str(app_path), default_timeout=30)
    at.run()
    return at


def test_app_renders_title_and_empty_state(monkeypatch, tmp_path: Path):
    """الصفحة تُبنى بلا تشغيلات: عنوان + رسالة حالة فارغة، وبلا استثناءات."""
    at = _run_app(monkeypatch, tmp_path / "runs", tmp_path / "memory.jsonl")
    assert not at.exception
    assert [title.value for title in at.title] == ["🛰️ لوحة متابعة new-cela"]
    assert len(at.tabs) == 3
    assert any("لا يوجد أي تشغيل مسجّل" in info.value for info in at.info)


def test_app_renders_real_run_and_memory(monkeypatch, tmp_path: Path):
    """مع سجل حقيقي وذاكرة حقيقية: الجداول والمؤشرات تظهر بلا أخطاء."""
    from src.core.memory import MemoryStore

    runs_dir = tmp_path / "runs"
    memory_path = tmp_path / "memory.jsonl"

    store = MemoryStore(path=memory_path)
    store.add(content="الاستضافة الشهرية أعلى بند في تكاليف المشروع", kind=MemoryKind.LESSON, score=9)

    archive = RunArchive(path=runs_dir)
    session = archive.start("حلّل تكاليف المشروع")
    session.set_planned_steps(2)
    session.record_recall(store.search("تكاليف المشروع", top_k=2))
    session.record_step(
        StepRecord(
            index=1,
            description="اجمع بنود التكلفة",
            status=StepStatus.DONE,
            score=9,
            attempts=1,
            result="بنود مجمّعة",
        )
    )
    session.record_step(
        StepRecord(index=2, description="حلّل", status=StepStatus.FAILED, attempts=2, score=3)
    )
    session.set_memories_written(2)
    session.finish("الخلاصة النهائية")

    at = _run_app(monkeypatch, runs_dir, memory_path)
    assert not at.exception
    assert at.metric, "لم تُعرَض أي مؤشرات"
    labels = [metric.label for metric in at.metric]
    assert "الحالة" in labels
    assert "متوسط التقييم" in labels
    assert len(at.dataframe) >= 1
    assert any(
        "الاستضافة الشهرية" in str(frame.to_string()) for frame in _frames(at)
    ), "الذكرى المسترجعة لم تظهر في تقرير التشغيل"


def test_app_memory_tab_search_interaction(monkeypatch, tmp_path: Path):
    """إدخال استعلام في الواجهة يعرض نتائج حقيقية من الذاكرة."""
    from src.core.memory import MemoryStore

    memory_path = tmp_path / "memory.jsonl"
    store = MemoryStore(path=memory_path)
    store.add(content="الاستضافة الشهرية أعلى بند في تكاليف المشروع", kind=MemoryKind.LESSON, score=9)

    at = _run_app(monkeypatch, tmp_path / "runs", memory_path)
    assert not at.exception
    assert at.text_input, "حقل الاستعلام غير موجود"

    # أول text_input في الصفحة هو حقل البحث، والثاني (في الشريط الجانبي) يسبقه؟
    search = next(widget for widget in at.text_input if "مهمة أو كلمات" in widget.label)
    search.set_value("تكاليف المشروع").run()

    assert not at.exception
    assert any("الاستضافة الشهرية" in str(frame.to_string()) for frame in _frames(at))
