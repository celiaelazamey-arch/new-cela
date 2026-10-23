"""
واجهة موجه النماذج (Model Router Contract)
===========================================
عقد واحد يفصل **محرك الوكيل** عن **أي مزود نماذج**:

  • `ModelInfo`   — ما يعرفه النظام عن نموذج: المزود، القدرات، سياق، ودرجة الثقة.
  • `ModelRouter` — بروتوكول: `available()` + `complete()`.
  • `CapabilityRouter` — تنفيذ مرجعي: يختار النموذج حسب **القدرة المطلوبة**
    ثم الثقة ثم الترتيب المعلن، ويبدّل تلقائياً عند فشل مزود.

قاعدة التكلفة: الاختيار لا يمر أبداً إلى مزود غير مسموح — كل محاولة تمر عبر
`QuotaGuard` قبل النداء، فلا يوجد «تحويل صامت إلى خدمة مدفوعة».

إضافة مزود جديد = تسجيل `ModelInfo` + دالة نداء واحدة؛ لا تغيير في محرك الوكيل.
"""
from __future__ import annotations

import logging
from collections.abc import Awaitable, Callable, Sequence
from dataclasses import dataclass, field
from enum import Enum
from typing import Any, Protocol, TypeVar, runtime_checkable

from src.core.quota import ProviderNotAllowed, QuotaExceeded, QuotaGuard, TrustLevel

logger = logging.getLogger("new-cela.models")

T = TypeVar("T")


class Capability(str, Enum):
    """القدرات التي يطلبها المحرك — لا أسماء نماذج."""

    REASONING = "reasoning"
    CODING = "coding"
    SUMMARIZE = "summarize"
    STRUCTURED = "structured"  # إخراج منظّم (Function Calling)
    VISION = "vision"
    LONG_CONTEXT = "long_context"


class NoEligibleModel(RuntimeError):
    """لا يوجد نموذج مسموح وقادر على تلبية الطلب ضمن الميزانية."""


@dataclass(frozen=True)
class ModelInfo:
    """بطاقة تعريف نموذج — كل ما يحتاجه الموجّه للقرار."""

    provider: str
    model: str
    capabilities: frozenset[Capability] = field(
        default_factory=lambda: frozenset({Capability.REASONING})
    )
    trust: TrustLevel = TrustLevel.UNVERIFIED
    context_window: int | None = None
    priority: int = 100  # الأصغر أولاً عند تساوي القدرة والثقة

    def supports(self, capability: Capability | None) -> bool:
        return capability is None or capability in self.capabilities

    @property
    def label(self) -> str:
        return f"{self.provider}:{self.model}"


# دالة النداء التي يمررها المتصل: (بطاقة النموذج، النص، نموذج الإخراج) → نتيجة
BackendCall = Callable[[ModelInfo, str, type | None], Awaitable[Any]]


@runtime_checkable
class ModelRouter(Protocol):
    """العقد الذي يعتمد عليه محرك الوكيل — أي تنفيذ يفي به قابل للاستبدال."""

    def available(self) -> list[ModelInfo]:
        """النماذج المتاحة الآن (بعد تطبيق سياسة التكلفة)."""
        ...

    async def complete(
        self,
        prompt: str,
        capability: Capability | None = None,
        output_format: type | None = None,
    ) -> Any:
        """ينفّذ طلباً واحداً ويعيد نصاً أو كائناً منظّماً حسب `output_format`."""
        ...


