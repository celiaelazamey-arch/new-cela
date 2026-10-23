"""مخطط الحالة الإدراكية التشغيلية والانتقالات ونقاط الحفظ.

الحالة بيانات يُشتق منها التقرير؛ ليست شخصية ولا شهادة نجاح ذاتية. كل انتقال
يحمل قبل/بعد وبصمتيهما، وكل checkpoint يحمل بصمة تمنع قبول نسخة عُدّلت بصمت.
"""
from __future__ import annotations

from typing import Any, Literal

from pydantic import BaseModel, Field

from src.cognitive._utils import digest_json
from src.cognitive.goals import Goal
from src.cognitive.world import WorldSnapshot
from src.core.evidence import Evidence

CompletionStatus = Literal["in_progress", "blocked", "done", "failed"]
GENESIS_HASH = "0" * 64


class ActionReceipt(BaseModel):
    """إثبات أن خطوة ذات أثر نُفِّذت؛ يُستخدم لمنع إعادتها عند الاستئناف."""

    action_id: str
    kind: str
    status: Literal["done", "failed"] = "done"
    before_hash: str = ""
    after_hash: str = ""
    evidence_refs: list[str] = Field(default_factory=list)


class CognitiveState(BaseModel):
    """الحالة القابلة للتسلسل؛ الحقول الإضافية تخدم القياس والاستئناف فقط."""

    task: str = Field(..., min_length=1)
    run_id: str = ""
    active_goal: str = ""
    goal: Goal | None = None
    environment: WorldSnapshot | None = None
    baseline: WorldSnapshot | None = None
    capabilities: list[str] = Field(default_factory=list)
    evidence: list[Evidence] = Field(default_factory=list)
    assumptions: list[str] = Field(default_factory=list)
    uncertainty: list[str] = Field(default_factory=list)
    memory_refs: list[str] = Field(default_factory=list)
    next_action: dict | None = None
    action_plan: list[dict] = Field(default_factory=list)
    approval_required: bool = False
    verification_plan: list[str] = Field(default_factory=list)
    completion_status: CompletionStatus = "in_progress"
    confidence: float = Field(default=0.0, ge=0.0, le=1.0)
    diagnosis: str = ""
    replan_count: int = Field(default=0, ge=0)
    tool_calls: int = Field(default=0, ge=0)
    revision: int = Field(default=0, ge=0)
    completed_steps: list[str] = Field(default_factory=list)
    receipts: list[ActionReceipt] = Field(default_factory=list)

    def fingerprint(self) -> str:
        return digest_json(self.model_dump(mode="json"))

    def has_completed(self, step_id: str) -> bool:
        return step_id in self.completed_steps


class Transition(BaseModel):
    """انتقال واحد مع صورتي الحالة وبصمتيهما؛ لا طابع زمني داخل البصمة."""

    stage: str
    reason: str = ""
    before: dict = Field(default_factory=dict)
    after: dict
    before_hash: str = GENESIS_HASH
    after_hash: str

    @classmethod
    def between(
        cls,
        before: CognitiveState | None,
        after: CognitiveState,
        *,
        stage: str,
        reason: str = "",
    ) -> Transition:
        return cls(
            stage=stage,
            reason=reason,
            before={} if before is None else before.model_dump(mode="json"),
            after=after.model_dump(mode="json"),
            before_hash=GENESIS_HASH if before is None else before.fingerprint(),
            after_hash=after.fingerprint(),
        )

    def verify(self) -> bool:
        """يتحقق أن الصورتين والبصمتين لم يُعبث بهما."""
        if self.after_hash != digest_json(self.after):
            return False
        if self.before_hash == GENESIS_HASH and not self.before:
            return True
        return bool(self.before) and self.before_hash == digest_json(self.before)


def apply_transition(
    state: CognitiveState,
    *,
    stage: str,
    updates: dict[str, Any] | None = None,
    reason: str = "",
) -> tuple[CognitiveState, Transition]:
    """انتقال نقي ومدقَّق: المدخل نفسه والتحديث نفسه يعطيان النتيجة نفسها."""
    payload = state.model_dump(mode="json")
    payload.update(updates or {})
    payload["revision"] = state.revision + 1
    updated = CognitiveState.model_validate(payload)
    transition = Transition.between(state, updated, stage=stage, reason=reason)
    return updated, transition


class Checkpoint(BaseModel):
    """لقطة استئناف مرتبطة برأس سجل الأدلة الذي أثبت انتقالاتها."""

    schema_version: int = 1
    state: CognitiveState
    event_head: str = GENESIS_HASH
    checkpoint_hash: str = ""

    def payload(self) -> dict:
        return self.model_dump(mode="json", exclude={"checkpoint_hash"})

    def sealed(self) -> Checkpoint:
        return self.model_copy(update={"checkpoint_hash": digest_json(self.payload())})

    def verify(self) -> bool:
        return bool(self.checkpoint_hash) and self.checkpoint_hash == digest_json(self.payload())

    @property
    def checkpoint_id(self) -> str:
        return f"{self.state.run_id}:{self.state.revision}"


__all__ = [
    "GENESIS_HASH",
    "ActionReceipt",
    "Checkpoint",
    "CognitiveState",
    "CompletionStatus",
    "Transition",
    "apply_transition",
]
