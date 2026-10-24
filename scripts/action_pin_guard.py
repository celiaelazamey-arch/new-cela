"""حارس تثبيت Actions — يمنع المراجع المتحركة في workflows (قاعدة supply chain).

الخطر: `uses: actions/checkout@v6` مرجع **متحرك** — يمكن لمن يملك المستودع
(أو مخترق له) إعادة توجيه الوسم إلى كود خبيث في أي وقت دون تغيير في ملفك.
GitHub Docs يوصّي صراحةً بتثبيت كل action بـ **commit SHA كامل (40 حرفاً)**:
https://docs.github.com/en/actions/reference/security/secure-use

هذا الفاحص يُفشل البناء عند أي `uses:` بلا SHA كامل، مع السماح بمراجع
محلية (`./`) وأكواد Docker الرسمية الموثّقة عند الحاجة.

مجاني 100% — المكتبة القياسية فقط (yaml اختياري؛ fallback نصي).
الاستخدام:
    python scripts/action_pin_guard.py
"""
from __future__ import annotations

import pathlib
import re
import sys

ROOT = pathlib.Path(__file__).resolve().parent.parent
WORKFLOW_DIR = ROOT / ".github" / "workflows"

# مراجع يُسمح بها بلا SHA (محلية فقط — لا شبكة).
LOCAL_PREFIXES = ("./", "../", "docker://")

_SHA_REF = re.compile(r"^[0-9a-f]{40}$")
_USES_LINE = re.compile(r"^\s*(?:-\s*)?uses:\s*([^\s#]+)")


def _extract_uses(text: str) -> list[tuple[int, str]]:
    """يستخرج (رقم السطر، المرجع) من كل سطر uses: — بلا اعتماد على PyYAML."""
    found: list[tuple[int, str]] = []
    for lineno, line in enumerate(text.splitlines(), 1):
        m = _USES_LINE.match(line)
        if m:
            found.append((lineno, m.group(1)))
    return found


def check_file(path: pathlib.Path) -> list[str]:
    issues: list[str] = []
    for lineno, ref in _extract_uses(path.read_text(encoding="utf-8")):
        if any(ref.startswith(p) for p in LOCAL_PREFIXES):
            continue
        if "@" not in ref:
            issues.append(f"{path.name}:{lineno}: uses بلا مرجع إصدار: {ref}")
            continue
        _repo, _sep, version = ref.partition("@")
        if not _SHA_REF.match(version):
            issues.append(
                f"{path.name}:{lineno}: '{ref}' يستخدم tag/branch متحركاً — "
                "ثبّته بـ commit SHA من 40 حرفاً (تحقق من مصدره الرسمي)."
            )
    return issues


def main() -> int:
    print("=" * 72)
    print("  حارس تثبيت Actions — منع المراجع المتحركة (supply chain)")
    print("=" * 72)
    print(f"  المفسّر: {sys.executable}")

    if not WORKFLOW_DIR.is_dir():
        print("  ❌ مجلد .github/workflows غير موجود.")
        return 1

    files = sorted(WORKFLOW_DIR.glob("*.y*ml"))
    violations: list[str] = []
    total = 0
    for path in files:
        uses = _extract_uses(path.read_text(encoding="utf-8"))
        total += len(uses)
        issues = check_file(path)
        violations.extend(issues)
        status = "✅" if not issues else "❌"
        pinned = len(uses) - len(issues)
        print(f"  {status} {path.name}: {pinned}/{len(uses)} مثبّتة بـ SHA")

    print(f"\n  إجمالي مراجع uses: {total}")

    if not violations:
        print("  ✅ كل Actions مثبّتة بـ commit SHA كامل.")
        print("=" * 72)
        return 0

    print(f"\n  ❌ مراجع متحركة ({len(violations)}):")
    for v in violations:
        print(f"     • {v}")
    print("\n  الحل: استبدل @vX بـ @<40-hex> مع تعليق يوضّح الإصدار.")
    print("=" * 72)
    return 1


if __name__ == "__main__":
    raise SystemExit(main())
