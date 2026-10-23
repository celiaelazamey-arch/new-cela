"""
محرك السياسات والموافقات (Policy & Approval Engine)
====================================================
الطبقة التي تجعل التنفيذ **مصرَّحاً** لا مطلقاً:

  • النطاقات (Scopes): كل أداة تعلن ما تحتاجه (مثل `fs.read`)، ولا تعمل أداة
    بنطاق غير ممنوح.
  • المخاطر (Risk): `read` تعمل مباشرة، `write`/`execute` تتطلب موافقة صريحة.
  • حصر المسارات: كل مسار يُحلَّم (`resolve`) ويُفحص ضد جذور مسموحة —
    يمنع `../` والمسارات المطلقة والروابط الرمزية الهاربة.
  • سجل تدقيق (Audit): كل قرار يُدوَّن بعد تنقيح الأسرار.

المبدأ الأساسي: **الافتراضي هو الرفض** (`DenyAll`) — لا موافقة ضمنية أبداً.
"""
from __future__ import annotations

import json
import logging
import os
from collections.abc import Iterable
from dataclasses import dataclass
from datetime import UTC, datetime
from enum import Enum
from pathlib import Path
from typing import Protocol, runtime_checkable

from pydantic import BaseModel

from src.core.telemetry import redact

logger = logging.getLogger("new-cela.policy")


class ToolRisk(str, Enum):
    """مستوى خطر الأداة — يحدد هل تحتاج موافقة صريحة؟"""

    READ = "read"  # قراءة فقط: لا تغيّر شيئاً
    WRITE = "write"  # تعديل ملفات/بيانات المستخدم
    EXECUTE = "execute"  # تنفيذ أوامر أو تشغيل برامج


class PolicyDecision(str, Enum):
    """قرار السياسة قبل التنفيذ."""

    ALLOW = "allow"
    ASK = "ask"
    DENY = "deny"


class PolicyDenied(RuntimeError):
    """رُفض الطلب بقرار سياسة (نطاق مفقود، مسار خارج النطاق، أو رفض موافقة)."""


# النطاقات الممنوحة افتراضياً: قراءة الملفات فقط. أي شيء آخر يتطلب تصريحاً.
DEFAULT_SCOPES: frozenset[str] = frozenset({"fs.read"})

# نطاقات تحتاج موافقة المستخدم دائماً مهما كانت الإعدادات
ALWAYS_ASK_SCOPES: frozenset[str] = frozenset(
    {"fs.write", "fs.delete", "shell.exec", "net.egress"}
)


class ApprovalRequest(BaseModel):
    """طلب موافقة يُعرَض على المستخدم قبل عملية حساسة."""

    tool: str
    risk: ToolRisk
    scope: str = ""
    summary: str = ""
    args_preview: str = ""


class ApprovalDecision(BaseModel):
    """جواب المستخدم (أو السياسة الآلية) على طلب الموافقة."""

    approved: bool
    note: str = ""
    by: str = "user"


@runtime_checkable
class ApprovalHandler(Protocol):
    """أي كائن قابل للاستدعاء يعيد قراراً — يسمح بتبديل واجهة الموافقة لاحقاً."""

    async def __call__(self, request: ApprovalRequest) -> ApprovalDecision: ...


class DenyAll:
    """المعالج الافتراضي: يرفض كل ما يحتاج موافقة (تشغيل غير تفاعلي آمن)."""

    async def __call__(self, request: ApprovalRequest) -> ApprovalDecision:
        return ApprovalDecision(
            approved=False,
            note=f"لا يوجد مستخدم للموافقة على «{request.tool}» — الرفض هو الافتراضي.",
            by="policy",
        )


class AutoApprove:
    """
    موافقة تلقائية صريحة — تُستخدم فقط عند طلب المستخدم (`--yes`) أو في الاختبارات.

    ليست «سلوكاً صامتاً»: كل قرار يُدوَّن في سجل التدقيق باسم `auto`.
    """

    def __init__(self, note: str = "موافقة تلقائية صريحة (تشغيل غير تفاعلي)") -> None:
        self.note = note

    async def __call__(self, request: ApprovalRequest) -> ApprovalDecision:
        return ApprovalDecision(approved=True, note=self.note, by="auto")


