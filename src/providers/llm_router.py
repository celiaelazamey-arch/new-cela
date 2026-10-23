"""
موجّه النماذج المجانية (Free Multi-Provider LLM Router)
======================================================
يعتمد على نماذج المحادثة الأصلية المدمجة في browser-use (بدون LangChain):
  • browser_use.llm.google.ChatGoogle — Gemini API (الحد المجاني) — مزود أساسي
  • browser_use.llm.groq.ChatGroq     — Groq API (الحد المجاني) — مزود بديل

الهدف: استمرارية التشغيل دون توقف عند تجاوز حدود الاستهلاك (Rate Limits)
أو انتهاء الحصة اليومية، عبر تبديل تلقائي بين المزودات.

ملاحظة معمارية: تم تجنب حزم langchain-google-genai / langchain-groq لأنها
تتعارض مع تثبيتات browser-use (تعارض ماسات موثّق في README).

قاعدة أمان: لا تُسجَّل المفاتيح ولا تُطبَّع في أي رسالة خطأ.
"""
from __future__ import annotations

import asyncio
import logging
from typing import Any

from src.config import settings

logger = logging.getLogger("new-cela.router")

GEMINI = "gemini"
GROQ = "groq"


class LLMRouter:
    """موجّه نماذج مجاني مع تبديل تلقائي بين المزودات عند الفشل."""

    def __init__(self) -> None:
        self.settings = settings
        self._models: dict[str, Any] = {}

    # ---------------- التحقق من توفر المفاتيح ----------------
    @property
    def gemini_available(self) -> bool:
        return bool(self.settings.gemini_api_key)

    @property
    def groq_available(self) -> bool:
        return bool(self.settings.groq_api_key)

    def _provider_order(self) -> list[str]:
        """المزودات المهيأة بترتيب الأولوية: Gemini ثم Groq."""
        order: list[str] = []
        if self.gemini_available:
            order.append(GEMINI)
        if self.groq_available:
            order.append(GROQ)
        return order

    def require_any_provider(self) -> None:
        """يرفض التشغيل إن لم يُضبط أي مفتاح — برسالة إرشادية بلا كشف أي بيانات."""
        if not self._provider_order():
            raise RuntimeError(
                "⚠️ لم يتم العثور على أي مفتاح API مجاني.\n"
                "الحل: اضبط GEMINI_API_KEY أو GROQ_API_KEY\n"
                "  • محلياً: في ملف .env (انظر .env.example)\n"
                "  • على GitHub: Settings ← Secrets and variables ← Actions"
            )

    # ---------------- بناء النماذج ----------------
    def _build_model(self, provider: str) -> Any:
        if provider == GEMINI:
            from browser_use.llm import ChatGoogle

            logger.info("بناء نموذج Gemini (%s)", self.settings.agent_primary_model)
            return ChatGoogle(
                model=self.settings.agent_primary_model,
                api_key=self.settings.gemini_api_key,
                temperature=0.2,
            )

        from browser_use.llm import ChatGroq

        logger.info("بناء نموذج Groq (%s)", self.settings.agent_fallback_model)
        return ChatGroq(
            model=self.settings.agent_fallback_model,
            api_key=self.settings.groq_api_key,
            temperature=0.2,
        )

    def _get_model(self, provider: str) -> Any:
        """يبني النموذج مرة واحدة ويخزّنه (يمنع استنزاف الاتصالات)."""
        if provider not in self._models:
            self._models[provider] = self._build_model(provider)
        return self._models[provider]

    def get_chat_model(self) -> Any:
        """نموذج المحادثة الأساسي — تستخدمه وحدة المتصفح (browser-use)."""
        self.require_any_provider()
        return self._get_model(self._provider_order()[0])

    # ---------------- الواجهة الرئيسية: خطوة تفكير واحدة ----------------
    async def execute_reasoning_step(self, prompt: str) -> str:
        """ينفّذ خطوة تفكير واحدة مع تبديل تلقائي عند الفشل."""
        providers = self._provider_order()
        if not providers:
            self.require_any_provider()  # يرفع الخطأ الإرشادي

        from browser_use.llm import UserMessage

        failures: list[str] = []
        for provider in providers:
            try:
                model = self._get_model(provider)
                response = await model.ainvoke([UserMessage(content=prompt)])
                return response.completion
            except Exception as exc:  # noqa: BLE001 — مقصود: أي خطأ مزود يفعّل البديل
                logger.warning("فشل المزود %s (%s) — تبديل.", provider, type(exc).__name__)
                failures.append(f"{provider}:{type(exc).__name__}")

        raise RuntimeError("فشلت كل المزودات المجانية المتاحة: " + "; ".join(failures))

    # ---------------- غلاف متزامن (sync) ----------------
    def complete(self, prompt: str) -> str:
        """نسخة متزامنة من execute_reasoning_step."""
        return asyncio.run(self.execute_reasoning_step(prompt))
