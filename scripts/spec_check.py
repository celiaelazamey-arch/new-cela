"""فاحص انحراف المنطق عن المواصفة (Failure Pattern 4: Logic Drift).

الخطر: يعامل الوكيل مخرجاته السابقة كأنها المصدر الأصلي، فتتراكم الأخطاء
دون ملاحظة. العلاج: **مقارنة دورية مع متطلبات أصلية ثابتة** (spec).

هذا الفاحص يثبّت «عقوداً معمارية» لا يجوز أن تنحرف: الدوال/الثوابت/الملفات
التي يعتمد عليها باقي النظام. لو تغيّر اسم أو اختفى عقد ⇒ يفشل البناء ويطلب
مراجعة بشرية بدل أن يكتشف المستخدم متأخراً.

كما يتحقق من ملف القواعد (CLAUDE.md) ومراجع السياسات — لأن غيابها هو
Failure Pattern 12 (غياب meta-instructions).

مجاني 100% — المكتبة القياسية فقط (ast). الاستخدام:
    python scripts/spec_check.py
"""
from __future__ import annotations

import ast
import pathlib
import sys

ROOT = pathlib.Path(__file__).resolve().parent.parent

# العقود المعمارية: {ملف: {اسم: نوع}} — يجب أن تبقى موجودة.
# أي تغيير هنا نفسه يحتاج مراجعة بشرية (هذا هو مغزى الملف).
CONTRACTS: dict[str, dict[str, str]] = {
    "src/providers/llm_router.py": {
        "LLMRouter": "class",
        "CircuitBreaker": "class",
        "execute_reasoning_step": "func",
        "execute_structured_step": "func",
        "get_chat_model": "func",
        "health": "func",
        "GEMINI": "const",
        "GROQ": "const",
    },
    "src/core/model_api.py": {
        "ModelInfo": "class",
        "CapabilityRouter": "class",
        "router_from_llm": "func",
    },
    "src/core/policy.py": {
        "PolicyEngine": "class",
        "PolicyDenied": "class",
        "DEFAULT_SCOPES": "const",
    },
    "src/core/quota.py": {
        "QuotaGuard": "class",
        "TrustLevel": "class",
    },
    "src/execution/sandbox.py": {
        "SandboxExecutor": "class",
    },
    "scripts/dependency_guard.py": {"main": "func"},
    "scripts/secret_scan.py": {"main": "func"},
    "scripts/pii_scan.py": {"main": "func"},
    "scripts/error_guard.py": {"main": "func"},
    "scripts/policy_guard.py": {"main": "func"},
}

# ملفات مرجعية يجب أن تظل موجودة (meta-instructions + أدلة).
REQUIRED_DOCS = (
    "CLAUDE.md",
    "AGENT_HANDOFF.md",
    "SECURITY.md",
    "docs/security-threat-model.md",
    "docs/ai-code-reliability.md",
    "docs/agent-failure-patterns.md",
    "docs/compliance-framework.md",
)

# بنود المواصفة الأصلية (من الطلبات الأولى) — تُذكَّر في التقرير لمنع النسيان.
ORIGINAL_REQUIREMENTS = (
    "لا فقدان لأي شيء بين الجلسات (AGENT_HANDOFF + git push)",
    "الاعتماد على الموارد المجانية فقط في البداية",
    "وكيل يتعامل مع المستودع ويشرح التنبيهات رغم 403",
    "قاعدة: لا دخول في مهمة قبل إنهاء وتأكيد سابقتها",
    "كل طلب يُسجَّل مع إثبات تنفيذ",
    "Generate ← Run ← Test ← Review ← Commit",
)


def _top_level_names(path: pathlib.Path) -> dict[str, str] | None:
    try:
        tree = ast.parse(path.read_text(encoding="utf-8"))
    except (SyntaxError, UnicodeDecodeError, OSError):
        return None
    names: dict[str, str] = {}
    for node in tree.body:  # التعريفات على المستوى الأعلى
        if isinstance(node, ast.ClassDef):
            names[node.name] = "class"
            # ✅ methods داخل الأصناف عقود أيضاً (execute_*/health/... ).
            # تجاهلها كان سيجعل الفاحص يدّعي اختفاء دوال موجودة فعلاً.
            for child in node.body:
                if isinstance(child, (ast.FunctionDef, ast.AsyncFunctionDef)):
                    names.setdefault(child.name, "func")
        elif isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef)):
            names[node.name] = "func"
        elif isinstance(node, ast.Assign):
            for target in node.targets:
                if isinstance(target, ast.Name) and target.id.isupper():
                    names[target.id] = "const"
        elif isinstance(node, ast.AnnAssign) and isinstance(node.target, ast.Name):
            if node.target.id.isupper():
                names[node.target.id] = "const"
    return names


def main() -> int:
    print("=" * 74)
    print("  فاحص انحراف المنطق — مقارنة الكود بالمواصفة (Failure Pattern 4)")
    print("=" * 74)
    print(f"  المفسّر: {sys.executable}")

    violations: list[str] = []

    # 1) العقود المعمارية
    for rel, expected in CONTRACTS.items():
        path = ROOT / rel
        names = _top_level_names(path)
        if names is None:
            violations.append(f"{rel}: غير موجود أو لا يُحلَّل — العقد مفقود.")
            continue
        for name, kind in expected.items():
            if name not in names:
                violations.append(f"{rel}: العقد «{name}» ({kind}) اختفى — انحراف عن المواصفة.")
            elif kind in ("class", "func") and names[name] != kind:
                violations.append(f"{rel}: «{name}» تغيّر نوعه إلى {names[name]} (كان {kind}).")

    # 2) المراجع
    for rel in REQUIRED_DOCS:
        if not (ROOT / rel).is_file():
            violations.append(f"مرجع مفقود: {rel} — غياب meta-instructions (البند 12).")

    print(f"  عقود مُتحقَّقة: {sum(len(v) for v in CONTRACTS.values())}")
    print(f"  مراجع مطلوبة: {len(REQUIRED_DOCS)}")
    print("\n  📜 المتطلبات الأصلية (مرجع ثابت — لا تُستبدل بمخرجات سابقة):")
    for i, req in enumerate(ORIGINAL_REQUIREMENTS, 1):
        print(f"     {i}. {req}")

    if not violations:
        print("\n  ✅ الكود مطابق للمواصفة. لم يُرصد انحراف منطقي.")
        print("=" * 74)
        return 0

    print(f"\n  ❌ انحرافات ({len(violations)}):")
    for v in violations:
        print(f"     • {v}")
    print("\n  لا تُصلح الانحراف بتغيير المواصفة — راجع المصدر الأصلي أولاً.")
    print("=" * 74)
    return 1


if __name__ == "__main__":
    raise SystemExit(main())
