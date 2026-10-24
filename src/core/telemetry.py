"""
سجل التشغيلات (Run Telemetry)
=============================
يحوّل ما يجري داخل `Planner` إلى ملفات JSON قابلة للمراقبة الحية:

  • ملف لكل تشغيل داخل `runs_dir` (افتراضياً `.agent_runs/`).
  • كتابة **ذرّية** بعد كل خطوة ⇒ تستطيع اللوحة عرض التقدّم أثناء العمل.
  • سجل دائم بعد الانتهاء ⇒ مراجعة تاريخية ومقارنة تشغيلات.

مبادئ التصميم:
  • **لا يُسقط المهمة:** أي خطأ إدخال/إخراج يُسجَّل تحذيراً ويُكمَل التنفيذ.
  • **تنقيح الأسرار:** كل نص يُمرَّر عبر `redact()` قبل الكتابة — لا مفاتيح في السجل.
  • **بلا تبعيات ثقيلة:** pydantic + stdlib فقط (الواجهة تقرأ هذه الملفات لاحقاً).
"""
from __future__ import annotations

import json
import logging
import os
import re
import time
import uuid
from datetime import UTC, datetime
from pathlib import Path

from src.core.path_guard import UnsafePath, guard_path, guard_run_id
from src.core.schemas import (
    MemoryHitView,
    RunReport,
    RunStatus,
    StepRecord,
    StepView,
)

logger = logging.getLogger("new-cela.telemetry")

# Allowlist لتسجيل اسم ملف التقرير — barrier يعترف به CodeQL (نمط store.py).
# يسمح بحروف/أرقام يونيكود/نقاط/شرطات/مسافات؛ أي محرف تحكّم ⇒ يُحجب الاسم.
_SAFE_FILE_LABEL = re.compile(r"[\w .\-]+")

DEFAULT_RUNS_DIR = ".agent_runs"
MAX_TEXT_CHARS = 6000
RESULT_PREVIEW_CHARS = 400

# ---- أنماط المفاتيح الشائعة (Gemini / Groq / OpenRouter / عام) ----
_SECRET_PATTERNS = (
    re.compile(r"AIza[0-9A-Za-z_\-]{30,}"),  # Google API key
    re.compile(r"gsk_[0-9A-Za-z]{20,}"),  # Groq
    re.compile(r"sk-or-v1-[0-9A-Za-z]{16,}"),  # OpenRouter
    re.compile(r"\bsk-[0-9A-Za-z]{20,}"),  # OpenAI-style
    re.compile(r"(?i)\bbearer\s+[0-9A-Za-z._\-]{12,}"),
    re.compile(r"(?i)\b(api[_-]?key|token|secret|password)\b\s*[:=]\s*['\"]?[^\s'\"]{6,}"),
)
REDACTED = "***تم التنقيح***"


def redact(text: str) -> str:
    """
    يحجب أي مفتاح/رمز يظهر داخل نص قبل حفظه في السجل.
    الهدف: حتى لو لصق المستخدم مفتاحاً في نص المهمة، لا يدخل السجل.
    """
    if not text:
        return ""
    cleaned = text
    for pattern in _SECRET_PATTERNS:
        cleaned = pattern.sub(REDACTED, cleaned)
    return cleaned


def _clip(text: str, limit: int = MAX_TEXT_CHARS) -> str:
    text = text or ""
    return text if len(text) <= limit else text[: limit - 1] + "…"


def _now_iso() -> str:
    """طابع زمني بالمللي ثانية — يضمن ترتيباً صحيحاً حتى داخل الثانية نفسها."""
    return datetime.now(UTC).isoformat(timespec="milliseconds")


def _run_id() -> str:
    """
    مُعرِّف ترتيبي بدقة الميكروثانية: `20261023T165542123456Z-a1b2c3`.

    لماذا الميكروثانية؟ لأن دقة `mtime` في بعض الأنظمة الملفية ثانية واحدة كاملة،
    فترتيب التشغيلات بالوقت الفعلي للملف غير موثوق. الطابع الزمني داخل الاسم
    يجعل الترتيب الاسمي = الترتيب الزمني دائماً، عبر العمليات أيضاً.
    """
    stamp = datetime.now(UTC).strftime("%Y%m%dT%H%M%S%fZ")
    return f"{stamp}-{uuid.uuid4().hex[:6]}"


