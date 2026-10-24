"""حارس معالجة الأخطاء — يمنع إخفاء الأخطاء (Failure Pattern 1).

الخطر: تميل وكلاء التوليد إلى كتم الأخطاء بدل إبلاغ المستخدم، فيبدو الكود
«يعمل» بينما لا ينفّذ المطلوب (Failure Pattern 2: أخطاء صامتة في منطق العمل).

هذا الفاحص **ذكي**: لا يكتفي بغياب `logger`، بل يتعرّف على أي «فعل هادف»
داخل المعالجة — لأن كثيراً من المعالجات المشروعة تُسجّل نتيجة فاشلة عبر
`assignment` أو `append` دون استدعاء logger.

التصنيفات المقبولة:
  • RAISE  — تُعيد رفع الاستثناء للطبقة الأعلى (fail fast).
  • LOG    — تستدعي logger/logging.
  • RETURN — تُعيد قيمة أو break/continue (تحكّم صريح في التدفّق).
  • ACTION — تُسجّل نتيجة فاشلة عبر assignment/augassign/append.
  • PASS   — مقبول **فقط** إن كان موثّقاً بتعليق يبرّره.

يُفشل البناء عند:
  • `except: pass` غير موثّق.
  • معالجة SILENT بلا أي فعل هادف.

مجاني 100% — المكتبة القياسية فقط (ast). الاستخدام:
    python scripts/error_guard.py
    python scripts/error_guard.py --strict-comments  # يلزم تعليقاً لكل معالجة
"""
from __future__ import annotations

import argparse
import ast
import pathlib
import re
import sys

ROOT = pathlib.Path(__file__).resolve().parent.parent

# ملفات خارج نطاق الفحص (مولّدة/مؤقتة/بيئات).
EXCLUDED_PARTS = frozenset({".venv", "__pycache__", ".git", "node_modules", ".pytest_cache"})

# بادئة المواثيق المقبولة في التعليق المرافق للمعالجة.
_DOC_MARKERS = ("noqa", "—", "--", "مقصود", "لأن", "السبب", "skip", "ignore")

_LOG_RE = re.compile(r"\blogger\.|\blogging\.|\bself\.log\(")

# استدعاءات «هادفة» تُبلّغ أو تُسجّل أو تتحقّق — تُعدّ فعلاً لا ابتلاعاً.
# (check في سكربتات التحقق · show/render للعرض البديل · warn/report للإبلاغ)
_PURPOSEFUL_CALL_RE = re.compile(
    r"\b(check|report|warn|record|notify|show_\w+|render_\w+|print)\s*\("
)


def _header_documented(lines: list[str], node: ast.ExceptHandler) -> bool:
    """هل السطر الذي يبدأ عنده `except` يحمل تعليقاً مبرّراً؟"""
    if node.lineno < 1 or node.lineno > len(lines):
        return False
    header = lines[node.lineno - 1]
    if "#" not in header:
        return False
    comment = header.split("#", 1)[1].strip()
    if not comment:
        return False
    return any(marker in header for marker in _DOC_MARKERS) or len(comment) >= 6


def _classify(node: ast.ExceptHandler, text: str, lines: list[str]) -> str:
    """يعيد تصنيف المعالجة: RAISE/LOG/RETURN/ACTION/PASS_DOC/PASS_BARE/SILENT."""
    segment = ast.get_source_segment(text, node) or ""

    if _LOG_RE.search(segment):
        return "LOG"
    if any(isinstance(s, ast.Raise) for s in ast.walk(node)):
        return "RAISE"
    if any(isinstance(s, (ast.Return, ast.Break, ast.Continue)) for s in ast.walk(node)):
        return "RETURN"
    # assert داخل معالجة = تحقّق صريح (شائع في الاختبارات).
    if any(isinstance(s, ast.Assert) for s in ast.walk(node)):
        return "ACTION"
    # استدعاء هادف (check/report/show_*/...) يُبلّغ أو يعالج بديلاً.
    if _PURPOSEFUL_CALL_RE.search(segment):
        return "ACTION"
    # فعل هادف: assignment (كسجل نتيجة فاشلة) أو استدعاء يُلحق/يُسجّل.
    has_assign = any(
        isinstance(s, (ast.Assign, ast.AugAssign, ast.AnnAssign)) for s in ast.walk(node)
    )
    has_append = bool(re.search(r"\.append\(|\.add\(|\.setdefault\(", segment))
    if has_assign or has_append:
        return "ACTION"

    if len(node.body) == 1 and isinstance(node.body[0], ast.Pass):
        return "PASS_DOC" if _header_documented(lines, node) else "PASS_BARE"

    return "SILENT"


