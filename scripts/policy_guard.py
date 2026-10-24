"""حارس السياسات — يمنع تعطيل الحماية لإصلاح خطأ تشغيلي (Failure Pattern 3).

الخطر: تميل الوكلاء إلى «حل» خطأ بتعطيله: حذف validation، تخفيف سياسة،
تعطيل مصادقة، أو منح صلاحيات واسعة (Failure Pattern 9). هذا الفاحص يثبّت
**ثوابت الأمان** كخطوط حمراء: أي تغيير عليها يُفشل البناء ويطلب مراجعة بشرية.

يتحقق من (بقراءة AST + نصوص، بلا تنفيذ للكود):
  1. DEFAULT_SCOPES في policy.py لا يتجاوز {fs.read}.
  2. لا `shell=True` ولا `create_subprocess_shell` في مسار التنفيذ.
  3. allow_paid لا يُفعَّل افتراضياً في quota.py.
  4. agent-runner لا يمرّر --yes ولا يعطّل التصنيف/التنقيح.
  5. قوائم السماح في secret_scan/pii_scan لا تُفرَّغ أو تُعطَّل.
  6. ملفات السياسة نفسها موجودة وقابلة للقراءة.

مجاني 100% — المكتبة القياسية فقط. الاستخدام:
    python scripts/policy_guard.py
"""
from __future__ import annotations

import ast
import pathlib
import re
import sys

ROOT = pathlib.Path(__file__).resolve().parent.parent

PROTECTED = {
    "src/core/policy.py": "محرك السياسة (رفض افتراضي)",
    "src/core/quota.py": "حارس التكلفة (منع المدفوع)",
    "src/execution/sandbox.py": "بيئة التنفيذ المعزولة",
    "scripts/secret_scan.py": "فاحص الأسرار",
    "scripts/pii_scan.py": "فاحص البيانات الشخصية",
    "scripts/error_guard.py": "حارس معالجة الأخطاء",
    "src/core/path_guard.py": "حاجز المسارات (CodeQL #1-#4)",
}


def _read(rel: str) -> str | None:
    path = ROOT / rel
    try:
        return path.read_text(encoding="utf-8")
    except OSError:
        return None


def check_default_scopes() -> list[str]:
    """DEFAULT_SCOPES يجب أن يبقى ضمن {fs.read} وألا يتوسّع صامتاً."""
    text = _read("src/core/policy.py")
    if text is None:
        return ["policy.py غير موجود — لا يمكن التحقق من النطاقات"]
    try:
        tree = ast.parse(text)
    except SyntaxError as exc:
        return [f"policy.py لا يُحلَّل: {exc}"]

    found = None
    for node in ast.walk(tree):
        target = None
        if isinstance(node, ast.AnnAssign) and isinstance(node.target, ast.Name):
            target = node.target.id
            value = node.value
        elif isinstance(node, ast.Assign) and len(node.targets) == 1 and isinstance(node.targets[0], ast.Name):
            target = node.targets[0].id
            value = node.value
        else:
            continue
        if target == "DEFAULT_SCOPES" and value is not None:
            # frozenset({"fs.read"}) أو {"fs.read"}
            if isinstance(value, ast.Call):
                args = value.args
            else:
                args = [value]
            for arg in args:
                if isinstance(arg, (ast.Set, ast.List, ast.Tuple)):
                    found = {
                        e.value for e in arg.elts
                        if isinstance(e, ast.Constant) and isinstance(e.value, str)
                    }
            break

    issues: list[str] = []
    if found is None:
        issues.append("لم يُعثر على DEFAULT_SCOPES صريح — راجع يدوياً.")
    elif found != {"fs.read"}:
        issues.append(
            f"DEFAULT_SCOPES تغيّر إلى {sorted(found)} (المتوقع ['fs.read']) "
            "— منح صلاحيات أوسع يحتاج مراجعة بشرية صريحة."
        )
    return issues


def check_no_shell() -> list[str]:
    """بيئة التنفيذ يجب أن تبقى بلا shell."""
    issues: list[str] = []
    for rel in ("src/execution/sandbox.py", "src/mcp/client.py"):
        text = _read(rel)
        if text is None:
            continue
        for pattern, label in (
            (r"\bshell\s*=\s*True\b", "shell=True"),
            (r"create_subprocess_shell", "create_subprocess_shell"),
        ):
            if re.search(pattern, text):
                issues.append(f"{rel}: استخدام {label} ممنوع (حقن أوامر).")
    return issues


