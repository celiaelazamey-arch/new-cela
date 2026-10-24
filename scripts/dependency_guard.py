"""حارس التبعيات — يمنع الاستيرادات غير المعلنة (Undeclared Imports).

يكتشف الثغرات 1-3 في نموذج موثوقية مخرجات الوكيل:
  • Hallucinated APIs / Packages — حزمة مستوردة لكنها غير مثبّتة.
  • Version mismatch — حزمة مثبّتة لكنها غير معلنة ⇒ تُثبَّت عرضياً عبر
    تبعية transititive، وقد تختفي أو يتغيّر إصدارها بلا إنذار.
  • Slopsquatting — أي اسم حزمة غير معلن يظهر في الكود يُعدّ مشبوهاً حتى
    يُراجعه إنسان ويُضيفه صراحةً إلى ملفات المتطلبات.

القاعدة: **كل استيراد طرف ثالث في `src/` يجب أن يكون معلناً صراحةً** في أحد
ملفات `requirements*.txt`. هذا يحوّل الاعتماد العرضي إلى اعتماد مقصود.

يفصل بين نتيجتين:
  - «غير معلن» ⇒ خطأ حقيقي ⇒ يُفشل البناء.
  - «غير مثبّت في هذه البيئة» ⇒ تحذير فقط، لأن الحزم الاختيارية
    (qiskit/google-api/...) قد لا تكون مركّبة محلياً بينما CI يثبّتها.
    **الفحص يعتمد على الـ interpreter الذي يشغّله** — لذلك في CI يُشغَّل
    بعد خطوة تثبيت المتطلبات، ويُطبَع مسار الـ interpreter للشفافية.

مجاني 100% — المكتبة القياسية فقط (`ast` + `importlib.metadata`).

الاستخدام:
    python scripts/dependency_guard.py
    python scripts/dependency_guard.py --require-installed   # وضع صارم محلياً
"""
from __future__ import annotations

import argparse
import ast
import importlib.metadata as md
import pathlib
import sys

ROOT = pathlib.Path(__file__).resolve().parent.parent

# خريطة **بادئة المسار الكامل للاستيراد** → اسم الحزمة في PyPI.
# الترتيب مهم: الأطول أولاً (google.oauth2 قبل google).
MODULE_PREFIX_TO_DIST: tuple[tuple[str, str], ...] = (
    ("googleapiclient", "google-api-python-client"),
    ("google.oauth2", "google-auth"),
    ("google.auth", "google-auth"),
    ("google_auth_oauthlib", "google-auth-oauthlib"),
    ("google.genai", "google-genai"),
    ("google", "google-genai"),
    ("PIL", "pillow"),
    ("pydantic_settings", "pydantic-settings"),
    ("qiskit_aer", "qiskit-aer"),
    ("browser_use", "browser-use"),
    ("pypdf", "pypdf"),
    ("qiskit", "qiskit"),
    ("pydantic", "pydantic"),
    ("streamlit", "streamlit"),
)

# حزم تُستخدم كأدوات أو عبر واجهات CLI فقط — لا تُستورد مباشرة في src/.
TOOL_ONLY: frozenset[str] = frozenset(
    {"ruff", "pytest", "playwright", "httpx", "groq", "pip-audit", "python-dotenv"}
)

# حزم اختيارية بحكم التصميم: تُثبَّت من ملفات requirements-* منفصلة، وقد لا
# تكون موجودة في تثبيت النواة (أو في CI الذي يثبّت النواة فقط).
# ``browser-use`` تحديداً مقصود غيابه عن CI: نقله إلى الملف الاختياري هو
# جوهر إصلاح ثغرات aiohttp — فلا يجوز أن يُجبر الحارس على تثبيته.
OPTIONAL: frozenset[str] = frozenset(
    {"qiskit", "qiskit-aer", "google-api-python-client", "google-auth",
     "google-auth-oauthlib", "google-genai", "pillow", "browser-use", "playwright"}
)


def declared_distributions() -> set[str]:
    """أسماء الحزم المعلنة في كل ملفات requirements*.txt."""
    declared: set[str] = set()
    for req in sorted(ROOT.glob("requirements*.txt")):
        for raw in req.read_text(encoding="utf-8").splitlines():
            line = raw.split("#", 1)[0].strip()
            if not line or line.startswith("-"):
                continue
            name = line
            for sep in ("[", "<", ">", "=", "!", "~", ";"):
                name = name.split(sep, 1)[0]
            name = name.strip().lower().replace("_", "-")
            if name:
                declared.add(name)
    return declared


def distribution_for(module: str) -> str:
    """يحوّل مسار الاستيراد الكامل إلى اسم حزمة PyPI."""
    for prefix, dist in MODULE_PREFIX_TO_DIST:
        if module == prefix or module.startswith(prefix + "."):
            return dist
    return module.split(".")[0].lower().replace("_", "-")