@dataclass(frozen=True)
class PolicyVerdict:
    """نتيجة تقييم السياسة مع سبب واضح قابل للعرض والتدقيق."""

    decision: PolicyDecision
    reason: str
    scope: str = ""

    @property
    def allowed(self) -> bool:
        return self.decision is PolicyDecision.ALLOW


class AuditEntry(BaseModel):
    """سطر واحد في سجل التدقيق — كافٍ لفهم «من فعل ماذا ومتى ولماذا»."""

    at: str
    tool: str
    risk: ToolRisk
    scope: str = ""
    decision: PolicyDecision
    reason: str = ""
    args_preview: str = ""
    ok: bool | None = None


class AuditLog:
    """
    سجل تدقيق في الذاكرة + ملف JSONL اختياري.

    لا يرفع استثناء أبداً: تعذّر الكتابة على القرص لا يمنع التنفيذ،
    لكنه يُبقي السجل في الذاكرة للعرض.
    """

    def __init__(self, path: str | os.PathLike[str] | None = None, max_entries: int = 500) -> None:
        self.path = Path(path) if path else None
        self.max_entries = max(1, int(max_entries))
        self._entries: list[AuditEntry] = []

    def record(self, entry: AuditEntry) -> AuditEntry:
        self._entries.append(entry)
        if len(self._entries) > self.max_entries:
            self._entries = self._entries[-self.max_entries :]
        self._append(entry)
        return entry

    def entries(self) -> list[AuditEntry]:
        return list(self._entries)

    def _append(self, entry: AuditEntry) -> None:
        if self.path is None:
            return
        try:
            self.path.parent.mkdir(parents=True, exist_ok=True)
            with open(self.path, "a", encoding="utf-8") as handle:
                handle.write(json.dumps(entry.model_dump(mode="json"), ensure_ascii=False) + "\n")
        except OSError as exc:
            logger.warning("تعذّر كتابة سجل التدقيق (%s).", type(exc).__name__)