def check_no_paid_default() -> list[str]:
    """allow_paid يجب أن يبقى False افتراضياً."""
    text = _read("src/core/quota.py")
    if text is None:
        return ["quota.py غير موجود"]
    # نبحث عن allow_paid: bool = True في توقيع الدالة/الإعدادات
    if re.search(r"allow_paid\s*:\s*bool\s*=\s*True", text):
        return ["quota.py: allow_paid افتراضياً True — يخالف «الموارد المجانية فقط»."]
    # وأي تفعيل صريح في موضع افتراضي
    if re.search(r"allow_paid\s*=\s*True", text) and "False" not in text:
        return ["quota.py: allow_paid مُفعَّل بلا بديل False — راجع يدوياً."]
    return []


def check_runner_hardening() -> list[str]:
    """agent-runner لا يمرّر --yes ولا يعطّل التنقيح/التقييد."""
    text = _read(".github/workflows/agent-runner.yml")
    if text is None:
        return ["agent-runner.yml غير موجود"]
    issues: list[str] = []
    # نستبعد أسطر التعليقات أولاً: ذكر "--yes" داخل شرح لا يُعدّ تمريراً.
    # (كان فحص نصي بسيط يخطئ لأنه لا يميّز التعليق عن الأمر التنفيذي.)
    code_lines = [
        line for line in text.splitlines()
        if not line.lstrip().startswith("#")
    ]
    code_only = "\n".join(code_lines)
    if re.search(r"--yes\b", code_only):
        issues.append("agent-runner.yml: يمرّر --yes (موافقة تلقائية على مُدخل غير موثوق).")
    if "sanitize_output.py" not in code_only:
        issues.append("agent-runner.yml: تنقيح المخرجات (sanitize_output.py) معطَّل.")
    if "MAX_TASK_CHARS" not in text:
        issues.append("agent-runner.yml: حدّ طول المهمة (MAX_TASK_CHARS) معطَّل.")
    if "author_association" not in text:
        issues.append("agent-runner.yml: تقييد كاتب الـ Issue (author_association) معطَّل.")
    return issues


def check_allowlists_intact() -> list[str]:
    """قوائم السماح يجب أن تبقى صريحة (لا تُفرَّغ ولا تُعطَّل)."""
    issues: list[str] = []
    for rel, name in (("scripts/secret_scan.py", "ALLOWLIST"),
                      ("scripts/pii_scan.py", "ALLOWLIST")):
        text = _read(rel)
        if text is None:
            issues.append(f"{rel} غير موجود")
            continue
        if "ALLOWLIST" not in text:
            issues.append(f"{rel}: قائمة السماح محذوفة — الفاحص قد يصبح بلا استثناءات صريحة.")
    return issues


def check_protected_present() -> list[str]:
    issues: list[str] = []
    for rel, label in PROTECTED.items():
        if _read(rel) is None:
            issues.append(f"ملف محمي مفقود: {rel} ({label})")
    return issues


def main() -> int:
    print("=" * 72)
    print("  حارس السياسات — منع تعطيل الحماية (Failure Patterns 3 + 9)")
    print("=" * 72)
    print(f"  المفسّر: {sys.executable}")

    checks = (
        ("النطاقات الافتراضية", check_default_scopes),
        ("بلا shell", check_no_shell),
        ("بلا مزود مدفوع افتراضي", check_no_paid_default),
        ("تأمين agent-runner", check_runner_hardening),
        ("قوائم السماح", check_allowlists_intact),
        ("الملفات المحمية", check_protected_present),
    )

    violations: list[str] = []
    for label, fn in checks:
        found = fn()
        if found:
            violations.extend(found)
            print(f"  ❌ {label}:")
            for v in found:
                print(f"       • {v}")
        else:
            print(f"  ✅ {label}")

    if not violations:
        print("\n  ✅ كل الخطوط الحمراء سليمة. لم تُعطَّل أي حماية.")
        print("=" * 72)
        return 0

    print(f"\n  🚫 {len(violations)} مخالفة. أي تعديل على الحماية يحتاج مراجعة بشرية.")
    print("=" * 72)
    return 1


if __name__ == "__main__":
    raise SystemExit(main())
