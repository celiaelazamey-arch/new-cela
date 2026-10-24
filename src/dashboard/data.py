"""
طبقة بيانات لوحة المتابعة (Dashboard Data Layer)
================================================
كل ما تحتاجه الواجهة، **بدون أي اعتماد على Streamlit**:
  • قراءة تقارير التشغيل من `.agent_runs/`.
  • تلخيص التشغيل وخطواته وذكرياته المسترجعة.
  • استعلام الذاكرة الاستراتيجية مباشرة (نفس محرك الاسترجاع الحقيقي).

لماذا الفصل؟ لأن منطق العرض القابل للاختبار يجب ألا يحتاج واجهة رسومية —
فالاختبارات تغطّي هذه الطبقة كاملة، و`app.py` يبقى رقيقاً (عرض فقط).
"""
from __future__ import annotations

from datetime import UTC, datetime
from pathlib import Path

from src.core.memory import MemoryHit, MemoryStore
from src.core.path_guard import UnsafePath
from src.core.schemas import MemoryKind, RunReport, RunStatus, StepStatus
from src.core.telemetry import DEFAULT_RUNS_DIR, RunArchive

STATUS_LABELS = {
    RunStatus.RUNNING: "قيد التنفيذ",
    RunStatus.COMPLETED: "مكتمل",
    RunStatus.FAILED: "فشل",
}
STATUS_ICONS = {
    RunStatus.RUNNING: "🟡",
    RunStatus.COMPLETED: "🟢",
    RunStatus.FAILED: "🔴",
    "stale": "⚪",
}
STEP_LABELS = {
    StepStatus.PENDING: "بانتظار",
    StepStatus.IN_PROGRESS: "قيد التنفيذ",
    StepStatus.DONE: "ناجحة",
    StepStatus.FAILED: "فاشلة",
}
STEP_ICONS = {
    StepStatus.PENDING: "⏳",
    StepStatus.IN_PROGRESS: "🔄",
    StepStatus.DONE: "✅",
    StepStatus.FAILED: "❌",
}
KIND_LABELS = {
    MemoryKind.SUCCESS: "نجاح",
    MemoryKind.FAILURE: "فشل",
    MemoryKind.LESSON: "درس",
    MemoryKind.FACT: "معلومة",
}
KIND_ICONS = {
    MemoryKind.SUCCESS: "✅",
    MemoryKind.FAILURE: "❌",
    MemoryKind.LESSON: "💡",
    MemoryKind.FACT: "📌",
}

# التشغيل الذي يبقى «قيد التنفيذ» أطول من هذا الحد يُعتبر منقطعاً (انتهت العملية)
STALE_AFTER_SECONDS = 600
MAX_ANSWER_CHARS = 4000


def _archive(runs_dir: str | Path | None) -> RunArchive | None:
    """يبني الأرشيف بأمان — None إذا كان المسار مرفوضاً من حاجز الأمان."""
    try:
        return RunArchive(path=runs_dir or DEFAULT_RUNS_DIR)
    except UnsafePath:
        return None


# ================= قراءة التشغيلات =================

def load_runs(runs_dir: str | Path | None, limit: int = 30) -> list[RunReport]:
    """أحدث التشغيلات أولاً — قائمة فارغة إذا لم يكن هناك سجل أو كان المسار مرفوضاً."""
    archive = _archive(runs_dir)
    return archive.list_recent(limit=limit) if archive is not None else []


def latest_run(runs_dir: str | Path | None) -> RunReport | None:
    return _archive(runs_dir).latest()


def find_run(runs_dir: str | Path | None, run_id: str) -> RunReport | None:
    return _archive(runs_dir).load(run_id)


def is_stale(report: RunReport, stale_after: float = STALE_AFTER_SECONDS) -> bool:
    """هل تبدو هذه المهمة منقطعة؟ (عملية أُوقفت أثناء التشغيل)."""
    if report.status is not RunStatus.RUNNING:
        return False
    started = _parse_time(report.started_at)
    if started is None:
        return False
    return (datetime.now(UTC) - started).total_seconds() > stale_after


