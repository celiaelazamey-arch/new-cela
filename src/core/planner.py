"""
محرك التخطيط الذكي (Planner → Executor → Critic)
================================================
حلقة تفكير متقدمة:
  1. Planner    — يفكّك المهمة إلى خطة من خطوات ملموسة.
  2. Executor   — ينفّذ كل خطوة عبر المزود المجاني.
  3. Critic     — يقيّم النتيجة ذاتياً ويصدر تغذية راجعة تصحيحية.
  4. إعادة المحاولة عند الرفض (بحد أقصى) ثم التركيب النهائي.

آليات الموثوقية:
  • إخراج منظّم (Function Calling عبر output_format) عند توفره في المزود.
  • استخراج نصي احتياطي لـ JSON (يتعامل مع الأسوار والضوضاء).
  • قبول متسامح عند تعذّر التقييم — يمنع الدوران اللانهائي.
  • حدود صارمة: max_steps للخطوات + max_retries لكل خطوة (حماية الحصة المجانية).
"""
from __future__ import annotations

import json
import logging
import re
from typing import TypeVar

from pydantic import ValidationError

from src.core.schemas import CritiqueOutput, PlanOutput, StepRecord, StepStatus
from src.providers.llm_router import LLMRouter

logger = logging.getLogger("new-cela.planner")

T = TypeVar("T")

PLAN_PROMPT = """أنت المخطّط (Planner) في نظام وكيل مستقل.
فكّك المهمة التالية إلى خطة تنفيذية ملموسة (3 إلى 7 خطوات كحد أقصى).

المهمة:
{task}

أعد الإجابة JSON حصراً بهذا الشكل:
{{"steps": ["وصف الخطوة الأولى", "وصف الخطوة الثانية", ...]}}"""

EXECUTE_PROMPT = """أنت المنفّذ (Executor) في نظام وكيل مستقل.

المهمة الأصلية:
{task}

الخطوة الحالية ({index}/{total}):
{description}
{feedback_block}
نفّذ هذه الخطوة بدقة وأعد النتيجة التفصيلية فقط."""

CRITIQUE_PROMPT = """أنت المقيّم (Critic) في نظام وكيل مستقل. قيّم نتيجة تنفيذ خطوة بصراحة وموضوعية.

المهمة الأصلية:
{task}

الخطوة المطلوبة:
{description}

النتيجة المنفّذة:
{result}

أعد الإجابة JSON حصراً بهذا الشكل:
{{"passed": true أو false, "score": رقم من 0 إلى 10, "feedback": "تحليلك", "suggestion": "كيفية التحسين إن فشلت"}}"""

SYNTHESIS_PROMPT = """أنت المنسّق النهائي في نظام وكيل مستقل.

المهمة الأصلية:
{task}

نتائج الخطوات ({done} ناجحة، {failed} فاشلة):
{summary}

اكتب الإجابة النهائية الشاملة والمفصّلة للمستخدم بناءً على النتائج أعلاه.
اذكر بوضوح أي خطوة فشلت إن وُجدت. ابدأ سطر الإجابة النهائية بـ: FINAL ANSWER:"""

_JSON_FENCE_RE = re.compile(r"```(?:json)?\s*(\{.*?\})\s*```", re.DOTALL)
_JSON_OBJECT_RE = re.compile(r"\{.*\}", re.DOTALL)


def extract_json_object(text: str) -> dict | None:
    """
    يستخرج أول كائن JSON صالح من رد النموذج.
    يتعامل مع: أسوار ```json، النص المحيط، والمحاولات الفاشلة (يعيد None).
    """
    fence = _JSON_FENCE_RE.search(text)
    candidate = fence.group(1) if fence else None

    if candidate is None:
        match = _JSON_OBJECT_RE.search(text)
        candidate = match.group(0) if match else None

    if candidate is None:
        return None

    try:
        data = json.loads(candidate)
    except json.JSONDecodeError:
        return None

    return data if isinstance(data, dict) else None


