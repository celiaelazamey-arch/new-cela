"""
موجّه النماذج المجانية (Free Multi-Provider LLM Router)
======================================================
يستخدم **العملاء الرسميين** مباشرة (بلا وسيط):
  • `google-genai` → Gemini API (الحد المجاني) — المزود الأساسي
  • `groq`         → Groq API (الحد المجاني) — المزود البديل

الهدف: استمرارية التشغيل دون توقف عند تجاوز حدود الاستهلاك (Rate Limits)
أو انتهاء الحصة اليومية، عبر تبديل تلقائي بين المزودات + **قاطع دائرة**
(circuit breaker) يمنع الضغط على مزود متعثر.

📌 **قرار معماري (2026-10-23):** كان هذا الموجّه يعتمد على `browser_use.llm`
(ChatGoogle/ChatGroq). ذلك الارتباط كان يثبّت `browser-use 0.9.7` الذي يُجبر
`aiohttp==3.12.15` (72 ثغرة معروفة). بفكّ الارتباط صار بإمكان نقل `browser-use`
إلى متطلبات اختيارية، فيصبح التثبيت الافتراضي خالياً من تلك الثغرات.
راجع `SECURITY.md` و`docs/security-threat-model.md`.

قواعد أمان:
  • لا تُسجَّل المفاتيح ولا تُطبَّع في أي رسالة خطأ.
  • المزود المتعثر يُفتح له قاطع الدائرة لفترة تهدئة (افتراضي 60 ثانية).
  • `get_chat_model()` يُبقي دعم المتصفح لكنه **اختياري** ويُحمَّل عند الطلب فقط.
"""
from __future__ import annotations

import asyncio
import json
import logging
import time
from typing import Any, TypeVar

from src.config import settings

logger = logging.getLogger("new-cela.router")

GEMINI = "gemini"
GROQ = "groq"

T = TypeVar("T")

# إعدادات قاطع الدائرة — قابلة للضبط عبر متغيرات البيئة لاحقاً.
CB_FAILURE_THRESHOLD = 3  # عدد الإخفاقات قبل فتح الدائرة
CB_COOLDOWN_SECONDS = 60.0  # فترة التهدئة قبل محاولة «نصف مفتوحة»


class CircuitBreaker:
    """
    قاطع دائرة بسيط لكل مزود.

    مغلق (closed) → يعمل طبيعياً. بعد `threshold` إخفاقات متتالية يُفتح
    (open) فيُستبعد المزود من الترتيب حتى تنقضي فترة التهدئة، ثم يُجرَّب
    من جديد (نصف مفتوح). يمنع الضغط المتكرر على مزود متعثر (cascading).
    """

    def __init__(self, threshold: int = CB_FAILURE_THRESHOLD,
                 cooldown: float = CB_COOLDOWN_SECONDS) -> None:
        self.threshold = max(1, int(threshold))
        self.cooldown = float(cooldown)
        self._failures: dict[str, int] = {}
        self._opened_at: dict[str, float] = {}

    def allow(self, provider: str) -> bool:
        """هل يُسمح بمحاولة هذا المزود الآن؟"""
        opened = self._opened_at.get(provider)
        if opened is None:
            return True
        if (time.monotonic() - opened) >= self.cooldown:
            # نصف مفتوح: صفّر العداد واسمح بمحاولة واحدة.
            self._opened_at.pop(provider, None)
            self._failures.pop(provider, None)
            logger.info("قاطع الدائرة: إعادة تجربة المزود %s بعد التهدئة.", provider)
            return True
        return False

    def record_success(self, provider: str) -> None:
        self._failures.pop(provider, None)
        self._opened_at.pop(provider, None)

    def record_failure(self, provider: str) -> None:
        count = self._failures.get(provider, 0) + 1
        self._failures[provider] = count
        if count >= self.threshold:
            self._opened_at[provider] = time.monotonic()
            logger.warning(
                "قاطع الدائرة: فتح دائرة المزود %s بعد %d إخفاقات (تهدئة %.0fs).",
                provider, count, self.cooldown,
            )

    def snapshot(self) -> dict[str, Any]:
        """لقطة حالة للمراقبة/الصحة."""
        return {
            "failures": dict(self._failures),
            "open": {
                p: round(self.cooldown - (time.monotonic() - t), 1)
                for p, t in self._opened_at.items()
            },
        }


