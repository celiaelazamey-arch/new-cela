"""
اختبارات دخان سريعة لنظام new-cela
===================================
تتحقق من سلامة البنية والواردات وضوابط الأمان — بدون أي مفاتيح API.
"""
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
    import asyncio

    from src.execution.sandbox import SandboxExecutor

    executor = SandboxExecutor()
    with pytest.raises(PermissionError):
        asyncio.run(executor.run("rm -rf /"))


def test_sandbox_runs_allowed_command():
    """الأمر المسموح يُنفّذ فعلياً ويعيد نتيجة مهيكلة."""
    import asyncio

    from src.execution.sandbox import SandboxExecutor

    result = asyncio.run(SandboxExecutor().run("echo hello-new-cela"))
    assert result.returncode == 0
    assert "hello-new-cela" in result.stdout


def test_planner_stops_at_final_answer():
    """حلقة ReAct تتوقف فوراً عند ظهور FINAL ANSWER."""
    import asyncio

    from src.core.planner import Planner
    from src.providers.llm_router import LLMRouter

    class FakeRouter(LLMRouter):
        async def execute_reasoning_step(self, prompt: str) -> str:
            return "Thought: اكتملت المهمة.\nFINAL ANSWER: 42"

    planner = Planner(router=FakeRouter(), max_steps=10)
    answer = asyncio.run(planner.solve("ما معنى الحياة؟"))
    assert answer == "42"