def step_to_view(record: StepRecord) -> StepView:
    """يحوّل سجل الخطوة الداخلي إلى لقطة عرض منقّحة ومقتطعة."""
    return StepView(
        index=record.index,
        description=redact(_clip(record.description, 500)),
        status=record.status,
        attempts=record.attempts,
        score=record.score,
        suggestion=redact(_clip(record.suggestion, 1000)),
        result_preview=redact(_clip(record.result, RESULT_PREVIEW_CHARS)),
        critiques=[redact(_clip(item, 500)) for item in record.critiques],
    )


class RunSession:
    """
    جلسة تشغيل واحدة: تجمع التقرير وتكتبه على القرص بعد كل تحديث.

    الاستخدام (من داخل `Planner`):
        session = archive.start("مهمة")
        session.record_recall(hits)
        session.record_step(record)
        session.finish(answer)      # أو session.fail(exc)
    """

    def __init__(self, archive: RunArchive, task: str) -> None:
        self._archive = archive
        self._started_monotonic = time.monotonic()
        self.report = RunReport(
            id=_run_id(),
            task=redact(_clip(task)),
            status=RunStatus.RUNNING,
            started_at=_now_iso(),
        )
        # ✅ defense-in-depth: الـ id مولَّد داخلياً، لكن نتحقق منه قبل بناء المسار.
        safe_id = guard_run_id(self.report.id)
        self.path = archive.path / f"{safe_id}.json"
        self._flush()

    # ---------------- التحديثات ----------------
    def record_recall(self, hits: list) -> None:
        """يحفظ الذكريات التي استُرجعت فعلاً (المصدر الحقيقي لتعلّم الوكيل)."""
        self.report.memory_hits = [
            MemoryHitView(
                kind=hit.entry.kind,
                content=redact(_clip(hit.entry.content, 500)),
                score=float(hit.score),
                similarity=float(hit.similarity),
                matched=list(hit.matched),
            )
            for hit in hits
        ]
        self._flush()

    def set_planned_steps(self, count: int) -> None:
        self.report.planned_steps = max(0, int(count))
        self._flush()

    def record_step(self, record: StepRecord) -> None:
        """يضيف/يحدّث خطوة ويُعيد كتابة الملف — أساس المتابعة الحية."""
        view = step_to_view(record)
        for position, existing in enumerate(self.report.steps):
            if existing.index == view.index:
                self.report.steps[position] = view
                break
        else:
            self.report.steps.append(view)
        self._flush()

    def set_memories_written(self, count: int) -> None:
        self.report.memories_written = max(0, int(count))
        self._flush()

    def finish(self, answer: str) -> RunReport:
        """يُغلق التشغيل بنجاح ويحفظ الإجابة النهائية."""
        self.report.status = RunStatus.COMPLETED
        self.report.answer = redact(_clip(answer))
        return self._close()

    def fail(self, error: BaseException | str) -> RunReport:
        """يُغلق التشغيل بفشل مع سبب مفهوم (بلا أثر مفاتيح)."""
        self.report.status = RunStatus.FAILED
        self.report.error = redact(_clip(f"{type(error).__name__}: {error}" if isinstance(error, BaseException) else str(error), 1000))
        return self._close()

    # ---------------- الداخل ----------------
    def _close(self) -> RunReport:
        self.report.finished_at = _now_iso()
        self.report.duration_seconds = round(time.monotonic() - self._started_monotonic, 2)
        self._flush()
        self._archive.prune()
        return self.report

    def _flush(self) -> None:
        """كتابة ذرّية عبر ملف مؤقت + os.replace — لا ملف نصف مكتوب أبداً."""
        try:
            self._archive.path.mkdir(parents=True, exist_ok=True)
            temp_path = self.path.with_suffix(".json.tmp")
            payload = json.dumps(
                self.report.model_dump(mode="json"), ensure_ascii=False, indent=2
            )
            temp_path.write_text(payload, encoding="utf-8")
            os.replace(temp_path, self.path)
        except OSError as exc:
            logger.warning(
                "تعذّر حفظ تقرير التشغيل (%s) — المهمة مستمرة.", type(exc).__name__
            )


