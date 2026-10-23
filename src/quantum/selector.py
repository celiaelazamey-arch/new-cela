"""
اختيار المرشحين تحت ميزانية — الجسر بين الكم والقرار الفعلي
==========================================================
هنا لا تُحاكى إلا الفيزياء؛ القرار يمر عبر قاعدة واحدة صريحة:

    لا يُتخذ قرار «مستوحى من الكم» إلا إذا كان **قابلاً للتنفيذ**
    و**أفضل أو مساوياً** للحل الجشع على نفس دالة الطاقة.

هذا يمنع الحالة الأخطر في الأنظمة المشابهة: أن يصبح الكم زينة تُنتج قراراً
أسوأ بلا أن يلاحظ أحد. كل قرار يُرفَق بتقرير مقارنة (`SelectionOutcome`)
يُسجَّل كدليل في `TaskResult`، فيستطيع المدقّق رؤية من فاز ولماذا.

المسألة التي نطبّقها فعلاً: **أدوات السياق تحت سقف نداءات المهمة**.
كل أداة تستهلك حصة، والقيم من ترتيب المستخدم (الأول = الأهم)، والكلفة تقديرية
معلنة (لا مقيسة) في `TOOL_COST_ESTIMATE`.
"""
from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any

from src.quantum.optimizer import Candidate, Problem, Solution, benchmark

# تقدير كلفة كل أداة بوحدات نداء (ليس قياساً — تقدير معلن قابل للتعديل).
TOOL_COST_ESTIMATE: dict[str, float] = {
    "read_file": 1.0,
    "search_files": 2.0,   # يمسح شجرة الملفات
    "list_files": 2.0,
    "run_command": 3.0,    # تنفيذ + موافقة + مهلة
    "write_file": 2.0,
    "replace_text": 2.0,   # كتابة مشروطة بالبصمة + موافقة
}
DEFAULT_TOOL_COST = 1.0


@dataclass
class SelectionOutcome:
    """قرار الاختيار + سببُه + الأدلة التي تبرّره."""

    chosen: list[Any] = field(default_factory=list)
    skipped: list[Any] = field(default_factory=list)
    method: str = "all"
    reasons: list[str] = field(default_factory=list)
    quantum: Solution | None = None
    greedy: Solution | None = None
    optimum: Solution | None = None
    verdict: str = ""

    def render(self) -> str:
        """سطر/سطور تُحقن كدليل — تُقرأ من دون سياق إضافي."""
        names = ", ".join(_label(item) for item in self.chosen) or "لا شيء"
        skipped = ", ".join(_label(item) for item in self.skipped) or "لا شيء"
        lines = [
            f"اختيار أدوات السياق ({self.method}): المختار = {names} | المستبعد = {skipped}",
        ]
        lines.extend(f"• {reason}" for reason in self.reasons)
        if self.verdict:
            lines.append(f"• {self.verdict}")
        return "\n".join(lines)


def _label(item: Any) -> str:
    """اسم مقروء لأي مرشح (ToolCall أو نص)."""
    return getattr(item, "name", None) or getattr(item, "key", None) or str(item)


def cost_of(name: str) -> float:
    return TOOL_COST_ESTIMATE.get(name, DEFAULT_TOOL_COST)


def build_problem(items: list[Any], budget: float) -> tuple[Problem, dict[str, Any]]:
    """
    يحوّل قائمة مرشحين (بترتيب أولوية تنازلي) إلى مسألة QUBO.

    القيمة تنخفض خطياً بالترتيب: الأول = n، الأخير = 1. هذا يُترجم «الأهم أولاً»
    إلى دالة موضوعية صريحة بدل قرار مُضمَر.
    """
    count = len(items)
    candidates: list[Candidate] = []
    lookup: dict[str, Any] = {}
    for index, item in enumerate(items):
        key = _label(item)
        unique = key if key not in lookup else f"{key}#{index}"
        candidates.append(
            Candidate(key=unique, value=float(count - index), cost=cost_of(key))
        )
        lookup[unique] = item
    return Problem(candidates=candidates, budget=budget), lookup


def select_under_budget(
    items: list[Any],
    budget: float,
    seed: int = 7,
    use_quantum: bool = True,
) -> SelectionOutcome:
    """
    يختار أفضل تشكيلة تحت الميزانية، ويقرّر بأمانة بين الكم والجشع.

    الحالات:
      • لا شيء يحتاج اختياراً (الميزانية تكفي، أو لا ميزانية إطلاقاً).
      • الجشع يكفي ⇒ يُستخدم الجشع (أرخص حسابياً) ويُذكر ذلك.
      • الكم أفضل وقابل للتنفيذ ⇒ يُستخدم الكم مع تسجيل المقارنة.
    """
    if not items:
        return SelectionOutcome(method="none", reasons=["لا مرشحين."])

    total_cost = sum(cost_of(_label(item)) for item in items)
    if budget >= total_cost:
        return SelectionOutcome(
            chosen=list(items),
            method="all",
            reasons=[
                (
                    f"الميزانية {budget:g} تكفي كل المرشحين "
                    f"(كلفة تقديرية {total_cost:g}) — لا حاجة لأي تحسين."
                )
            ],
        )

    problem, lookup = build_problem(items, budget)
    if not use_quantum:
        from src.quantum.optimizer import solve_greedy

        greedy = solve_greedy(problem)
        chosen = [lookup[key] for key in greedy.keys]
        chosen_ids = {id(item) for item in chosen}
        return SelectionOutcome(
            chosen=chosen,
            skipped=[item for item in items if id(item) not in chosen_ids],
            greedy=greedy,
            method="greedy",
            reasons=[
                f"تحسين الكم معطَّل: اختير الجشع (قيمة {greedy.value:g}، كلفة {greedy.cost:g})."
            ],
        )

    report = benchmark(problem, seed=seed)
    quantum, greedy = report.quantum_inspired, report.greedy

    use_quantum_result = quantum.feasible and quantum.energy <= greedy.energy + 1e-9
    winner = quantum if use_quantum_result else greedy
    method = "quantum-inspired (SQA)" if use_quantum_result else "greedy"

    reasons = [
        f"الميزانية {budget:g} < الكلفة الكلية {total_cost:g} ⇒ اختيار مطلوب.",
        (
            f"الكمومي: قيمة {quantum.value:g} بكلفة {quantum.cost:g} "
            f"(جدوى: {'نعم' if quantum.feasible else 'لا'}) | "
            f"الجشع: قيمة {greedy.value:g} بكلفة {greedy.cost:g}."
        ),
    ]
    if not use_quantum_result:
        reasons.append("رُفض حل الكم لأنه غير قابل للتنفيذ أو ليس أفضل من الجشع.")

    chosen = [lookup[key] for key in winner.keys]
    chosen_ids = {id(item) for item in chosen}
    return SelectionOutcome(
        chosen=chosen,
        skipped=[item for item in items if id(item) not in chosen_ids],
        quantum=quantum,
        greedy=greedy,
        optimum=report.optimum,
        method=method,
        reasons=reasons,
        verdict=report.verdict,
    )
