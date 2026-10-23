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
