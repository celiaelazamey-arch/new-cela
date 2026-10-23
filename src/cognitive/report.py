"""تقرير عربي مشتق من الحالة والأدلة، لا نصاً يولّد ادّعاء إنجاز حراً."""
from __future__ import annotations

from pydantic import BaseModel, Field

from src.cognitive.goals import CriterionResult, Goal
from src.cognitive.state import CognitiveState
from src.cognitive.world import WorldDiff


class CognitiveReport(BaseModel):
    """عرض قابل للاختبار: ما تم، ما بقي، لماذا، وما لم يُتحقق."""

    state: CognitiveState
    goal: Goal | None = None
    criteria: list[CriterionResult] = Field(default_factory=list)
    diff: WorldDiff | None = None
    event_head: str = ""

    def render(self) -> str:
        state = self.state
        goal = self.goal or state.goal
        status_labels = {
            "done": "مكتمل بأدلة",
            "blocked": "محجوب بسياسة أو موافقة",
            "failed": "لم يتحقق",
            "in_progress": "قيد التنفيذ",
        }
        lines = [
            "# تقرير التشغيل الإدراكي",
            f"- المهمة: {state.task}",
            f"- الهدف النشط: {goal.description if goal else state.active_goal or 'غير محدد'}",
            f"- الحالة: {status_labels[state.completion_status]}",
            f"- مراجعة الحالة: {state.revision}",
            "",
            "## ما تم",
        ]
        if state.receipts:
            for receipt in state.receipts:
                lines.append(
                    f"- {receipt.kind} ({receipt.action_id}): {receipt.status}; "
                    f"البصمة بعد الأثر: {receipt.after_hash or 'لا يوجد'}"
                )
        else:
            lines.append("- لا أثر منفّذ مسجل.")

        commands = state.environment.commands if state.environment else []
        if commands:
            lines.append("- الأوامر المقيسة:")
            for observation in commands:
                lines.append(f"  - `{observation.command}` → exit={observation.returncode}")
        if self.diff is not None:
            changed = self.diff.changed_paths()
            lines.append("- الملفات المتغيرة: " + (", ".join(changed) if changed else "لا شيء"))

        lines.extend(["", "## ما بقي وما لم يُتحقق"])
        pending = [result for result in self.criteria if result.required and not result.passed]
        if pending:
            lines.extend(f"- ❌ {result.criterion_id}: {result.detail}" for result in pending)
        elif state.completion_status == "done":
            lines.append("- لا شروط إلزامية متبقية وفق الملاحظات الحالية.")
        else:
            lines.append("- لا يمكن إعلان اكتمال الهدف من الحالة الحالية.")
        if state.next_action:
            lines.append(f"- الإجراء التالي المسجل: {state.next_action.get('kind', 'غير معروف')}")

        lines.extend(["", "## لماذا وحدود المعرفة"])
        if state.diagnosis:
            lines.append(f"- التشخيص: {state.diagnosis}")
        if state.uncertainty:
            lines.extend(f"- عدم يقين: {item}" for item in state.uncertainty)
        if state.assumptions:
            lines.extend(f"- افتراض (ليس حقيقة): {item}" for item in state.assumptions)
        if not state.diagnosis and not state.uncertainty and not state.assumptions:
            lines.append("- لا تشخيص إضافي؛ الحكم مقتصر على الأدلة المسجلة.")
        lines.extend(
            [
                f"- الثقة الداخلية: {state.confidence:.2f}؛ مؤشر أدلة لا احتمال نجاح مُعايَر.",
                f"- الأدلة: {len(state.evidence)}؛ رأس السلسلة: {self.event_head or 'غير محفوظ'}",
                "",
                "> هذه حالة هندسية تشغيلية، وليست وعياً فينومينولوجياً أو تجربة ذاتية.",
            ]
        )
        return "\n".join(lines)


def render_cognitive_report(
    state: CognitiveState,
    *,
    goal: Goal | None = None,
    criteria: list[CriterionResult] = (),
    diff: WorldDiff | None = None,
    event_head: str = "",
) -> str:
    return CognitiveReport(
        state=state,
        goal=goal,
        criteria=list(criteria),
        diff=diff,
        event_head=event_head,
    ).render()


__all__ = ["CognitiveReport", "render_cognitive_report"]