class LLMRouter:
    """موجّه نماذج مجاني مع تبديل تلقائي وقاطع دائرة لكل مزود."""

    def __init__(self) -> None:
        self.settings = settings
        self._clients: dict[str, Any] = {}
        self.circuit_breaker = CircuitBreaker()

    # ---------------- التحقق من توفر المفاتيح ----------------
    @property
    def gemini_available(self) -> bool:
        return bool(self.settings.gemini_api_key)

    @property
    def groq_available(self) -> bool:
        return bool(self.settings.groq_api_key)

    def _provider_order(self) -> list[str]:
        """المزودات المهيأة بترتيب الأولوية، مع استبعاد دوائر القاطع المفتوحة."""
        order: list[str] = []
        if self.gemini_available:
            order.append(GEMINI)
        if self.groq_available:
            order.append(GROQ)
        return [p for p in order if self.circuit_breaker.allow(p)]

    def require_any_provider(self) -> None:
        """يرفض التشغيل إن لم يُضبط أي مفتاح — برسالة إرشادية بلا كشف أي بيانات."""
        if not (self.gemini_available or self.groq_available):
            raise RuntimeError(
                "⚠️ لم يتم العثور على أي مفتاح API مجاني.\n"
                "الحل: اضبط GEMINI_API_KEY أو GROQ_API_KEY\n"
                "  • محلياً: في ملف .env (انظر .env.example)\n"
                "  • على GitHub: Settings ← Secrets and variables ← Actions"
            )

    # ---------------- بناء العملاء الرسميين ----------------
    def _get_client(self, provider: str) -> Any:
        """يبني عميل المزود الرسمي مرة واحدة ويخزّنه (يمنع استنزاف الاتصالات)."""
        if provider not in self._clients:
            if provider == GEMINI:
                from google import genai

                logger.info("بناء عميل Gemini (%s)", self.settings.agent_primary_model)
                self._clients[provider] = genai.Client(
                    api_key=self.settings.gemini_api_key
                )
            else:
                from groq import AsyncGroq

                logger.info("بناء عميل Groq (%s)", self.settings.agent_fallback_model)
                self._clients[provider] = AsyncGroq(
                    api_key=self.settings.groq_api_key
                )
        return self._clients[provider]

    async def _invoke_text(self, provider: str, prompt: str) -> str:
        """نداء نصّي واحد على مزود محدد."""
        if provider == GEMINI:
            client = self._get_client(provider)
            response = await client.aio.models.generate_content(
                model=self.settings.agent_primary_model,
                contents=prompt,
            )
            return response.text or ""
        client = self._get_client(provider)
        response = await client.chat.completions.create(
            model=self.settings.agent_fallback_model,
            messages=[{"role": "user", "content": prompt}],
            temperature=0.2,
        )
        return response.choices[0].message.content or ""

    async def _invoke_structured(self, provider: str, prompt: str,
                                 output_format: type[T]) -> T | None:
        """نداء بإخراج منظّم — يعيد كائناً من النوع المطلوب أو None."""
        try:
            if provider == GEMINI:
                from google.genai import types

                client = self._get_client(provider)
                response = await client.aio.models.generate_content(
                    model=self.settings.agent_primary_model,
                    contents=prompt,
                    config=types.GenerateContentConfig(
                        response_mime_type="application/json",
                        response_schema=output_format,
                        temperature=0.2,
                    ),
                )
                text = response.text or ""
            else:
                client = self._get_client(provider)
                response = await client.chat.completions.create(
                    model=self.settings.agent_fallback_model,
                    messages=[{"role": "user", "content": prompt}],
                    temperature=0.2,
                    response_format={"type": "json_object"},
                )
                text = response.choices[0].message.content or ""

            payload = json.loads(text)
            # يتحقق من المخطط عبر pydantic إن أمكن، وإلا يعيد القاموس كما هو.
            if hasattr(output_format, "model_validate"):
                return output_format.model_validate(payload)
            return payload
        except Exception as exc:  # noqa: BLE001 — فشل الإخراج المنظّم يفعّل البديل
            logger.warning(
                "تعذّر الإخراج المنظّم عبر %s (%s) — تجربة البديل.",
                provider, type(exc).__name__,
            )
            return None

    # ---------------- الواجهة العامة (العقد القديم محفوظ) ----------------
    async def execute_reasoning_step(self, prompt: str) -> str:
        """ينفّذ خطوة تفكير واحدة مع تبديل تلقائي عند الفشل."""
        self.require_any_provider()
        providers = self._provider_order() or [GEMINI, GROQ]
        if not any(self.circuit_breaker.allow(p) for p in providers):
            raise RuntimeError("كل المزودات في حالة تهدئة (circuit breaker) — حاول لاحقاً.")

        failures: list[str] = []
        for provider in providers:
            if not self.circuit_breaker.allow(provider):
                continue
            # تخطي المزود غير المهيأ
            if provider == GEMINI and not self.gemini_available:
                continue
            if provider == GROQ and not self.groq_available:
                continue
            try:
                result = await self._invoke_text(provider, prompt)
                self.circuit_breaker.record_success(provider)
                return result
            except Exception as exc:  # noqa: BLE001 — أي خطأ مزود يفعّل البديل
                logger.warning("فشل المزود %s (%s) — تبديل.", provider, type(exc).__name__)
                self.circuit_breaker.record_failure(provider)
                failures.append(f"{provider}:{type(exc).__name__}")

        raise RuntimeError("فشلت كل المزودات المجانية المتاحة: " + "; ".join(failures))

    async def execute_structured_step(self, prompt: str, output_format: type[T]) -> T | None:
        """
        ينفّذ خطوة بإخراج منظّم عبر المزود الأساسي ثم البديل.
        يعيد None إذا فشل المزودان — ليلجأ المتصل للاستخراج النصي.
        """
        self.require_any_provider()
        for provider in self._provider_order():
            if provider == GEMINI and not self.gemini_available:
                continue
            if provider == GROQ and not self.groq_available:
                continue
            result = await self._invoke_structured(provider, prompt, output_format)
            if result is not None:
                self.circuit_breaker.record_success(provider)
                return result
            self.circuit_breaker.record_failure(provider)
        logger.warning("تعذّر الإخراج المنظّم عبر كل المزودات: %s", output_format.__name__)
        return None

    # ---------------- دعم صريح لمزود محدد (يستخدمه model_api) ----------------
    async def execute_reasoning_step_for(self, provider: str, prompt: str) -> str:
        """نداء نصّي على مزود محدد دون تبديل داخلي مزدوج."""
        try:
            result = await self._invoke_text(provider, prompt)
            self.circuit_breaker.record_success(provider)
            return result
        except Exception as exc:  # يُسجَّل ويُعاد رفعه للطبقة الأعلى
            self.circuit_breaker.record_failure(provider)
            raise RuntimeError(f"{provider}:{type(exc).__name__}") from exc

    async def execute_structured_step_for(self, provider: str, prompt: str,
                                          output_format: type[T]) -> T | None:
        """نداء منظّم على مزود محدد."""
        result = await self._invoke_structured(provider, prompt, output_format)
        if result is None:
            self.circuit_breaker.record_failure(provider)
        else:
            self.circuit_breaker.record_success(provider)
        return result

    # ---------------- المتصفح (اختياري — يُحمَّل عند الطلب) ----------------
    def get_chat_model(self) -> Any:
        """
        نموذج محادثة لوحدة المتصفح (browser-use) — **ميزة اختيارية**.

        يُحمَّل `browser_use` عند الاستدعاء فقط؛ فإن لم يكن مثبّتاً تُرفع
        رسالة إرشادية بدل تعطيل بقية النظام. راجع `requirements-browser.txt`.
        """
        self.require_any_provider()
        try:
            from browser_use.llm import ChatGoogle, ChatGroq
        except ImportError as exc:
            raise RuntimeError(
                "ميزة المتصفح اختيارية وغير مثبّتة. نفّذ:\n"
                "  pip install -r requirements-browser.txt\n"
                "(ملاحظة: تتضمّن تبعيات ذات تنبيهات أمنية موثّقة في SECURITY.md)"
            ) from exc

        provider = GEMINI if self.gemini_available else GROQ
        if provider == GEMINI:
            return ChatGoogle(
                model=self.settings.agent_primary_model,
                api_key=self.settings.gemini_api_key,
                temperature=0.2,
            )
        return ChatGroq(
            model=self.settings.agent_fallback_model,
            api_key=self.settings.groq_api_key,
            temperature=0.2,
        )

    # ---------------- فحص الصحة (health) ----------------
    def health(self) -> dict[str, Any]:
        """فحص جاهزية بلا نداءات شبكية — يعكس توفر المفاتيح وحالة القواطع."""
        providers = {}
        if self.gemini_available:
            providers[GEMINI] = {"configured": True,
                                 "circuit_open": not self.circuit_breaker.allow(GEMINI)}
        if self.groq_available:
            providers[GROQ] = {"configured": True,
                               "circuit_open": not self.circuit_breaker.allow(GROQ)}
        return {
            "healthy": bool(providers) and any(
                not v["circuit_open"] for v in providers.values()
            ),
            "providers": providers,
            "circuit_breaker": self.circuit_breaker.snapshot(),
        }

    # ---------------- غلاف متزامن (sync) ----------------
    def complete(self, prompt: str) -> str:
        """نسخة متزامنة من execute_reasoning_step."""
        return asyncio.run(self.execute_reasoning_step(prompt))
