"""ملخّص تدقيق أمني لتبعيات المشروع — مجاني 100% وبلا أي مفتاح.

يقرأ مخرجات `pip-audit --format json` ويطبع تقريراً عربياً مختصراً.
يُستخدم محلياً وفي بوابة الجودة (CI)؛ لا يحتاج صلاحيات GitHub Security،
لأنه يعتمد على قاعدة بيانات استشارات PyPI العامة.

الاستخدام:
    pip-audit -r requirements.txt [..] --format json > audit-report.json || true
    python scripts/security_audit.py audit-report.json

ملاحظة: التقرير معلوماتي حالياً (لا يُفشل البناء) بسبب التثبيت الحصري
الموثّق في SECURITY.md: browser-use 0.9.7 → aiohttp==3.12.15.
"""
from __future__ import annotations

import json
import sys
from pathlib import Path


def summarize(report_path: Path) -> int:
    """يطبع ملخص الثغرات ويعيد عدد الحزم المتأثرة."""
    if not report_path.exists():
        print(f"⚠️  ملف التقرير غير موجود: {report_path}")
        return -1

    try:
        data = json.loads(report_path.read_text(encoding="utf-8"))
    except json.JSONDecodeError as exc:
        print(f"⚠️  تعذّر قراءة التقرير: {exc}")
        return -1

    dependencies = data.get("dependencies", [])
    affected = [dep for dep in dependencies if dep.get("vulns")]
    total_entries = sum(len(dep["vulns"]) for dep in affected)

    print("=" * 62)
    print("  تقرير التدقيق الأمني للتبعيات (PyPI Advisory DB)")
    print("=" * 62)
    print(f"  إجمالي الحزم المحلولة : {len(dependencies)}")
    print(f"  الحزم المتأثرة        : {len(affected)}")
    print(f"  إجمالي إدخالات الثغرات: {total_entries}")

    if not affected:
        print("  ✅ لا توجد ثغرات معروفة في الإصدارات المحلولة.")
        print("=" * 62)
        return 0

    for dep in affected:
        vulns = dep["vulns"]
        fixes = sorted({tuple(v.get("fix_versions") or []) for v in vulns})
        unique_ids = {v["id"] for v in vulns}
        print(f"\n  📦 {dep['name']} {dep['version']}")
        print(f"     إدخالات: {len(vulns)} | معرّفات فريدة: {len(unique_ids)}")
        print(f"     إصدارات الإصلاح المقترحة: {fixes or 'لا يوجد'}")
        for vuln in vulns[:3]:
            aliases = ", ".join((vuln.get("aliases") or [])[:2])
            suffix = f" ({aliases})" if aliases else ""
            print(f"       • {vuln['id']}{suffix}")

    print("\n  ℹ️  تفاصيل كاملة: راجع ملف التقرير أو SECURITY.md")
    print("=" * 62)
    return len(affected)


if __name__ == "__main__":
    target = Path(sys.argv[1]) if len(sys.argv) > 1 else Path("audit-report.json")
    raise SystemExit(0 if summarize(target) >= 0 else 1)
