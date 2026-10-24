"""ينقّح مخرجات الوكيل قبل نشرها على GitHub Issue (قد يكون عاماً).

يمنع تسريب: مفاتيح API، بيانات شخصية، ومحاولات حقن التوجيه في المخرجات.
يقتطع النص إلى حد أقصى ويضيف وسم AI للمادة 50 من EU AI Act.

الاستخدام:
    python scripts/sanitize_output.py agent_result.txt 12000
"""
from __future__ import annotations

import sys
from pathlib import Path

MAX_DEFAULT = 12_000


def sanitize(text: str, limit: int = MAX_DEFAULT) -> str:
    """تنقيح + اقتطاع. يعتمد على أنماط محلية موثّقة."""
    import re

    patterns = (
        re.compile(r"AIza[0-9A-Za-z_\-]{30,}"),
        re.compile(r"gsk_[0-9A-Za-z]{20,}"),
        re.compile(r"sk-or-v1-[0-9A-Za-z]{16,}"),
        re.compile(r"\bsk-[0-9A-Za-z]{20,}"),
        re.compile(r"gh[pousr]_[0-9A-Za-z]{20,}"),
        re.compile(r"(?i)\b(api[_-]?key|token|secret|password)\b\s*[:=]\s*['\"]?[^\s'\"]{6,}"),
        re.compile(r"\b[23]\d{13}\b"),  # رقم قومي مصري
        re.compile(r"\b01[0125]\d{8}\b"),  # هاتف مصري
        re.compile(r"[A-Za-z0-9._%+-]+@[A-Za-z0-9.-]+\.[A-Za-z]{2,}"),
    )
    cleaned = text
    for pattern in patterns:
        cleaned = pattern.sub("***تم التنقيح***", cleaned)

    if len(cleaned) > limit:
        cleaned = cleaned[: limit - 40] + "\n… (اقتُطع لتجاوز الحد المسموح)"
    return cleaned


def main() -> int:
    if len(sys.argv) < 2:
        print("الاستخدام: sanitize_output.py <file> [max_chars]", file=sys.stderr)
        return 2
    path = Path(sys.argv[1])
    try:
        limit = int(sys.argv[2]) if len(sys.argv) > 2 else MAX_DEFAULT
    except ValueError:
        limit = MAX_DEFAULT
    if not path.exists():
        print("لا توجد مخرجات.", file=sys.stderr)
        return 1
    raw = path.read_text(encoding="utf-8", errors="replace")
    sys.stdout.write(sanitize(raw, limit))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
