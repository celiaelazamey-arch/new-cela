"""فاحص البيانات الشخصية (PII) في الملفات المتتبّعة — مجاني، بلا تبعيات.

يهدف إلى منع تسريب بيانات شخصية إلى مستودع عام، دعماً لامتثال قانون حماية
البيانات الشخصية المصري 151/2020 (المادة 14 — النقل عبر الحدود) وGDPR.
يفحص: الرقم القومي المصري، أرقام الهواتف المصرية، البريد الإلكتروني،
وبطاقات الائتمان. يُشغَّل في بوابة الجودة عند كل دفع.

قائمة السماح تضم قيماً وهمية مقصودة في الاختبارات (نطاق .invalid/.example).

الاستخدام:
    python scripts/pii_scan.py
"""
from __future__ import annotations

import re
import subprocess
from pathlib import Path

# ---- الأنماط ----
PATTERNS: tuple[tuple[str, re.Pattern[str]], ...] = (
    # الرقم القومي المصري: يبدأ بـ 2 أو 3 (قرن الميلاد) + 13 رقماً = 14
    ("National ID (EG)", re.compile(r"\b[23]\d{13}\b")),
    # هاتف مصري: 01 + (0/1/2/5) + 8 أرقام = 11
    ("Mobile (EG)", re.compile(r"\b01[0125]\d{8}\b")),
    # بريد إلكتروني
    ("Email", re.compile(r"[A-Za-z0-9._%+-]+@[A-Za-z0-9.-]+\.[A-Za-z]{2,}")),
    # بطاقة ائتمان: أرقام متصلة فقط (13-19) — لا نماذج PDF/xref ولا تواريخ
    ("Payment card", re.compile(r"\b\d{13,19}\b")),
)


def _passes_luhn(digits: str) -> bool:
    """يتحقق من رقم البطاقة بخوارزمية Luhn — يمنع إنذارات الأرقام العشوائية."""
    total = 0
    for index, char in enumerate(reversed(digits)):
        value = int(char)
        if index % 2 == 1:
            value *= 2
            if value > 9:
                value -= 9
        total += value
    return total % 10 == 0

# ---- قيم وهمية مقصودة في الاختبارات ----
ALLOWLIST: frozenset[str] = frozenset(
    {
        "celia@example.invalid",
        "user@example.com",
        "test@example.com",
        "01012345678",
    }
)

# ملفات لا تُفحص (هذا الفاحص نفسه + نموذج المتغيرات + ملفات التوثيق التي
# تذكر أمثلة الأنماط داخل تعليقات — تُستثنى لتجنّب الإنذار الذاتي).
SKIP_SUFFIXES: tuple[str, ...] = ("secret_scan.py", "pii_scan.py", ".env.example")


def tracked_files() -> list[Path]:
    """الملفات المتتبّعة في git فقط (لا .env ولا .venv ولا الكاش)."""
    result = subprocess.run(
        ["git", "ls-files", "-z"], capture_output=True, text=True, check=True
    )
    return [Path(item) for item in result.stdout.split("\0") if item and Path(item).is_file()]


def scan() -> list[tuple[str, int, str, str]]:
    """يعيد التطابقات غير المسموح بها."""
    findings: list[tuple[str, int, str, str]] = []
    for path in tracked_files():
        if any(str(path).endswith(suffix) for suffix in SKIP_SUFFIXES):
            continue
        try:
            text = path.read_text(encoding="utf-8")
        except (UnicodeDecodeError, OSError):
            continue
        for lineno, line in enumerate(text.splitlines(), 1):
            # تخطي أسطر التوثيق التي تشرح الأنماط نفسها
            if "re.compile(" in line or "PATTERNS" in line:
                continue
            for label, pattern in PATTERNS:
                for match in pattern.finditer(line):
                    value = match.group(0).strip()
                    if value in ALLOWLIST or ".invalid" in value or ".example" in value:
                        continue
                    # بطاقة الائتمان: تجاهل إن كانت كل الأرقام متطابقة،
                    # أو لم تجتز فحص Luhn (مثل جداول xref في ملفات PDF).
                    digits = re.sub(r"\D", "", value)
                    if label == "Payment card" and (
                        len(set(digits)) <= 2 or not _passes_luhn(digits)
                    ):
                        continue
                    findings.append((str(path), lineno, label, f"{value[:12]}…"))
    return findings


def main() -> int:
    findings = scan()
    print("=" * 60)
    print("  فحص البيانات الشخصية (PII) في الملفات المتتبّعة")
    print("=" * 60)
    if not findings:
        print("  ✅ لا توجد بيانات شخصية مكشوفة في الملفات المتتبّعة.")
        print(f"  الأنماط المفحوصة: {len(PATTERNS)} | قيم السماح: {len(ALLOWLIST)}")
        print("=" * 60)
        return 0

    print(f"  ❌ تم العثور على {len(findings)} تطابقاً مشتبهاً:")
    for path, lineno, label, preview in findings:
        print(f"     {path}:{lineno} — {label} — {preview}")
    print("\n  ⚠️  تذكير قانوني: رفع بيانات شخصية إلى مستودع عام قد يُعدّ نقلاً")
    print("     عبر الحدود بموجب المادة 14 من قانون 151/2020. راجع فوراً.")
    print("=" * 60)
    return 1


if __name__ == "__main__":
    raise SystemExit(main())