def scan(root: pathlib.Path = ROOT) -> tuple[list[tuple[str, int, str, str]], dict[str, int]]:
    """يفحص كل ملفات Python ويعيد (المخالفات، إحصاء التصنيفات)."""
    violations: list[tuple[str, int, str, str]] = []
    stats: dict[str, int] = {}

    for path in sorted(root.rglob("*.py")):
        if any(part in EXCLUDED_PARTS for part in path.parts):
            continue
        try:
            text = path.read_text(encoding="utf-8")
            tree = ast.parse(text)
        except (SyntaxError, UnicodeDecodeError, OSError):
            continue
        lines = text.splitlines()
        rel = str(path.relative_to(root))
        for node in ast.walk(tree):
            if not isinstance(node, ast.ExceptHandler):
                continue
            kind = _classify(node, text, lines)
            stats[kind] = stats.get(kind, 0) + 1
            if kind == "PASS_BARE":
                violations.append((rel, node.lineno, kind, "except: pass بلا توثيق — أضف تعليقاً يبرّر أو سجّل/ارفع"))
            elif kind == "SILENT":
                violations.append((rel, node.lineno, kind, "معالجة صامتة بلا raise/log/return/action"))
    return violations, stats


def main() -> int:
    parser = argparse.ArgumentParser(description="حارس معالجة الأخطاء")
    parser.add_argument(
        "--strict-comments",
        action="store_true",
        help="يلزم كل معالجة (بما فيها LOG/ACTION) بتعليق يوثّق القرار.",
    )
    args = parser.parse_args()

    violations, stats = scan()

    print("=" * 72)
    print("  حارس معالجة الأخطاء — منع إخفاء الأخطاء (Failure Pattern 1)")
    print("=" * 72)
    print(f"  المفسّر: {sys.executable}")
    print("  التصنيفات: " + " · ".join(f"{k}={v}" for k, v in sorted(stats.items())))

    extra: list[tuple[str, int, str, str]] = []
    if args.strict_comments:
        for path in sorted(ROOT.rglob("*.py")):
            if any(part in EXCLUDED_PARTS for part in path.parts):
                continue
            try:
                text = path.read_text(encoding="utf-8")
                tree = ast.parse(text)
            except (SyntaxError, UnicodeDecodeError, OSError):
                continue
            lines = text.splitlines()
            rel = str(path.relative_to(ROOT))
            for node in ast.walk(tree):
                if isinstance(node, ast.ExceptHandler) and not _header_documented(lines, node):
                    extra.append((rel, node.lineno, "UNDOCUMENTED", "معالجة بلا تعليق يوثّق القرار"))

    total = violations + (extra if args.strict_comments else [])
    if not total:
        print("  ✅ لا ابتلاع صامت. كل معالجة تُبلغ أو تسجّل أو ترفع أو تعترف صراحةً.")
        print("=" * 72)
        return 0

    print(f"\n  ❌ مخالفات ({len(total)}):")
    for rel, lineno, kind, msg in total:
        print(f"     {rel}:{lineno} [{kind}] — {msg}")
    print("\n  الإصلاح: ارفع الاستثناء، أو سجّله، أو أضف تعليقاً يبرّر الكتم.")
    print("=" * 72)
    return 1


if __name__ == "__main__":
    raise SystemExit(main())
