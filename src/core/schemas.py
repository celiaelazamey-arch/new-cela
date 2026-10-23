"""
مخططات البيانات المنظّمة (Pydantic State Models)
===============================================
تعرّف عقود الإخراج المنظّم (Function Calling) بين الوكيل والنماذج،
وحالات دورة حياة كل خطوة في الخطة (آلة حالات صريحة).
"""
from __future__ import annotations

from enum import Enum

from pydantic import BaseModel, Field


class StepStatus(str, Enum):
    """دورة حياة الخطوة — آلة حالات صريحة."""

    PENDING = "pending"
    IN_PROGRESS = "in_progress"
    DONE = "done"
    FAILED = "failed"


class PlanOutput(BaseModel):
    """عقد إخراج التخطيط (Planner): قائمة الخطوات الملموسة بالترتيب."""

    steps: list[str] = Field(..., min_length=1, description="خطوات التنفيذ بالترتيب")


class CritiqueOutput(BaseModel):
    """عقد إخراج التقييم الذاتي (Critic)."""

    passed: bool = Field(..., description="هل حققت الخطوة هدفها؟")
    score: int = Field(..., ge=0, le=10, description="تقييم من 10")
    feedback: str = Field(..., description="تحليل النتيجة")
    suggestion: str = Field(default="", description="كيفية التحسين عند الفشل")


class StepRecord(BaseModel):
    """سجل تنفيذ خطوة واحدة — يُستخدم للمراجعة ولوحة المتابعة لاحقاً."""

    index: int
    description: str
    status: StepStatus = StepStatus.PENDING
    result: str = ""
    attempts: int = 0
    score: int = 0
    suggestion: str = ""
    critiques: list[str] = Field(default_factory=list)

    @property
    def succeeded(self) -> bool:
        return self.status is StepStatus.DONE


class MemoryKind(str, Enum):
    """تصنيف الذكرى — يحدّد أولويتها عند الاسترجاع."""

    SUCCESS = "success"  # خطوة نجحت واجتازت التقييم
    FAILURE = "failure"  # خطوة فشلت بعد استنفاد المحاولات
    LESSON = "lesson"  # درس مستخلص من مهمة سابقة (الأعلى قيمة)
    FACT = "fact"  # معلومة/خلاصة عامة عن مهمة ناجحة


class MemoryEntry(BaseModel):
    """
    ذكرى واحدة في الذاكرة الاستراتيجية — وحدة التخزين والاسترجاع.

    `id` هو بصمة محتوى حتمية (SHA-1) تُستخدم لمنع التكرار،
    و`created_at` طابع زمني ISO-8601 بتوقيت UTC.
    """

    id: str = ""
    kind: MemoryKind = MemoryKind.FACT
    content: str = Field(..., min_length=1, description="النص المخزَّن القابل للاسترجاع")
    task: str = Field(default="", description="المهمة الأصلية التي أنتجت هذه الذكرى")
    score: int = Field(default=0, ge=0, le=10, description="تقييم المقيّم (0 = غير معروف)")
    tags: list[str] = Field(default_factory=list)
    created_at: str = ""


class RunStatus(str, Enum):
    """حالة تشغيل كامل — تظهر مباشرة في لوحة المتابعة."""

    RUNNING = "running"
    COMPLETED = "completed"
    FAILED = "failed"


class StepView(BaseModel):
    """لقطة قابلة للتسلسل من خطوة — للعرض والتحليل خارج عملية الوكيل."""

    index: int
    description: str
    status: StepStatus = StepStatus.PENDING
    attempts: int = 0
    score: int = Field(default=0, ge=0, le=10)
    suggestion: str = ""
    result_preview: str = ""
    critiques: list[str] = Field(default_factory=list)


class MemoryHitView(BaseModel):
    """لقطة من ذكرى مسترجعة — تُظهر ما رآه الوكيل فعلاً قبل التخطيط."""

    kind: MemoryKind
    content: str
    score: float = 0.0
    similarity: float = 0.0
    matched: list[str] = Field(default_factory=list)


class RunReport(BaseModel):
    """
    تقرير تشغيل واحد — العقد بين المحرك والواجهة.

    يُكتب على القرص بعد كل خطوة (كتابة ذرّية) لتستطيع اللوحة متابعة
    التشغيل حياً، ويبقى بعد انتهاء العملية كسجل قابل للمراجعة.
    """

    id: str
    task: str
    status: RunStatus = RunStatus.RUNNING
    started_at: str = ""
    finished_at: str = ""
    duration_seconds: float = 0.0
    planned_steps: int = 0
    answer: str = ""
    error: str = ""
    steps: list[StepView] = Field(default_factory=list)
    memory_hits: list[MemoryHitView] = Field(default_factory=list)
    memories_written: int = 0

    @property
    def steps_done(self) -> int:
        return sum(1 for step in self.steps if step.status is StepStatus.DONE)

    @property
    def steps_failed(self) -> int:
        return sum(1 for step in self.steps if step.status is StepStatus.FAILED)

    @property
    def average_score(self) -> float:
        scored = [step.score for step in self.steps if step.score]
        return round(sum(scored) / len(scored), 1) if scored else 0.0

    @property
    def progress(self) -> float:
        """نسبة التقدّم 0..1 — تعتمد على عدد الخطوات المخطط لها."""
        planned = max(self.planned_steps, len(self.steps))
        if planned == 0:
            return 1.0 if self.status is not RunStatus.RUNNING else 0.0
        return min(1.0, len(self.steps) / planned)
