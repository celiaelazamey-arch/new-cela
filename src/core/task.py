"""
عقود المهمة والنتيجة القابلة للتحقق (Task Contracts)
====================================================
العقد بين **المستخدم** و**محرك الوكيل**:

  TaskRequest → [ سياسة ← ميزانية ← أدوات سياق ← تخطيط ← تنفيذ ] → TaskResult

القاعدة الجوهرية: **لا نعلن النجاح بلا دليل.**
`TaskResult.verified` لا يصبح `True` إلا إذا اجتازت كل الشروط الإلزامية،
والحالة الافتراضية عند الفشل هي `UNVERIFIED` لا `COMPLETED`.
"""
from __future__ import annotations

from enum import Enum

from pydantic import BaseModel, Field


class TaskStatus(str, Enum):
    """حالة المهمة كما تُعرَض للمستخدم — صادقة ولا تجمّل."""

    COMPLETED = "completed"  # نُفِّذت واجتازت كل شروط التحقق
    UNVERIFIED = "unverified"  # نُفِّذت جزئياً/بلا دليل كافٍ — لا يُعلن نجاحها
    BLOCKED = "blocked"  # مُنعت قبل التنفيذ (سياسة/ميزانية/مزود)
    FAILED = "failed"  # انهار التنفيذ بخطأ


class ToolCall(BaseModel):
    """طلب استدعاء أداة قبل التخطيط (جمع السياق) — يمر بسياسة وموافقة."""

    name: str = Field(..., min_length=1)
    args: dict = Field(default_factory=dict)
    purpose: str = ""


class Evidence(BaseModel):
    """
    دليل واحد على عمل تم — أساس التحقق.

    `kind`: tool | step | model | artifact
    """

    kind: str
    ref: str = ""
    content: str = ""
    ok: bool = True


class AcceptanceCheck(BaseModel):
    """شرط نجاح واحد — قابل للعرض والتفسير (لا درجة غامضة)."""

    name: str
    passed: bool
    detail: str = ""
    required: bool = True


class TaskRequest(BaseModel):
    """ما يطلبه المستخدم — مع سياسة تحقق صريحة قابلة للتعديل."""

    task: str = Field(..., min_length=1, description="المهمة بالعربية الطبيعية")
    context_tools: list[ToolCall] = Field(
        default_factory=list, description="أدوات تُنفَّذ قبل التخطيط لجمع السياق"
    )
    require_all_steps: bool = Field(
        default=True, description="هل يجب نجاح كل خطوة ليُعتبر العمل موثّقاً؟"
    )
    require_evidence: bool = Field(
        default=True, description="هل يجب وجود دليل واحد على الأقل (أداة/خطوة)؟"
    )
    min_average_score: int = Field(
        default=0, ge=0, le=10, description="أدنى متوسط تقييم من المقيّم"
    )


class TaskResult(BaseModel):
    """النتيجة النهائية: إجابة + أدلة + شروط + حكم صادق."""

    status: TaskStatus
    answer: str = ""
    verified: bool = False
    evidence: list[Evidence] = Field(default_factory=list)
    checks: list[AcceptanceCheck] = Field(default_factory=list)
    blocked_reason: str = ""
    error: str = ""
    report_id: str = ""
    tool_calls: int = 0
    model_calls: int = 0

    @property
    def failed_checks(self) -> list[AcceptanceCheck]:
        return [check for check in self.checks if check.required and not check.passed]

    def summary(self) -> str:
        """سطر عربي موجز للطرفية — يذكر الدليل وما لم يُتحقق."""
        passed = sum(1 for check in self.checks if check.passed)
        head = {
            TaskStatus.COMPLETED: "✅ مكتملة ومتحقَّقة",
            TaskStatus.UNVERIFIED: "⚠️ غير متحقَّقة",
            TaskStatus.BLOCKED: "⛔ ممنوعة قبل التنفيذ",
            TaskStatus.FAILED: "❌ فاشلة",
        }[self.status]
        parts = [f"{head} — الشروط: {passed}/{len(self.checks)}، الأدلة: {len(self.evidence)}"]
        if self.blocked_reason:
            parts.append(f"السبب: {self.blocked_reason}")
        if self.error:
            parts.append(f"الخطأ: {self.error}")
        if self.failed_checks:
            parts.append("لم تُجتَز: " + "، ".join(check.name for check in self.failed_checks))
        return " | ".join(parts)