class CapabilityRouter:
    """
    تنفيذ مرجعي لعقد `ModelRouter`.

    الترتيب: القدرة المطلوبة ← الثقة (متحقَّق قبل غيره) ← الأولوية المعلنة.
    عند فشل مزود: يُسجَّل السبب ويُجرَّب التالي، وإن فشل الجميع يُرفع خطأ شامل.
    """

    def __init__(
        self,
        models: Sequence[ModelInfo],
        call: BackendCall,
        quota: QuotaGuard | None = None,
    ) -> None:
        self.models = list(models)
        self.call = call
        self.quota = quota or QuotaGuard()

    # ---------------- العرض والاختيار ----------------
    def available(self) -> list[ModelInfo]:
        """النماذج التي تسمح بها سياسة التكلفة (بلا فحص استهلاك)."""
        allowed: list[ModelInfo] = []
        for info in self.models:
            try:
                self.quota.check_provider(info.provider, info.trust)
            except ProviderNotAllowed:
                continue
            allowed.append(info)
        return sorted(allowed, key=_ordering)

    def eligible(self, capability: Capability | None = None) -> list[ModelInfo]:
        """النماذج القادرة على هذه القدرة والمسموح بها."""
        return [info for info in self.available() if info.supports(capability)]

    # ---------------- التنفيذ ----------------
    async def complete(
        self,
        prompt: str,
        capability: Capability | None = None,
        output_format: type | None = None,
    ) -> Any:
        candidates = self.eligible(capability)
        if not candidates:
            raise NoEligibleModel(
                f"لا يوجد نموذج مسموح يلبي القدرة "
                f"«{capability.value if capability else 'أي قدرة'}» ضمن سياسة التكلفة."
            )

        failures: list[str] = []
        for info in candidates:
            try:
                self.quota.consume_model(info.provider, info.trust)
            except (ProviderNotAllowed, QuotaExceeded) as exc:
                failures.append(f"{info.label}:{type(exc).__name__}")
                if isinstance(exc, QuotaExceeded):
                    break  # لا فائدة من تجربة مزود آخر: الميزانية نفسها
                continue

            try:
                return await self.call(info, prompt, output_format)
            except Exception as exc:  # noqa: BLE001 — فشل مزود يفعّل البديل
                logger.warning("فشل النموذج %s (%s) — تجربة البديل.", info.label, type(exc).__name__)
                failures.append(f"{info.label}:{type(exc).__name__}")

        raise NoEligibleModel("فشلت كل النماذج المسموحة: " + "; ".join(failures))


def _ordering(info: ModelInfo) -> tuple[int, int, str]:
    """متحقَّق أولاً، ثم الأولوية، ثم الاسم (ترتيب حتمي)."""
    trust_rank = {
        TrustLevel.VERIFIED_FREE: 0,
        TrustLevel.UNVERIFIED: 1,
        TrustLevel.PAID: 2,
    }[info.trust]
    return (trust_rank, info.priority, info.label)


def router_from_llm(llm_router: Any, quota: QuotaGuard | None = None) -> CapabilityRouter:
    """
    يلفّ `LLMRouter` الحالي في عقد `ModelRouter` — بلا تغيير في بقية النظام.

    المزودان (Gemini/Groq) مُعلنان `VERIFIED_FREE` بناءً على مراجعة يدوية
    موثّقة في README؛ وهذا **تصريح** لا افتراض: أي مزود جديد يبدأ `UNVERIFIED`.
    """
    settings = getattr(llm_router, "settings", None)
    models = [
        ModelInfo(
            provider="gemini",
            model=getattr(settings, "agent_primary_model", "gemini-2.5-flash"),
            capabilities=frozenset(
                {
                    Capability.REASONING,
                    Capability.CODING,
                    Capability.SUMMARIZE,
                    Capability.STRUCTURED,
                    Capability.VISION,
                    Capability.LONG_CONTEXT,
                }
            ),
            trust=TrustLevel.VERIFIED_FREE,
            context_window=1_000_000,
            priority=10,
        ),
        ModelInfo(
            provider="groq",
            model=getattr(settings, "agent_fallback_model", "llama-3.3-70b-versatile"),
            capabilities=frozenset(
                {Capability.REASONING, Capability.CODING, Capability.SUMMARIZE, Capability.STRUCTURED}
            ),
            trust=TrustLevel.VERIFIED_FREE,
            context_window=131_072,
            priority=20,
        ),
    ]

    if not getattr(llm_router, "gemini_available", False):
        models = [info for info in models if info.provider != "gemini"]
    if not getattr(llm_router, "groq_available", False):
        models = [info for info in models if info.provider != "groq"]

    async def _call(info: ModelInfo, prompt: str, output_format: type | None) -> Any:
        return await _invoke(llm_router, info, prompt, output_format)

    return CapabilityRouter(models, _call, quota=quota)


async def _invoke(llm_router: Any, info: ModelInfo, prompt: str, output_format: type | None) -> Any:
    """ينفّذ النداء على المزود المطلوب تحديداً (لا تبديل داخلي مزدوج)."""
    if output_format is not None:
        if hasattr(llm_router, "execute_structured_step_for"):
            return await llm_router.execute_structured_step_for(
                info.provider, prompt, output_format
            )
        return await llm_router.execute_structured_step(prompt, output_format)

    if hasattr(llm_router, "execute_reasoning_step_for"):
        return await llm_router.execute_reasoning_step_for(info.provider, prompt)
    return await llm_router.execute_reasoning_step(prompt)
