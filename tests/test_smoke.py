"""
اختبارات نظام new-cela
=======================
تتحقق من سلامة البنية والواجهات وضوابط الأمان وحلقة التخطيط — بدون أي مفاتيح API.
"""
import asyncio
import importlib

import pytest


def test_package_imports():
    """كل الوحدات تستورد بنجاح."""
    modules = (
        "src",
        "src.config",
        "src.providers",
        "src.providers.llm_router",
        "src.core",
        "src.core.schemas",
        "src.core.memory",
        "src.core.planner",
        "src.execution",
        "src.execution.sandbox",
        "src.browser",
        "src.browser.browser_engine",
        "src.main",
    )
    for module in modules:
        assert importlib.import_module(module) is not None


def test_router_requires_api_key():
    """يرفض التشغيل بدون أي مفتاح — ضمان عدم وجود مسار تشغيل صامت."""
    from src.config import Settings
    from src.providers.llm_router import LLMRouter

    router = LLMRouter()
    router.settings = Settings(
        _env_file=None,
        gemini_api_key=None,
        groq_api_key=None,
        openrouter_api_key=None,
    )
    with pytest.raises(RuntimeError, match="مفتاح API"):
        router.require_any_provider()


def test_sandbox_rejects_unknown_command():
    """القائمة البيضاء تمنع الأوامر الخطرة."""
    from src.execution.sandbox import SandboxExecutor

    executor = SandboxExecutor()
    with pytest.raises(PermissionError):
        asyncio.run(executor.run("rm -rf /"))


def test_sandbox_runs_allowed_command():
    """الأمر المسموح يُنفّذ فعلياً ويعيد نتيجة مهيكلة."""
    from src.execution.sandbox import SandboxExecutor

    result = asyncio.run(SandboxExecutor().run("echo hello-new-cela"))
    assert result.returncode == 0
    assert "hello-new-cela" in result.stdout


# ================= اختبارات استخراج JSON =================

def test_extract_json_object():
    """يستخرج JSON من الأسوار ومن النص المحيط ويرفض غير الصالح."""
    from src.core.planner import extract_json_object

    assert extract_json_object('```json\n{"a": 1}\n```') == {"a": 1}
    assert extract_json_object("نص قبل {\"b\": 2} نص بعد") == {"b": 2}
    assert extract_json_object('```\n{"c": [1, 2]}\n```') == {"c": [1, 2]}
    assert extract_json_object("لا يوجد JSON هنا") is None
    assert extract_json_object("{JSON غير صالح}") is None


# ================= اختبارات محرك التخطيط الذكي =================

class ScriptedRouter:
    """موجّه وهمي يرد بردود مبرمجة بالتسلسل — لاختبار الحلقة بدون شبكة."""

    def __init__(self, text_responses=None, structured_responses=None):
        from src.config import Settings

        self.text_responses = list(text_responses or [])
        self.structured_responses = dict(structured_responses or {})
        self.prompts: list[str] = []
        self.settings = Settings(_env_file=None)

    async def execute_reasoning_step(self, prompt: str) -> str:
        self.prompts.append(prompt)
        if self.text_responses:
            return self.text_responses.pop(0)
        return "FINAL ANSWER: رد افتراضي"

    async def execute_structured_step(self, prompt: str, output_format):
        self.prompts.append(prompt)
        return self.structured_responses.get(output_format)


def test_planner_happy_path_text_json():
    """الحلقة الكاملة بالمسار النصي: خطة ← تنفيذ ← تقييم ← تركيب."""
    from src.core.planner import Planner

    router = ScriptedRouter(
        text_responses=[
            '{"steps": ["اجمع البيانات", "حلّل البيانات"]}',
            "بيانات الخطوة الأولى: أرقام ومصادر.",
            '{"passed": true, "score": 9, "feedback": "ممتاز", "suggestion": ""}',
            "تحليل الخطوة الثانية: استنتاجات مدعومة.",
            '{"passed": true, "score": 8, "feedback": "جيد", "suggestion": ""}',
            "الخلاصة الشاملة.\nFINAL ANSWER: الإجابة النهائية",
        ]
    )
    planner = Planner(router=router, max_retries=2)
    answer = asyncio.run(planner.solve("مهمة اختبار"))

    assert answer == "الإجابة النهائية"
    assert len(planner.history) == 2
    assert all(record.succeeded for record in planner.history)
    assert all(record.attempts == 1 for record in planner.history)


