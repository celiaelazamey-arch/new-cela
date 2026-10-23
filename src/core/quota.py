"""
حارس التكلفة والحصص (QuotaGuard)
================================
الغرض: **لا تنفيذ يتجاوز الميزانية، ولا انتقال صامت إلى خدمة مدفوعة.**

ثلاثة مستويات ثقة لكل مزود/أداة:

| المستوى | المعنى | السلوك الافتراضي |
|---|---|---|
| `verified_free` | حصة مجانية **متحقَّق منها** (المنطقة، بلا بطاقة، حدود معلومة، لا فوترة تلقائية) | مسموح |
| `unverified` | يُدَّعى أنها مجانية لكن لم يُتحقق | **ممنوع** حتى يُصرَّح صراحةً |
| `paid` | مدفوع أو قد يفوتر تلقائياً | **ممنوع** دائماً في وضع «تكلفة صفرية» |

كما يحدّ عدد نداءات النموذج والأدوات لكل مهمة، ويرفع `QuotaExceeded`
برسالة توضح ما استُهلك وما تبقّى.
"""
from __future__ import annotations

import logging
from collections import Counter
from dataclasses import dataclass, field
from enum import Enum

logger = logging.getLogger("new-cela.quota")


class TrustLevel(str, Enum):
    """درجة الثقة في أن الاستخدام يبقى بلا تكلفة."""

    VERIFIED_FREE = "verified_free"
    UNVERIFIED = "unverified"
    PAID = "paid"


class QuotaExceeded(RuntimeError):
    """تجاوز الميزانية المحددة للمهمة."""


class ProviderNotAllowed(RuntimeError):
    """مزود غير مسموح به في هذا الوضع (غير متحقَّق أو مدفوع)."""


@dataclass
class Usage:
    """استهلاك المهمة الحالية — بلا أي بيانات حساسة."""

    model_calls: int = 0
    tool_calls: int = 0
    by_provider: Counter = field(default_factory=Counter)
    by_tool: Counter = field(default_factory=Counter)

    def snapshot(self) -> dict[str, object]:
        return {
            "model_calls": self.model_calls,
            "tool_calls": self.tool_calls,
            "by_provider": dict(self.by_provider),
            "by_tool": dict(self.by_tool),
        }


class QuotaGuard:
    """
    بوابة استهلاك واحدة للمهمة.

    الاستخدام:
        guard = QuotaGuard(max_model_calls=30, max_tool_calls=10)
        guard.check_provider("gemini", TrustLevel.VERIFIED_FREE)
        guard.consume_model("gemini")
    """

    def __init__(
        self,
        max_model_calls: int = 40,
        max_tool_calls: int = 20,
        allow_unverified: bool = False,
        allow_paid: bool = False,
    ) -> None:
        self.max_model_calls = max(0, int(max_model_calls))
        self.max_tool_calls = max(0, int(max_tool_calls))
        # سماح صريح فقط — وليس افتراضياً
        self.allow_unverified = bool(allow_unverified)
        self.allow_paid = bool(allow_paid)
        self.usage = Usage()

    # ---------------- فحص المزودين ----------------
    def check_provider(
        self, provider: str, trust: TrustLevel | str = TrustLevel.VERIFIED_FREE
    ) -> None:
        """
        يرفض المزود غير المتحقق/المدفوع قبل أي نداء.

        هذا هو «بوابة التكلفة» المطلوبة: لا نفترض أن وجود مفتاح API يعني المجانية.
        """
        level = TrustLevel(trust)
        if level is TrustLevel.PAID and not self.allow_paid:
            raise ProviderNotAllowed(
                f"المزود «{provider}» مصنَّف مدفوعاً — ممنوع في وضع التكلفة الصفرية."
            )
        if level is TrustLevel.UNVERIFIED and not self.allow_unverified:
            raise ProviderNotAllowed(
                f"المزود «{provider}» غير متحقَّق من مجانيته. "
                "تحقّق من المنطقة والبطاقة وحدود الحصة أولاً، أو مرّر allow_unverified صراحةً."
            )

    # ---------------- الاستهلاك ----------------
    def remaining(self) -> dict[str, int]:
        """المتبقي من الميزانية — تُعرض للمستخدم قبل التشغيل."""
        return {
            "model_calls": max(0, self.max_model_calls - self.usage.model_calls),
            "tool_calls": max(0, self.max_tool_calls - self.usage.tool_calls),
        }

    def can_call_model(self) -> bool:
        return self.usage.model_calls < self.max_model_calls

    def can_call_tool(self) -> bool:
        return self.usage.tool_calls < self.max_tool_calls

    def consume_model(self, provider: str, trust: TrustLevel | str = TrustLevel.VERIFIED_FREE) -> None:
        """يسجّل نداء نموذج — بعد فحص المزود والميزانية."""
        self.check_provider(provider, trust)
        if not self.can_call_model():
            raise QuotaExceeded(
                f"تجاوز حد نداءات النموذج ({self.max_model_calls}) — "
                "أوقف المهمة بدل تجاوز الميزانية."
            )
        self.usage.model_calls += 1
        self.usage.by_provider[provider] += 1

    def consume_tool(self, name: str) -> None:
        """يسجّل نداء أداة — بعد فحص الميزانية."""
        if not self.can_call_tool():
            raise QuotaExceeded(
                f"تجاوز حد نداءات الأدوات ({self.max_tool_calls}) — المهمة تحتاج تصريحاً بميزانية أكبر."
            )
        self.usage.tool_calls += 1
        self.usage.by_tool[name] += 1

    def snapshot(self) -> dict[str, object]:
        """لقطة كاملة للعرض في لوحة المتابعة وسجل التشغيل."""
        return {
            "limits": {
                "model_calls": self.max_model_calls,
                "tool_calls": self.max_tool_calls,
            },
            "remaining": self.remaining(),
            "usage": self.usage.snapshot(),
            "allow_unverified": self.allow_unverified,
            "allow_paid": self.allow_paid,
        }
