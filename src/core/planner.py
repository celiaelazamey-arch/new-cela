"""
نواة التفكير (Planner / ReAct Loop)
===================================
حلقة تفكير منظمة: Thought → Action → Observation
مع سجل تراكمي وحد أقصى للخطوات لمنع الدوران اللانهائي واستنزاف الحصة المجانية.
"""
from __future__ import annotations

import logging

from src.providers.llm_router import LLMRouter

logger = logging.getLogger("new-cela.planner")

REACT_TEMPLATE = """أنت وكيل منطقي مستقل (ReAct). فكّر خطوة بخطوة بلغة عربية واضحة.

المهمة الحالية:
{task}

الخطوات السابقة وملاحظاتها:
{history}

قواعد الإخراج (التزم بها حرفياً):
1. ابدأ كل خطوة بـ "Thought:" ثم "Action:" ثم "Observation:".
2. إذا اكتملت المهمة، أنهِ ردك بالسطر: FINAL ANSWER: <الإجابة النهائية>
3. لا تخترع معلومات غير مدعومة بالملاحظات.
"""


class Planner:
    """منسّق التفكير عالي المستوى — يقود الوكيل حتى الإجابة النهائية."""

    def __init__(
        self,
        router: LLMRouter | None = None,
        max_steps: int | None = None,
    ) -> None:
        self.router = router or LLMRouter()
        self.max_steps = max_steps or self.router.settings.agent_max_steps

    def _build_prompt(self, task: str, history: list[str]) -> str:
        history_text = "\n".join(history) if history else "(لا توجد خطوات سابقة)"
        return REACT_TEMPLATE.format(task=task, history=history_text)

    async def solve(self, task: str) -> str:
        """يشغّل حلقة ReAct ويعيد الإجابة النهائية."""
        history: list[str] = []

        for step in range(1, self.max_steps + 1):
            logger.info("خطوة التفكير %d/%d", step, self.max_steps)
            thought = await self.router.execute_reasoning_step(
                self._build_prompt(task, history)
            )
            history.append(thought)

            if "FINAL ANSWER:" in thought:
                return thought.split("FINAL ANSWER:", 1)[1].strip()

        return (
            f"توقفت الحلقة بعد {self.max_steps} خطوة دون الوصول لإجابة نهائية.\n"
            f"آخر خطوة:\n{history[-1]}"
        )
