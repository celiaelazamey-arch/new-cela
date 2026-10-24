"""فاحص أسرار للملفات المتتبّعة — مجاني 100%، بلا أي تبعية خارجية.

يفحص كل ملف في فهرس git بحثاً عن أنماط مفاتيح API ومفاتيح خاصة،
ويُفشل العملية عند أي تطابق غير مدرج في قائمة السماح. صُمّم ليُشغَّل
في بوابة الجودة (CI) عند كل دفع — البند 5 من نموذج التهديدات.

قائمة السماح تضم قيم اختبار وهمية مقصودة (تُستخدم لاختبار دالة التنقيح)،
وليست أسراراً حقيقية. أي قيمة جديدة تُعتبر تسريباً حتى يُراجعها إنسان.

الاستخدام:
    python scripts/secret_scan.py
"""
from __future__ import annotations

import re
import subprocess
from pathlib import Path

# ---- الأنماط المكتشفة (تطابق src/core/telemetry.py + إضافات GitHub) ----
PATTERNS: tuple[tuple[str, re.Pattern[str]], ...] = (
    ("Google API key", re.compile(r"AIza[0-9A-Za-z_\-]{30,}")),
    ("Groq key", re.compile(r"gsk_[0-9A-Za-z]{20,}")),
    ("OpenRouter key", re.compile(r"sk-or-v1-[0-9A-Za-z]{16,}")),
    ("OpenAI-style key", re.compile(r"\bsk-[0-9A-Za-z]{20,}")),
    ("GitHub token", re.compile(r"gh[pousr]_[0-9A-Za-z]{20,}")),
    ("Private key", re.compile(r"-----BEGIN (?:RSA|OPENSSH|EC|DSA) PRIVATE KEY-----")),
)

# ---- قيم وهمية مقصودة داخل اختبارات التنقيح — ليست أسراراً ----
ALLOWLIST: frozenset[str] = frozenset(
    {
        "AIzaSyD-1234567890abcdefghijklmnopqrstu",
        "AIzaSyD-1234567890abcdefghijklmnop",
        "gsk_abcdefghijklmnopqrstuvwxyz123456",
        "sk-or-v1-abcdef1234567890abcdef",
        "AIzaSyDUMMY1234567890123456789012345",
    }
)

# الملفات التي لا تخضع للفحص (نموذج المتغيرات + هذا الفاحص نفسه).
SKIP_FILES: frozenset[str] = frozenset({".env.example", "scripts/secret_scan.py"})


def tracked_files() -> list[Path]:
    """يعيد الملفات المتتبّعة في git فقط — لا يفحص .env ولا الكاش ولا .venv."""
    result = subprocess.run(
        ["git", "ls-files", "-z"], capture_output=True, text=True, check=True
    )
    return [Path(item) for item in result.stdout.split("\0") if item and Path(item).is_file()]


def scan() -> list[tuple[str, int, str, str]]:
    """يفحص الملفات ويعيد قائمة بالتطابقات غير المسموح بها."""
    findings: list[tuple[str, int, str, str]] = []
    for path in tracked_files():
        if path.name in SKIP_FILES or path.name == "secret_scan.py":
            continue
        try:
            text = path.read_text(encoding="utf-8")
        except (UnicodeDecodeError, OSError):
            continue  # ملفات ثنائية — تُترك لفاحص متخصص لاحقاً
        for lineno, line in enumerate(text.splitlines(), 1):
            for label, pattern in PATTERNS:
                for match in pattern.finditer(line):
                    value = match.group(0)
                    if value in ALLOWLIST:
                        continue
                    findings.append((str(path), lineno, label, f"{value[:10]}…"))
    return findings


def main() -> int:
    findings = scan()
    print("=" * 58)
    print("  فحص الأسرار في الملفات المتتبّعة (git ls-files)")
    print("=" * 58)
    if not findings:
        print("  ✅ لا توجد مفاتيح أو أسرار مكشوفة في الملفات المتتبّعة.")
        print(f"  الأنماط المفحوصة: {len(PATTERNS)} | قيم السماح: {len(ALLOWLIST)}")
        print("=" * 58)
        return 0

    print(f"  ❌ تم العثور على {len(findings)} تطابقاً مشتبهاً:")
    for path, lineno, label, preview in findings:
        print(f"     {path}:{lineno} — {label} — {preview}")
    print("\n  راجع كل تطابق. إن كان قيمة اختبار مقصودة، أضفها إلى ALLOWLIST")
    print("  مع توضيح السبب. لا تُضف مفاتيح حقيقية أبداً.")
    print("=" * 58)
    return 1


if __name__ == "__main__":
    raise SystemExit(main())