def status_of(report: RunReport) -> str:
    """مفتاح الحالة الفعلي المعروض (يراعي الانقطاع)."""
    return "stale" if is_stale(report) else report.status.value


def status_text(report: RunReport) -> str:
    key = status_of(report)
    if key == "stale":
        return "منقطع"
    return STATUS_LABELS[report.status]


def status_icon(report: RunReport) -> str:
    return STATUS_ICONS[status_of(report)]


# ================= تلخيص التشغيل =================

def summarize(report: RunReport) -> dict[str, object]:
    """أرقام لوحة الحالة — كل القيم أولية/نصوص جاهزة للعرض."""
    return {
        "id": report.id,
        "status": status_of(report),
        "status_text": status_text(report),
        "status_icon": status_icon(report),
        "task": report.task,
        "steps_total": len(report.steps),
        "steps_planned": max(report.planned_steps, len(report.steps)),
        "steps_done": report.steps_done,
        "steps_failed": report.steps_failed,
        "average_score": report.average_score,
        "progress": report.progress,
        "duration": format_duration(report.duration_seconds),
        "memory_hits": len(report.memory_hits),
        "memories_written": report.memories_written,
        "started_at": format_time(report.started_at),
        "finished_at": format_time(report.finished_at),
        "answer": (report.answer or "")[:MAX_ANSWER_CHARS],
        "error": report.error,
    }


def aggregate(reports: list[RunReport]) -> dict[str, float]:
    """مؤشرات عامة أعلى الصفحة (نسبة النجاح، متوسط التقييم، إجمالي الذكريات)."""
    total = len(reports)
    if total == 0:
        return {"total": 0, "completed": 0, "failed": 0, "success_rate": 0.0,
                "average_score": 0.0, "memories_written": 0}

    completed = sum(1 for report in reports if report.status is RunStatus.COMPLETED)
    failed = sum(1 for report in reports if report.status is RunStatus.FAILED)
    scores = [report.average_score for report in reports if report.average_score]
    return {
        "total": total,
        "completed": completed,
        "failed": failed,
        "success_rate": round(100 * completed / total, 1),
        "average_score": round(sum(scores) / len(scores), 1) if scores else 0.0,
        "memories_written": sum(report.memories_written for report in reports),
    }


def step_rows(report: RunReport) -> list[dict[str, object]]:
    """صفوف جدول الخطوات — بأعمدة عربية واضحة."""
    rows: list[dict[str, object]] = []
    for step in report.steps:
        rows.append(
            {
                "#": step.index,
                "الحالة": f"{STEP_ICONS[step.status]} {STEP_LABELS[step.status]}",
                "الخطوة": step.description,
                "المحاولات": step.attempts,
                "التقييم": step.score,
                "ملاحظة المقيّم": (step.critiques[-1] if step.critiques else ""),
                "التصحيح المقترح": step.suggestion,
                "النتيجة": step.result_preview,
            }
        )
    return rows


def memory_hit_rows(report: RunReport) -> list[dict[str, object]]:
    """الذكريات التي استخدمها هذا التشغيل فعلاً."""
    return [
        {
            "النوع": f"{KIND_ICONS[hit.kind]} {KIND_LABELS[hit.kind]}",
            "الذكرى": hit.content,
            "الدرجة": round(hit.score, 3),
            "التشابه": round(hit.similarity, 3),
            "الكلمات المطابقة": "، ".join(hit.matched),
        }
        for hit in report.memory_hits
    ]


# ================= الذاكرة =================

def open_memory(memory_path: str | Path | None, max_entries: int | None = None) -> MemoryStore:
    """يفتح مخزن الذاكرة للقراءة — مسار مرفوض ⇒ مخزن فارغ بلا أخطاء."""
    try:
        return MemoryStore(
            path=memory_path or ":memory:",
            max_entries=max_entries or 500,
        )
    except UnsafePath:
        return MemoryStore(path=":memory:", max_entries=max_entries or 500)


