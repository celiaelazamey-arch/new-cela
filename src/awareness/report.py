"""
التقرير الذاتي (Introspection Report)
====================================
يجمع ثلاث طبقات في تقرير واحد مقروء:

    نموذج الذات (ما أستطيعه) + بؤرة الانتباه (ما نظرت إليه) + الثقة (كم أثق)

وعند إضافته إلى `TaskResult`، يصبح للمستخدم جواب على سؤال لا يجيب عنه أي
وكيل «واثق» عادةً: **ماذا لم تنظر إليه؟ ولماذا؟**

الحد الصريح، مكتوب في كل تقرير: هذا **وعي وظيفي** — بنية بيانات ووصف للحالة —
لا تجربة ذاتية. الادعاء بغير ذلك ليس خطأ هندسياً فقط، بل تضليل للمستخدم.
"""
from __future__ import annotations

from pydantic import BaseModel, Field

from src.awareness.metacognition import ConfidenceEstimate
from src.awareness.self_model import SelfModel, default_self_model
from src.awareness.workspace import WorkspaceState

DISCLAIMER = (
    "⚠️ حد صريح: هذا وعي وظيفي (نموذج ذات + انتباه محدود + تقييم معرفي)، "
    "وليس وعياً فينومينولوجياً ولا تجربة ذاتية."
)


class IntrospectionReport(BaseModel):
    """تقرير ذاتي واحد — كل الأرقام فيه مقيسة، والادعاءات محدودة."""

    self_model: SelfModel = Field(default_factory=default_self_model)
    workspace: WorkspaceState = Field(default_factory=WorkspaceState)
    confidence: ConfidenceEstimate | None = None
    task: str = ""

    def render(self, with_self_model: bool = True) -> str:
        lines: list[str] = []
        if self.task:
            lines.append(f"المهمة: {self.task}")
        if self.confidence is not None:
            lines.append(self.confidence.render())
        if self.workspace.focused or self.workspace.ignored:
            lines.append(self.workspace.render())
        if with_self_model:
            lines.append("")
            lines.append(self.self_model.describe())
        lines.append("")
        lines.append(DISCLAIMER)
        return "\n".join(lines)

    def brief(self) -> str:
        """سطر واحد يُحفظ في الأدلة — بلا إغراق السجل."""
        parts = [self.workspace.attention.describe()]
        if self.confidence is not None:
            parts.append(f"الثقة {self.confidence.score:.2f} ({self.confidence.band})")
        hidden = len(self.workspace.ignored)
        if hidden:
            parts.append(f"مُهمَل: {hidden}")
        return " | ".join(parts)


def main(argv: list[str] | None = None) -> int:
    """`python -m src.awareness.report` — صورة الذات كما هي الآن."""
    import argparse

    parser = argparse.ArgumentParser(
        prog="python -m src.awareness.report",
        description="يعرض نموذج النظام لنفسه (قدرات، حدود، وحد صريح)",
    )
    parser.add_argument("--tests", type=int, default=0, help="عدد الاختبارات الناجحة للعرض")
    parser.add_argument("--json", action="store_true", help="إخراج JSON")
    args = parser.parse_args(argv)

    model = default_self_model(tests_passing=args.tests)
    if args.json:
        print(model.model_dump_json(indent=2))
    else:
        print(model.describe())
        print()
        print(DISCLAIMER)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