class RunArchive:
    """
    أرشيف التشغيلات على القرص — مصدر بيانات لوحة المتابعة.

    - `keep` حد أقصى لعدد الملفات المحفوظة (الأقدم يُحذف تلقائياً).
    - القراءة متسامحة: أي ملف تالف يُتخطى ولا يُسقط الأرشيف كله.
    """

    def __init__(self, path: str | os.PathLike[str] | None = DEFAULT_RUNS_DIR, keep: int = 50) -> None:
        # ✅ حاجز الأمان: المسار قد يأتي من st.text_input (مصدر غير موثوق).
        # يُطهَّر هنا قبل أي glob / load — يعالج تنبيه CodeQL #3.
        try:
            self.path = guard_path(path or DEFAULT_RUNS_DIR)
        except UnsafePath as exc:
            # نوع الاستثناء فقط — لا قيمة من المستخدم تصل للسجل (يمنع CWE-117).
            logger.warning("مسار سجل تشغيلات مرفوض (%s).", type(exc).__name__)
            raise
        self.keep = max(1, int(keep))

    # ---------------- الكتابة ----------------
    def start(self, task: str) -> RunSession:
        return RunSession(self, task)

    def prune(self) -> None:
        """يُبقي أحدث `keep` ملفات فقط."""
        try:
            files = sorted(self.path.glob("*.json"))
            for stale in files[: max(0, len(files) - self.keep)]:
                stale.unlink(missing_ok=True)
        except OSError as exc:
            logger.debug("تعذّر تقليم سجل التشغيلات (%s).", type(exc).__name__)

    # ---------------- القراءة ----------------
    def load(self, run_id: str) -> RunReport | None:
        """يقرأ تقريراً واحداً بالمعرِّف — None إذا كان مفقوداً أو تالفاً."""
        # ✅ نفس نمط cognitive/store.py::_validate_run_id — يمنع حقن المسار
        # عبر run_id قبل بناء `self.path / f"{run_id}.json"`.
        safe_id = guard_run_id(run_id)
        candidate = self.path / f"{safe_id}.json"
        if not candidate.exists():
            return None
        return _read_report(candidate)

    def list_recent(self, limit: int = 20) -> list[RunReport]:
        """أحدث التقارير أولاً (الاسم ترتيبي) — مع تجاهل الملفات التالفة كلياً."""
        if limit <= 0:
            return []
        try:
            files = sorted(self.path.glob("*.json"), reverse=True)
        except OSError:
            return []

        reports: list[RunReport] = []
        for file in files:
            if len(reports) >= limit:
                break
            report = _read_report(file)
            if report is not None:
                reports.append(report)
        return reports

    def latest(self) -> RunReport | None:
        reports = self.list_recent(limit=1)
        return reports[0] if reports else None

    def __repr__(self) -> str:  # pragma: no cover - تشخيصي
        return f"RunArchive(path={self.path!r}, keep={self.keep})"


def _read_report(file: Path) -> RunReport | None:
    """قراءة متسامحة: ملف تالف/ناقص لا يُسقط بقية السجل."""
    try:
        return RunReport.model_validate(json.loads(file.read_text(encoding="utf-8")))
    except (OSError, ValueError, TypeError) as exc:
        # ✅ BARRIER بنمط store.py: اسم الملف يُسجَّل فقط إذا طابق allowlist،
        # وإلا يُحجب. يمنع Log Injection عبر اسم ملف خبيث (CWE-117).
        file_label = file.name
        if _SAFE_FILE_LABEL.fullmatch(file_label):
            logger.warning("تخطّي تقرير تشغيل غير صالح %s (%s).",
                           file_label, type(exc).__name__)
        else:
            logger.warning("تخطّي تقرير تشغيل غير صالح (%s).", type(exc).__name__)
        return None
