"""
موجّه النماذج المجانية (Free Multi-Provider LLM Router)
======================================================
يدعم:
  • Gemini API (الحد المجاني) كمزوّد أساسي — google-genai + langchain-google-genai
  • Groq API (الحد المجاني) كمزوّد بديل سريع — groq + langchain-groq

الهدف: استمرارية التشغيل دون توقف عند تجاوز حدود الاستهلاك (Rate Limits)
أو انتهاء الحصة اليومية، عبر تبديل تلقائي بين المزودات.

قاعدة أمان: لا تُسجَّل المفاتيح ولا تُطبَّع في أي رسالة خطأ.
"""
from __future__ import annotations

import asyncio
import logging
from typing import Any

from src.config import settings

logger = logging.getLogger("new-cela.router")


class LLMRouter:
    """موجّه نماذج مجاني مع تبديل تلقائي بين المزودات عند الفشل."""

    def __init__(self) -> None:
        self.settings = settings
        self._gemini_client: Any | None = None

    # ---------------- التحقق من توفر المفاتيح ----------------
    @property
    def gemini_available(self) -> bool:
        return bool(self.settings.gemini_api_key)

    @property
    def groq_available(self) -> bool:
        return bool(self.settings.groq_api_key)

    def require_any_provider(self) -> None:
        """يرفض التشغيل إن لم يُضبط أي مفتاح — برسالة إرشادية بلا كشف أي بيانات."""
        if not (self.gemini_available or self.groq_available):
            raise RuntimeError(
                "⚠️ لم يتم العثور على أي مفتاح API مجاني.\n"
                "الحل: اضبط GEMINI_API_KEY أو GROQ_API_KEY\n"
                "  • محلياً: في ملف .env (انظر .env.example)\n"
                "  • على GitHub: Settings ← Secrets and variables ← Actions"
            )

    # ---------------- نموذج متوافق مع LangChain (تستخدمه browser-use) ----------------
    def get_chat_model(self) -> Any:
        """يعيد نموذج محادثة متوافقاً مع LangChain/Browser-Use."""
        self.require_any_provider()

        if self.gemini_available:
            from langchain_google_genai import ChatGoogleGenerativeAI

            logger.info("المزود الأساسي: Gemini (%s)", self.settings.agent_primary_model)
            return ChatGoogleGenerativeAI(
                model=self.settings.agent_primary_model,
                google_api_key=self.settings.gemini_api_key,
                temperature=0.2,
            )

        from langchain_groq import ChatGroq

        logger.info("المزود البديل: Groq (%s)", self.settings.agent_fallback_model)
        return ChatGroq(
            model=self.settings.agent_fallback_model,
            groq_api_key=self.settings.groq_api_key,
            temperature=0.2,
        )

    # ---------------- عميل Gemini الأصلي (سريع لخطوات التفكير النصي) ----------------
    def _get_gemini_client(self) -> Any:
        if self._gemini_client is None:
            from google import genai

            self._gemini_client = genai.Client(api_key=self.settings.gemini_api_key)
        return self._gemini_client

    async def _gemini_generate(self, prompt: str) -> str:
        client = self._get_gemini_client()
        response = await asyncio.to_thread(
            client.models.generate_content,
            model=self.settings.agent_primary_model,
            contents=prompt,
        )
        return response.text or ""

    async def _groq_generate(self, prompt: str) -> str:
        from groq import Groq

        client = Groq(api_key=self.settings.groq_api_key)
        response = await asyncio.to_thread(
            client.chat.completions.create,
            model=self.settings.agent_fallback_model,
            messages=[{"role": "user", "content": prompt}],
        )
        return response.choices[0].message.content or ""

    # ---------------- الواجهة الرئيسية: خطوة تفكير واحدة ----------------
    async def execute_reasoning_step(self, prompt: str) -> str:
        """ينفّذ خطوة تفكير واحدة مع تبديل تلقائي عند الفشل."""
        self.require_any_provider()
        failures: list[str] = []

        if self.gemini_available:
            try:
                return await self._gemini_generate(prompt)
            except Exception as exc:  # noqa: BLE001 — مقصود: أي خطأ مزود يفعّل البديل
                logger.warning("فشل Gemini (%s) — التبديل إلى البديل.", type(exc).__name__)
                failures.append(f"gemini:{type(exc).__name__}")

        if self.groq_available:
            try:
                return await self._groq_generate(prompt)
            except Exception as exc:  # noqa: BLE001 — مقصود: تسجيل الفشل ثم رفع الخطأ
                logger.warning("فشل Groq أيضاً (%s).", type(exc).__name__)
                failures.append(f"groq:{type(exc).__name__}")

        raise RuntimeError("فشلت كل المزودات المجانية المتاحة: " + "; ".join(failures))

    # ---------------- غلاف متزامن (sync) ----------------
    def complete(self, prompt: str) -> str:
        """نسخة متزامنة من execute_reasoning_step."""
        return asyncio.run(self.execute_reasoning_step(prompt))