def third_party_imports() -> dict[str, list[str]]:
    """{مسار_الاستيراد_الكامل: [ملف:سطر, ...]} لكل استيراد طرف ثالث في src/."""
    std = set(sys.stdlib_module_names)
    local = {"src", "tests", "scripts"}
    found: dict[str, list[str]] = {}
    for path in sorted((ROOT / "src").rglob("*.py")):
        try:
            tree = ast.parse(path.read_text(encoding="utf-8"))
        except (SyntaxError, UnicodeDecodeError):
            continue
        rel = str(path.relative_to(ROOT))
        for node in ast.walk(tree):
            names: list[str] = []
            if isinstance(node, ast.Import):
                names = [alias.name for alias in node.names]
            elif isinstance(node, ast.ImportFrom) and node.level == 0 and node.module:
                names = [node.module]
            for full in names:
                top = full.split(".")[0]
                if top in std or top in local:
                    continue
                found.setdefault(full, []).append(f"{rel}:{node.lineno}")
    return found


def is_installed(dist: str) -> bool:
    try:
        md.version(dist)
        return True
    except md.PackageNotFoundError:
        return False


def main() -> int:
    parser = argparse.ArgumentParser(description="حارس التبعيات")
    parser.add_argument(
        "--require-installed",
        action="store_true",
        help="يفشل أيضاً عند عدم تثبيت حزمة معلنة في البيئة الحالية (للاستخدام المحلي).",
    )
    args = parser.parse_args()

    declared = declared_distributions()
    imports = third_party_imports()

    # تجميع حسب الحزمة لتجنّب التكرار (google.oauth2 + googleapiclient ...)
    by_dist: dict[str, list[str]] = {}
    for full, locations in imports.items():
        dist = distribution_for(full)
        if dist in TOOL_ONLY:
            continue
        by_dist.setdefault(dist, []).extend(f"{full} @ {loc}" for loc in locations)

    undeclared: list[tuple[str, list[str]]] = []
    not_installed: list[tuple[str, list[str]]] = []

    for dist, locations in sorted(by_dist.items()):
        if dist not in declared:
            undeclared.append((dist, locations))
        if not is_installed(dist):
            not_installed.append((dist, locations))

    print("=" * 70)
    print("  حارس التبعيات — الاستيرادات مقابل المتطلبات المعلنة")
    print("=" * 70)
    print(f"  المفسّر: {sys.executable}")
    print(f"  حزم معلنة: {len(declared)} | حزم مستوردة: {len(by_dist)}")

    if not undeclared and not not_installed:
        print("  ✅ كل استيرادات الطرف الثالث معلنة ومثبّتة في هذه البيئة.")
        print("=" * 70)
        return 0

    if undeclared:
        print(f"\n  ❌ استيرادات غير معلنة ({len(undeclared)}) — اعتماد عرضي:")
        for dist, locations in undeclared:
            print(f"     '{dist}' غير موجودة في requirements*.txt")
            for loc in locations[:3]:
                print(f"        • {loc}")
        print("\n  أضف الحزمة صراحةً (مثلاً Pillow في requirements-ocr.txt).")

    if not_installed:
        marker = "❌" if args.require_installed else "⚠️ "
        print(
            f"\n  {marker} غير مثبّتة في هذه البيئة ({len(not_installed)}) "
            f"— {'خطأ' if args.require_installed else 'تحذير بيئي'}:"
        )
        for dist, locations in not_installed:
            tag = " (اختيارية — يُسمح بغيابها)" if dist in OPTIONAL else ""
            print(f"     '{dist}'{tag} | {locations[:1]}")
        if not args.require_installed:
            print("\n  ملاحظة: قد تكون الحزم مركّبة في venv آخر. شغّل الفاحص بنفس")
            print("  مفسّر المشروع، أو استخدم --require-installed لوضع صارم.")
            print("  في CI تُشغَّل هذه الخطوة بعد تثبيت كل المتطلبات.")

    # الوضع الصارم يفشل فقط على الحزم **الإلزامية** غير المثبّتة؛
    # الاختيارية يُسمح بغيابها (هذا هو مغزى فصلها في ملفات منفصلة).
    required_missing = [(d, loc) for d, loc in not_installed if d not in OPTIONAL]
    if args.require_installed and required_missing:
        print(f"\n  ❌ الوضع الصارم: {len(required_missing)} حزمة إلزامية غير مثبّتة:")
        for dist, locations in required_missing:
            print(f"     '{dist}' | {locations[:1]}")

    print("\n  ⚠️  لا تضف أي حزمة قبل التحقق من وجودها وسمعتها (Slopsquatting).")
    print("=" * 70)
    return 1 if undeclared or (args.require_installed and required_missing) else 0


if __name__ == "__main__":
    raise SystemExit(main())