class Planner:
    """محرك التخطيط الذكي — يقود الوكيل من المهمة إلى الإجابة النهائية."""

    def __init__(
        self,
        router: LLMRouter | None = None,
        max_steps: int | None = None,
        max_retries: int = 2,
    ) -> None:
        self.router = router or LLMRouter()
        self.max_steps = max_steps or self.router.settings.agent_max_steps
        self.max_retries = max(1, max_retries)
        self.history: list[StepRecord] = []

    # ================= الواجهة الرئيسية =================
    async def solve(self, task: str) -> str:
        """يشغّل الحلقة الكاملة: تخطيط ← تنفيذ وتقييم ← تركيب."""
        steps = await self._create_plan(task)
        logger.info("الخطة المعتمدة: %d خطوة/خطوات", len(steps))

        self.history = []
        total = min(len(steps), self.max_steps)
        for index, description in enumerate(steps[: self.max_steps], start=1):
            record = await self._run_step(task, index, total, description)
            self.history.append(record)

        return await self._synthesize(task, self.history)

    # ================= المرحلة 1: التخطيط (Planner) =================
    async def _create_plan(self, task: str) -> list[str]:
        """يفكّك المهمة لخطوات — بإخراج منظّم ثم استخراج نصي ثم خطوة واحدة كحل أخير."""
        prompt = PLAN_PROMPT.format(task=task)

        plan = await self._try_structured(prompt, PlanOutput)
        if plan is not None:
            steps = [s.strip() for s in plan.steps if s.strip()]
            if steps:
                return steps[: self.max_steps]

        for attempt in range(1, 3):
            response = await self.router.execute_reasoning_step(prompt)
            data = extract_json_object(response)
            if data is not None:
                try:
                    steps = [s.strip() for s in PlanOutput.model_validate(data).steps if s.strip()]
                    if steps:
                        return steps[: self.max_steps]
                except ValidationError:
                    logger.warning("خطة غير صالحة (محاولة %d) — إعادة الصياغة.", attempt)
            prompt += "\n\n⚠️ ردك السابق لم يكن JSON صالحاً. أعد المحاولة بصيغة JSON حصراً."

        logger.warning("تعذّر تحليل الخطة — التنفيذ كخطوة واحدة.")
        return [task]

    # ================= المرحلة 2+3: التنفيذ والتقييم (Executor + Critic) =================
    async def _run_step(self, task: str, index: int, total: int, description: str) -> StepRecord:
        """ينفّذ خطوة واحدة مع تقييم ذاتي وإعادة محاولة تصحيحية."""
        record = StepRecord(index=index, description=description, status=StepStatus.IN_PROGRESS)
        feedback = ""

        for attempt in range(1, self.max_retries + 1):
            record.attempts = attempt
            feedback_block = (
                f"\n⚠️ تغذية راجعة تصحيحية من المحاولة السابقة:\n{feedback}\n" if feedback else ""
            )
            result = await self.router.execute_reasoning_step(
                EXECUTE_PROMPT.format(
                    task=task,
                    index=index,
                    total=total,
                    description=description,
                    feedback_block=feedback_block,
                )
            )
            record.result = result

            critique = await self._critique(task, description, result)
            if critique is None:
                # تعذّر التقييم — قبول متسامح لتجنّب الدوران اللانهائي
                record.status = StepStatus.DONE
                record.critiques.append("تقييم غير متوفر — تم القبول افتراضياً")
                logger.info("✓ الخطوة %d مقبولة (تقييم غير متوفر)", index)
                return record

            record.critiques.append(f"[{critique.score}/10] {critique.feedback}")

            if critique.passed:
                record.status = StepStatus.DONE
                logger.info("✓ الخطوة %d نجحت (%d/10)", index, critique.score)
                return record

            feedback = critique.suggestion or critique.feedback
            logger.warning(
                "✗ الخطوة %d مرفوضة (%d/10) — إعادة المحاولة %d/%d",
                index,
                critique.score,
                attempt,
                self.max_retries,
            )

        record.status = StepStatus.FAILED
        return record

    async def _critique(self, task: str, description: str, result: str) -> CritiqueOutput | None:
        """يقيّم نتيجة الخطوة — بإخراج منظّم ثم استخراج نصي؛ None عند التعذّر."""
        prompt = CRITIQUE_PROMPT.format(task=task, description=description, result=result)

        critique = await self._try_structured(prompt, CritiqueOutput)
        if critique is not None:
            return critique

        response = await self.router.execute_reasoning_step(prompt)
        data = extract_json_object(response)
        if data is not None:
            try:
                return CritiqueOutput.model_validate(data)
            except ValidationError:
                logger.warning("تقييم غير صالح — قبول النتيجة افتراضياً.")
        return None

    # ================= المرحلة 4: التركيب النهائي =================
    async def _synthesize(self, task: str, records: list[StepRecord]) -> str:
        """يجمع نتائج كل الخطوات في إجابة نهائية واحدة."""
        summary_lines = []
        for record in records:
            mark = "✓" if record.succeeded else "✗"
            summary_lines.append(
                f"{mark} الخطوة {record.index}: {record.description}\n"
                f"   النتيجة: {record.result}"
            )
        summary = "\n".join(summary_lines) if summary_lines else "(لا توجد خطوات منفّذة)"
        done = sum(1 for r in records if r.succeeded)
        failed = len(records) - done

        prompt = SYNTHESIS_PROMPT.format(task=task, summary=summary, done=done, failed=failed)
        response = await self.router.execute_reasoning_step(prompt)

        if "FINAL ANSWER:" in response:
            return response.split("FINAL ANSWER:", 1)[1].strip()
        return response

    # ================= أداة مساعدة: الإخراج المنظّم =================
    async def _try_structured(self, prompt: str, model: type[T]) -> T | None:
        """يحصل على كائن منظّم عبر Function Calling؛ None إذا تعذّر."""
        try:
            return await self.router.execute_structured_step(prompt, model)
        except Exception as exc:  # noqa: BLE001 — مقصود: أي فشل يتحول للمسار النصي
            logger.warning("الإخراج المنظّم غير متاح (%s) — التحويل للنص.", type(exc).__name__)
            return None