class PolicyEngine:
    """
    يقرر: هل تُنفَّذ هذه الأداة بهذه الوسائط الآن؟

    الاستخدام:
        policy = PolicyEngine(allowed_roots=[Path.cwd()])
        await policy.authorize(tool="read_file", risk=ToolRisk.READ, scope="fs.read", args={...})
    """

    def __init__(
        self,
        allowed_roots: Iterable[str | Path] | None = None,
        granted_scopes: Iterable[str] = DEFAULT_SCOPES,
        approval_handler: ApprovalHandler | None = None,
        audit: AuditLog | None = None,
        max_approvals: int = 10,
    ) -> None:
        roots = [Path(root) for root in (allowed_roots or [Path.cwd()])]
        self.allowed_roots: tuple[Path, ...] = tuple(root.resolve() for root in roots)
        self.granted_scopes = frozenset(granted_scopes)
        self.approval_handler: ApprovalHandler = approval_handler or DenyAll()
        self.audit = audit or AuditLog()
        self.max_approvals = max(0, int(max_approvals))
        self._approvals_used = 0

    # ---------------- حصر المسارات ----------------
    def resolve_path(self, raw: str | Path) -> Path:
        """
        يحلّم المسار ويتحقق أنه داخل جذور مسموحة.

        يرفع `PolicyDenied` عند أي محاولة خروج (نسبي أو مطلق أو رمزي).
        """
        candidate = Path(str(raw)).expanduser()
        if not candidate.is_absolute():
            candidate = self.allowed_roots[0] / candidate
        try:
            resolved = candidate.resolve()
        except (OSError, RuntimeError) as exc:  # حلقة روابط رمزية أو خطأ نظام
            raise PolicyDenied(f"تعذّر حلّ المسار «{raw}».") from exc

        if self._inside_allowed(resolved):
            return resolved
        raise PolicyDenied(
            f"المسار «{raw}» خارج النطاق المسموح "
            f"({', '.join(str(root) for root in self.allowed_roots)})."
        )

    def _inside_allowed(self, path: Path) -> bool:
        return any(path == root or root in path.parents for root in self.allowed_roots)

    # ---------------- التقييم ----------------
    def evaluate(self, tool: str, risk: ToolRisk, scope: str = "") -> PolicyVerdict:
        """يقيّم الطلب دون أي تفاعل — قرار قابل للاختبار وحدوياً."""
        if scope and scope not in self.granted_scopes:
            return PolicyVerdict(
                PolicyDecision.DENY,
                f"النطاق «{scope}» غير ممنوح لهذه الجلسة.",
                scope,
            )

        if risk is ToolRisk.READ and scope not in ALWAYS_ASK_SCOPES:
            return PolicyVerdict(PolicyDecision.ALLOW, "قراءة فقط ضمن نطاق ممنوح.", scope)

        if self._approvals_used >= self.max_approvals:
            return PolicyVerdict(
                PolicyDecision.DENY,
                f"تجاوز حد الموافقات لكل مهمة ({self.max_approvals}).",
                scope,
            )

        return PolicyVerdict(
            PolicyDecision.ASK,
            f"عملية {risk.value} تتطلب موافقة صريحة.",
            scope,
        )

    async def authorize(
        self,
        tool: str,
        risk: ToolRisk,
        scope: str = "",
        args: dict | None = None,
        summary: str = "",
    ) -> PolicyVerdict:
        """
        يقيّم ثم يحصل على الموافقة عند الحاجة، ويسجّل القرار في التدقيق.

        يرفع `PolicyDenied` عند الرفض — ولا يعيد إلا قرار `ALLOW`.
        """
        verdict = self.evaluate(tool, risk, scope)
        preview = redact(_preview_args(args))

        if verdict.decision is PolicyDecision.DENY:
            self._audit(tool, risk, scope, PolicyDecision.DENY, verdict.reason, preview)
            raise PolicyDenied(f"رُفض تنفيذ «{tool}»: {verdict.reason}")

        if verdict.decision is PolicyDecision.ASK:
            request = ApprovalRequest(
                tool=tool,
                risk=risk,
                scope=scope,
                summary=summary or f"هل تسمح بتنفيذ «{tool}»؟",
                args_preview=preview,
            )
            decision = await self.approval_handler(request)
            self._approvals_used += 1
            if not decision.approved:
                reason = decision.note or "رفض المستخدم."
                self._audit(tool, risk, scope, PolicyDecision.DENY, reason, preview)
                raise PolicyDenied(f"لم تُمنح الموافقة لتنفيذ «{tool}»: {reason}")
            self._audit(tool, risk, scope, PolicyDecision.ALLOW, decision.note or "موافقة", preview)
            return PolicyVerdict(PolicyDecision.ALLOW, decision.note or "موافقة", scope)

        self._audit(tool, risk, scope, PolicyDecision.ALLOW, verdict.reason, preview)
        return verdict

    def record_outcome(self, tool: str, risk: ToolRisk, scope: str, ok: bool, note: str = "") -> None:
        """يسجّل نتيجة التنفيذ الفعلية بجانب قرار السياسة."""
        self._audit(tool, risk, scope, PolicyDecision.ALLOW, note or ("نجح" if ok else "فشل"), "", ok)

    # ---------------- داخلي ----------------
    def _audit(
        self,
        tool: str,
        risk: ToolRisk,
        scope: str,
        decision: PolicyDecision,
        reason: str,
        preview: str,
        ok: bool | None = None,
    ) -> None:
        self.audit.record(
            AuditEntry(
                at=datetime.now(UTC).isoformat(timespec="milliseconds"),
                tool=tool,
                risk=risk,
                scope=scope,
                decision=decision,
                reason=redact(reason),
                args_preview=preview,
                ok=ok,
            )
        )


def _preview_args(args: dict | None, limit: int = 200) -> str:
    """معاينة وسائط للتدقيق — منقّحة ومقتطعة."""
    if not args:
        return ""
    try:
        text = json.dumps(args, ensure_ascii=False, default=str)
    except (TypeError, ValueError):
        text = str(args)
    text = redact(text)
    return text if len(text) <= limit else text[: limit - 1] + "…"