def test_planner_structured_output_path():
    """يعمل بالإخراج المنظّم (Function Calling) عندما يدعمه المزود."""
    from src.core.planner import Planner
    from src.core.schemas import CritiqueOutput, PlanOutput

    router = ScriptedRouter(
        structured_responses={
            PlanOutput: PlanOutput(steps=["خطوة منفّذة"]),
            CritiqueOutput: CritiqueOutput(passed=True, score=10, feedback="ممتاز"),
        },
        text_responses=["نتيجة التنفيذ", "FINAL ANSWER: تم الإنجاز"],
    )
    planner = Planner(router=router)
    answer = asyncio.run(planner.solve("مهمة"))

    assert answer == "تم الإنجاز"
    assert len(planner.history) == 1
    assert planner.history[0].succeeded


def test_planner_retries_after_failed_critique():
    """يرفض المقيّم المحاولة الأولى ← يُعاد التنفيذ بالتغذية الراجعة ← ينجح."""
    from src.core.planner import Planner

    router = ScriptedRouter(
        text_responses=[
            '{"steps": ["خطوة واحدة"]}',
            "محاولة أولى ناقصة",
            '{"passed": false, "score": 4, "feedback": "ناقص", "suggestion": "أضف تفاصيل وأرقاماً"}',
            "محاولة ثانية محسّنة بتفاصيل وأرقام",
            '{"passed": true, "score": 9, "feedback": "جيد الآن", "suggestion": ""}',
            "FINAL ANSWER: الإجابة بعد التحسين",
        ]
    )
    planner = Planner(router=router, max_retries=2)
    answer = asyncio.run(planner.solve("مهمة"))

    assert answer == "الإجابة بعد التحسين"
    assert planner.history[0].attempts == 2
    assert planner.history[0].succeeded
    assert len(planner.history[0].critiques) == 2
    # التغذية الراجعة (اقتراح المقيّم) وصلت فعلاً إلى منفّذ المحاولة الثانية
    assert any("أضف تفاصيل" in prompt for prompt in router.prompts)


def test_planner_marks_step_failed_after_max_retries():
    """استنفاد إعادة المحاولات ⇒ الخطوة تُعلَّم فاشلة بأمانة."""
    from src.core.planner import Planner
    from src.core.schemas import StepStatus

    router = ScriptedRouter(
        text_responses=[
            '{"steps": ["خطوة صعبة"]}',
            "محاولة 1",
            '{"passed": false, "score": 2, "feedback": "ضعيف", "suggestion": "حسّن"}',
            "محاولة 2",
            '{"passed": false, "score": 3, "feedback": "لا يزال ضعيفاً", "suggestion": "حسّن"}',
            "FINAL ANSWER: ملخص يذكر الفشل",
        ]
    )
    planner = Planner(router=router, max_retries=2)
    answer = asyncio.run(planner.solve("مهمة"))

    assert planner.history[0].status is StepStatus.FAILED
    assert planner.history[0].attempts == 2
    assert not planner.history[0].succeeded
    assert "ملخص" in answer


def test_invalid_plan_falls_back_to_single_step():
    """خطة غير قابلة للتحليل ⇒ التنفيذ كخطوة واحدة بدلاً من الانهيار."""
    from src.core.planner import Planner

    router = ScriptedRouter(
        text_responses=[
            "ليس JSON على الإطلاق",
            "ولا هذه المرة",
            "نتيجة التنفيذ المباشر",
            '{"passed": true, "score": 7, "feedback": "مقبول", "suggestion": ""}',
            "FINAL ANSWER: الإجابة",
        ]
    )
    planner = Planner(router=router)
    answer = asyncio.run(planner.solve("مهمة بسيطة"))

    assert len(planner.history) == 1
    assert planner.history[0].description == "مهمة بسيطة"
    assert planner.history[0].succeeded
    assert answer == "الإجابة"


def test_cli_parser_accepts_all_flags():
    """واجهة CLI تعرّف كل الوسائط — يمنع AttributeError وقت التشغيل."""
    from src.main import build_parser

    args = build_parser().parse_args(
        ["--task", "مهمة", "--mode", "reason", "--max-steps", "5", "--max-retries", "3"]
    )
    assert args.task == "مهمة"
    assert args.mode == "reason"
    assert args.max_steps == 5
    assert args.max_retries == 3

    defaults = build_parser().parse_args(["--task", "مهمة"])
    assert defaults.mode == "reason"
    assert defaults.max_retries == 2


def test_max_steps_caps_plan_execution():
    """max_steps يحدّ عدد الخطوات المنفّذة فعلياً."""
    from src.core.planner import Planner

    router = ScriptedRouter(
        text_responses=[
            '{"steps": ["أ", "ب", "ج", "د"]}',
            "نتيجة أ", '{"passed": true, "score": 9, "feedback": "ok"}',
            "نتيجة ب", '{"passed": true, "score": 9, "feedback": "ok"}',
            "FINAL ANSWER: انتهى",
        ]
    )
    planner = Planner(router=router, max_steps=2)
    asyncio.run(planner.solve("مهمة"))

    assert len(planner.history) == 2
    assert [r.description for r in planner.history] == ["أ", "ب"]
