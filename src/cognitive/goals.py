"""أهداف صريحة بمعايير نجاح قابلة للتنفيذ وميزانيات محدودة.

وجود وصف للهدف لا يكفي: كل معيار إما أمر يُنفَّذ عبر الأدوات، أو تغيّر ملف
يُقاس بلقطتين، أو شروط المحرك التي تُحسب من الأدلة. لا يوجد معيار «يبدو جيداً».
"""
from __future__ import annotations

from typing import Literal

from pydantic import BaseModel, Field, model_validator

from src.cognitive._utils import digest_text

GoalStatus = Literal["in_progress", "blocked", "done", "failed"]
CriterionKind = Literal["command_exit", "file_changed", "engine_checks"]


class GoalBudget(BaseModel):
    """سقف العمل لا وعد بأن الهدف سيتحقق داخله."""

    max_steps: int = Field(default=3, ge=1, le=20)
    max_replans: int = Field(default=1, ge=0, le=1)
    max_tool_calls: int = Field(default=12, ge=1, le=100)


class SuccessCriterion(BaseModel):
    """شرط نجاح واحد مرتبط بملاحظة قابلة لإعادة التشغيل."""

    id: str = Field(..., min_length=1)
    kind: CriterionKind
    description: str = ""
    command: str = ""
    expected_exit_code: int = 0
    path: str = ""
    required: bool = True

    @model_validator(mode="after")
    def validate_executable_target(self):
        if self.kind == "command_exit" and not self.command.strip():
            raise ValueError("معيار command_exit يحتاج أمراً قابلاً للتنفيذ.")
        if self.kind == "file_changed" and not self.path.strip():
            raise ValueError("معيار file_changed يحتاج مسار ملف.")
        return self


class CriterionResult(BaseModel):
    """نتيجة قياس معيار، لا تقييم لغوي من النموذج."""

    criterion_id: str
    passed: bool
    required: bool = True
    detail: str = ""


class Goal(BaseModel):
    """هدف رئيسي أو فرعي؛ الأولوية الأكبر تُخدم أولاً عند الجدولة."""

    id: str = Field(..., min_length=1)
    description: str = Field(..., min_length=1)
    parent_id: str = ""
    success_criteria: list[SuccessCriterion] = Field(default_factory=list, min_length=1)
    budget: GoalBudget = Field(default_factory=GoalBudget)
    priority: int = Field(default=0, ge=0, le=100)
    status: GoalStatus = "in_progress"
    subgoals: list[Goal] = Field(default_factory=list)

    @property
    def verification_commands(self) -> list[str]:
        own = [
            criterion.command
            for criterion in self.success_criteria
            if criterion.kind == "command_exit"
        ]
        return [*own, *(command for child in self.subgoals for command in child.verification_commands)]

    def flatten(self) -> list[Goal]:
        """قائمة حتمية بالهدف ثم أهدافه الفرعية بترتيبها المعلن."""
        return [self, *(child for subgoal in self.subgoals for child in subgoal.flatten())]

    def with_status(self, status: GoalStatus) -> Goal:
        """نسخة مغلقة باتساق؛ معايير الفروع جزء من حكم الأصل."""
        return self.model_copy(
            update={
                "status": status,
                "subgoals": [child.with_status(status) for child in self.subgoals],
            }
        )

    @classmethod
    def for_engine(cls, task: str) -> Goal:
        """هدف المهام العامة: نجاحه هو اجتياز شروط المحرك الإلزامية فعلاً."""
        identifier = "goal-" + digest_text("engine:" + task)[:16]
        return cls(
            id=identifier,
            description=task,
            success_criteria=[
                SuccessCriterion(
                    id="engine-verification",
                    kind="engine_checks",
                    description="اجتياز كل شروط TaskResult الإلزامية",
                )
            ],
        )

    @classmethod
    def for_repair(
        cls,
        task: str,
        test_command: str,
        target_path: str,
        *,
        max_steps: int = 3,
    ) -> Goal:
        """هدف قبول الإصلاح: تغيّر الملف وأصبح أمر الاختبار بالرمز المطلوب."""
        identifier = "goal-" + digest_text(f"repair:{task}:{test_command}:{target_path}")[:16]
        return cls(
            id=identifier,
            description=task,
            success_criteria=[
                SuccessCriterion(
                    id="file-changed",
                    kind="file_changed",
                    path=target_path,
                    description="تغيّرت بصمة الملف المستهدف فعلاً",
                ),
                SuccessCriterion(
                    id="tests-pass",
                    kind="command_exit",
                    command=test_command,
                    expected_exit_code=0,
                    description="أمر الاختبار يخرج بالرمز 0",
                ),
            ],
            budget=GoalBudget(max_steps=max_steps, max_replans=1),
        )


__all__ = [
    "CriterionKind",
    "CriterionResult",
    "Goal",
    "GoalBudget",
    "GoalStatus",
    "SuccessCriterion",
]