def memory_overview(memory_path: str | Path | None) -> dict[str, object]:
    """إحصاءات الذاكرة + عدد لكل نوع (جاهزة لبطاقات العرض)."""
    store = open_memory(memory_path)
    entries = store.all()
    counts = {kind: 0 for kind in MemoryKind}
    for entry in entries:
        counts[entry.kind] += 1
    return {
        "total": len(entries),
        "counts": counts,
        "path": str(store.path) if store.path else None,
        "latest": format_time(entries[-1].created_at) if entries else "",
        "store": store,
    }


def search_memory(
    memory_path: str | Path | None,
    query: str,
    top_k: int = 8,
    kinds: list[MemoryKind] | None = None,
) -> list[MemoryHit]:
    """
    استعلام حقيقي على الذاكرة — نفس محرك TF-IDF الذي يستخدمه الوكيل،
    حتى تُظهر اللوحة «ما سيراه الوكيل» لا محاكاة مختلفة.
    """
    if not query or not query.strip():
        return []
    store = open_memory(memory_path)
    return store.search(query, top_k=top_k, kinds=kinds)


def hit_rows(hits: list[MemoryHit]) -> list[dict[str, object]]:
    return [
        {
            "النوع": f"{KIND_ICONS[hit.entry.kind]} {KIND_LABELS[hit.entry.kind]}",
            "الذكرى": hit.entry.content,
            "الدرجة": round(hit.score, 3),
            "التشابه": round(hit.similarity, 3),
            "التقييم": hit.entry.score,
            "الكلمات المطابقة": "، ".join(hit.matched),
        }
        for hit in hits
    ]


def recent_entries(memory_path: str | Path | None, limit: int = 20) -> list[dict[str, object]]:
    """أحدث الذكريات المخزَّنة (الأحدث أولاً) لعرضها في تبويب الذاكرة."""
    entries = open_memory(memory_path).all()
    rows: list[dict[str, object]] = []
    for entry in reversed(entries[-limit:]) if limit > 0 else []:
        rows.append(
            {
                "النوع": f"{KIND_ICONS[entry.kind]} {KIND_LABELS[entry.kind]}",
                "الذكرى": entry.content,
                "المهمة": entry.task,
                "التقييم": entry.score,
                "التاريخ": format_time(entry.created_at),
            }
        )
    return rows


def memory_export(memory_path: str | Path | None) -> str:
    """تصدير الذاكرة كـ JSON — للنسخ الاحتياطي أو التحليل الخارجي."""
    import json

    entries = open_memory(memory_path).all()
    return json.dumps([entry.model_dump(mode="json") for entry in entries], ensure_ascii=False, indent=2)


# ================= تنسيق =================

def format_duration(seconds: float) -> str:
    """مدّة مقروءة بالعربية: 12.4 ث / 3 د 05 ث / 1 س 04 د."""
    total = max(0, round(seconds or 0))
    if total < 60:
        return f"{total} ث"
    minutes, secs = divmod(total, 60)
    if minutes < 60:
        return f"{minutes} د {secs:02d} ث"
    hours, minutes = divmod(minutes, 60)
    return f"{hours} س {minutes:02d} د"


def format_time(iso: str) -> str:
    """طابع زمني مقروء (UTC) — النص الأصلي عند تعذّر التحليل."""
    parsed = _parse_time(iso)
    if parsed is None:
        return iso or "—"
    return parsed.strftime("%Y-%m-%d %H:%M") + " UTC"


def _parse_time(iso: str) -> datetime | None:
    if not iso:
        return None
    try:
        parsed = datetime.fromisoformat(iso)
    except (TypeError, ValueError):
        return None
    return parsed if parsed.tzinfo else parsed.replace(tzinfo=UTC)
