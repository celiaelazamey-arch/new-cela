"""
نقطة الدخول الرئيسية لنظام new-cela
====================================
الاستخدام المحلي:
    python -m src.main --task "اكتب دالة Fibonacci في Python" --mode reason
    python -m src.main --task "ابحث عن سعر الذهب اليوم" --mode browser
    python -m src.main --task "python --version" --mode code

الذاكرة الاستراتيجية مفعّلة افتراضياً في النمط reason:
    python -m src.main --task "..." --memory-path .agent_memory/memory.jsonl
    python -m src.main --task "..." --no-memory        # تعطيل كامل للقراءة والكتابة

كل تشغيل يُسجَّل في .agent_runs/ (يقرأه لوحة المتابعة):
    python -m src.dashboard                             # فتح اللوحة على 8501

في GitHub Actions: تُقرأ المهمة من الوسيط --task الذي يمرره سير العمل.
"""
from __future__ import annotations

import argparse
import asyncio
import logging
import sys
from pathlib import Path

from src.browser.browser_engine import BrowserEngine
from src.config import settings
from src.core.engine import AgentEngine
from src.core.memory import MemoryRetriever, MemoryStore
from src.core.policy import AutoApprove, PolicyEngine
from src.core.quota import QuotaGuard
from src.core.task import TaskRequest, TaskResult, TaskStatus, ToolCall
from src.core.telemetry import RunArchive
from src.execution.sandbox import SandboxExecutor
from src.providers.llm_router import LLMRouter
from src.tools.builtin import build_default_registry
from src.tools.registry import ToolContext

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s | %(levelname)s | %(name)s | %(message)s",
)
logger = logging.getLogger("new-cela")


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        prog="new-cela",
        description="نظام وكلاء ذكي مستقل — تشغيل مجاني بالكامل",
    )
    # --task يبقى مطلوباً لكل الأنماط عدا --health أو --plans.
    parser.add_argument("--task", "-t", default=None, help="المهمة المطلوبة من الوكيل")
    parser.add_argument(
        "--health",
        action="store_true",
        help="فحص جاهزية المزودات وحالة قواطع الدائرة — بلا نداءات شبكية",
    )
    parser.add_argument(
        "--plans",
        action="store_true",
        help="عرض جدول مقارنة خطط التسعير والحصص في صفحة واحدة (البند 7)",
    )
    parser.add_argument(
        "--niches",
        action="store_true",
        help="عرض استراتيجية النيش العمودي ومقياس القرار بعد 90 يوماً",
    )
    parser.add_argument(
        "--plan",
        choices=["free", "pro", "team", "business", "enterprise"],
        default=None,
        help="اختيار خطة الحصص للمهمة (الافتراضي: free بسقف 40 نموذج / 20 أداة)",
    )
    parser.add_argument(
        "--json",
        action="store_true",
        help="إخراج نتيجة --health أو --plans بصيغة JSON (للـ CI والمراقبة)",
    )
    parser.add_argument(
        "--mode",
        "-m",
        choices=["reason", "browser", "code"],
        default="reason",
        help="نمط التنفيذ: reason (تفكير) | browser (تصفح) | code (تنفيذ أوامر)",
    )
    parser.add_argument("--max-steps", type=int, default=None, help="الحد الأقصى لخطوات الخطة")
    parser.add_argument(
        "--max-retries",
        type=int,
        default=2,
        help="حد إعادة محاولة كل خطوة عند رفضها من المقيّم (Critic)",
    )
    parser.add_argument(
        "--no-memory",
        action="store_true",
        help="تعطيل الذاكرة الاستراتيجية لهذه المهمة (لا قراءة ولا كتابة)",
    )
    parser.add_argument(
        "--memory-path",
        default=None,
        help="مسار ملف الذاكرة (JSONL) — الافتراضي من الإعدادات",
    )
    parser.add_argument(
        "--memory-top-k",
        type=int,
        default=None,
        help="عدد الذكريات المسترجعة لكل استعلام (الافتراضي 4)",
    )
    parser.add_argument(
        "--runs-dir",
        default=None,
        help="مجلد سجل التشغيلات الذي تقرأه لوحة المتابعة",
    )
    parser.add_argument(
        "--no-telemetry",
        action="store_true",
        help="عدم كتابة تقرير تشغيل (لا سجل ولا مراقبة)",
    )

    # ---- أدوات وسياسة وميزانية (عقود P0) ----
    parser.add_argument(
        "--read",
        action="append",
        default=None,
        metavar="PATH",
        help="ملف يُقرأ كسياق قبل التخطيط (يمكن تكراره)",
    )
    parser.add_argument(
        "--search",
        default=None,
        metavar="QUERY",
        help="بحث نصي في مساحة العمل كسياق قبل التخطيط",
    )
    parser.add_argument(
        "--allow-root",
        action="append",
        default=None,
        metavar="DIR",
        help="جذر مسموح للأدوات (افتراضياً مجلد العمل الحالي)",
    )
    parser.add_argument(
        "--grant",
        action="append",
        default=None,
        metavar="SCOPE",
        help="نطاق إضافي ممنوح للأدوات (مثال: shell.exec)",
    )
    parser.add_argument(
        "--yes",
        action="store_true",
        help="موافقة صريحة على العمليات الحساسة (بدون سؤال تفاعلي)",
    )
    parser.add_argument(
        "--max-model-calls", type=int, default=40, help="سقف نداءات النموذج لكل مهمة"
    )
    parser.add_argument(
        "--max-tool-calls", type=int, default=20, help="سقف نداءات الأدوات لكل مهمة"
    )
    parser.add_argument(
        "--allow-unverified-provider",
        action="store_true",
        help="السماح بمزود غير متحقَّق من مجانيته (ممنوع افتراضياً)",
    )
    parser.add_argument(
        "--require-verified",
        action="store_true",
        help="إنهاء التشغيل برمز 2 إذا لم تجتز المهمة شروط التحقق",
    )
    parser.add_argument(
        "--knowledge",
        default=None,
        metavar="CORPUS",
        help="فهرس معرفة JSONL يُحقن سياقه الموثّق في الطلب (اختياري)",
    )
    parser.add_argument(
        "--learn",
        default=None,
        metavar="QUERY",
        help="جلب أوراق علمية مجانية وإضافتها للفهرس قبل التنفيذ (اختياري)",
    )
    parser.add_argument(
        "--learn-limit",
        type=int,
        default=3,
        help="عدد الأوراق المجلوبة لكل استعلام (افتراضياً 3)",
    )
    parser.add_argument(
        "--no-awareness",
        action="store_true",
        help="تعطيل منظور الوعي الوظيفي (انتباه محدود + تقدير ثقة)",
    )
    parser.add_argument(
        "--mcp-config",
        default=None,
        metavar="FILE",
        help="ملف mcp.json لخوادم MCP (لا يُشغّل شيئاً بلا --mcp-server و --yes)",
    )
    parser.add_argument(
        "--mcp-server",
        action="append",
        default=None,
        metavar="NAME",
        help="اسم خادم MCP لتفعيله (يتكرر)، يتطلب --yes لأنها عملية خارجية",
    )
    parser.add_argument(
        "--ocr",
        action="append",
        default=None,
        metavar="FILE",
        help="صورة/PDF لاستخراج نصها وإضافتها إلى فهرس المعرفة (يتكرر)",
    )
    return parser


def build_quota(args: argparse.Namespace) -> QuotaGuard:
    """حارس التكلفة — يمنع المزود غير المتحقق وتجاوز السقف."""
    plan_name = getattr(args, "plan", None)
    if plan_name and args.max_model_calls == 40 and args.max_tool_calls == 20:
        from src.core.pricing import build_plan_guard

        return build_plan_guard(
            plan_name,
            allow_unverified=args.allow_unverified_provider,
            allow_paid=False,
        )
    return QuotaGuard(
        max_model_calls=args.max_model_calls,
        max_tool_calls=args.max_tool_calls,
        allow_unverified=args.allow_unverified_provider,
    )


def build_policy(args: argparse.Namespace) -> PolicyEngine:
    """سياسة الأدوات: نطاقات محدودة + موافقة صريحة فقط عند طلبها."""
    from src.core.policy import DEFAULT_SCOPES

    return PolicyEngine(
        allowed_roots=args.allow_root or [Path.cwd()],
        granted_scopes=set(DEFAULT_SCOPES) | set(args.grant or []),
        approval_handler=AutoApprove() if args.yes else None,
    )


def build_request(args: argparse.Namespace) -> TaskRequest:
    """يحوّل وسائط CLI إلى طلب مهمة بعقد صريح."""
    calls = []
    for path in args.read or []:
        calls.append(ToolCall(name="read_file", args={"path": path}, purpose="قراءة سياق"))
    if args.search:
        calls.append(ToolCall(name="search_files", args={"query": args.search}, purpose="بحث"))
    return TaskRequest(task=args.task, context_tools=calls)


def build_memory(args: argparse.Namespace) -> MemoryRetriever | None:
    """ينشئ طبقة الذاكرة الاستراتيجية — أو None عند التعطيل الصريح."""
    if args.no_memory:
        logger.info("الذاكرة الاستراتيجية معطّلة لهذه المهمة (--no-memory).")
        return None

    store = MemoryStore.from_settings(settings, path=args.memory_path)
    logger.info("الذاكرة الاستراتيجية: %d ذكرى محمّلة من %s", len(store), store.path)
    return MemoryRetriever(
        store,
        top_k=args.memory_top_k or settings.agent_memory_top_k,
        min_score=settings.agent_memory_min_score,
    )


def build_telemetry(args: argparse.Namespace) -> RunArchive | None:
    """ينشئ أرشيف التشغيلات — أو None عند التعطيل الصريح."""
    if args.no_telemetry:
        logger.info("سجل التشغيلات معطّل لهذه المهمة (--no-telemetry).")
        return None

    archive = RunArchive(
        path=args.runs_dir or settings.agent_runs_path,
        keep=settings.agent_runs_keep,
    )
    logger.info("سجل التشغيلات: %s", archive.path)
    return archive


def build_knowledge(args: argparse.Namespace):
    """
    يبني فهرس المعرفة قبل التشغيل — ويعيد None بصراحة عند عدم الطلب.

    `--learn` يجلب أوراقاً مجانية ويفهرسها؛ وفشل الشبكة لا يمنع التشغيل.
    """
    path = getattr(args, "knowledge", None)
    query = getattr(args, "learn", None)
    ocr_paths = getattr(args, "ocr", None)
    if not path and not query and not ocr_paths:
        return None

    from src.knowledge.corpus import CorpusIndex

    corpus = CorpusIndex(path or ".agent_knowledge/corpus.jsonl")

    if query:
        from src.knowledge.sources import PaperSearch, ingest_papers

        try:
            report = PaperSearch().search(query, limit=getattr(args, "learn_limit", 3))
        except Exception as exc:  # noqa: BLE001 — البحث رفاهية لا شرط للتشغيل
            logger.warning(
                "تعذّر جلب الأبحاث (%s) — نُكمل بالفهرس الحالي.", type(exc).__name__
            )
        else:
            logger.info("أبحاث: %s", report.summary())
            if report.papers:
                logger.info("قطع معرفة مضافة من الأوراق: %d", ingest_papers(report, corpus))
            for name, reason in report.errors.items():
                logger.warning("تعذّر جلب %s (%s) — نُكمل بالفهرس الحالي.", name, reason)

    ocr_paths = getattr(args, "ocr", None)
    if ocr_paths:
        from src.ocr.pipeline import ingest_paths

        try:
            report = ingest_paths(ocr_paths, corpus)
        except Exception as exc:  # noqa: BLE001 — OCR رفاهية لا شرط
            logger.warning("تعذّر OCR (%s) — نُكمل بلا نص المستند.", type(exc).__name__)
        else:
            logger.info("OCR: %s", report.summary())
            for path, reason in report.failures.items():
                logger.warning("تعذّر استخراج %s (%s).", path, reason)

    logger.info("فهرس المعرفة: %d قطعة من %d مصدراً", len(corpus), len(corpus.sources()))
    return corpus


def start_mcp_servers(args: argparse.Namespace, registry) -> list:
    """
    يشغّل خوادم MCP المطلوبة ويسجّل أدواتها.

    شروط صريحة: `--mcp-config` + `--mcp-server` + `--yes`. بلا `--yes` لا تُشغَّل
    أي عملية خارجية، وتُطبع رسالة توضح ما كان سيحدث.
    """
    config_path = getattr(args, "mcp_config", None)
    requested = getattr(args, "mcp_server", None)
    if not config_path or not requested:
        return []

    from src.mcp.bridge import McpBridge
    from src.mcp.client import StdioMcpClient
    from src.mcp.config import McpConfigError, load_config

    if not getattr(args, "yes", False):
        logger.warning(
            "خوادم MCP لم تُشغَّل: تحتاج موافقة صريحة (--yes) لتشغيل عملية خارجية."
        )
        return []

    try:
        config = load_config(config_path)
    except McpConfigError as exc:
        logger.error("إعداد MCP غير صالح: %s", exc)
        return []

    clients: list = []
    for name in requested:
        try:
            server = config.get(name)
        except McpConfigError as exc:
            logger.error("%s — تم تجاوزه.", exc)
            continue
        client = StdioMcpClient(
            server.argv(),
            name=name,
            env=server.env,
            timeout=float(getattr(settings, "agent_timeout", 30) or 30),
        )
        try:
            client.start()
            client.initialize()
            tools = client.list_tools()
        except Exception as exc:  # noqa: BLE001 — خادم معطوب لا يُسقط المهمة
            logger.error("تعذّر بدء خادم MCP «%s» (%s).", name, type(exc).__name__)
            client.close()
            continue

        registered = McpBridge(client, name, tools).register_into(registry)
        clients.append(client)
        logger.info("MCP «%s»: %d أداة — %s", name, len(registered), ", ".join(registered))

    return clients


async def run_agent(args: argparse.Namespace) -> str | TaskResult:
    """يوجّه المهمة إلى الوحدة المناسبة حسب النمط المختار."""
    # نمط code لا يحتاج نموذجاً لغوياً — يُنفّذ مباشرة في البيئة المعزولة
    if args.mode == "code":
        result = await SandboxExecutor(timeout=settings.agent_timeout).run(args.task)
        return result.summary()

    router = LLMRouter()
    router.require_any_provider()

    if args.mode == "browser":
        result = await BrowserEngine(router).run_task(args.task, max_steps=args.max_steps)
        return str(result)

    registry = build_default_registry(
        ToolContext(policy=build_policy(args), quota=build_quota(args))
    )
    mcp_clients: list = []
    try:
        mcp_clients = start_mcp_servers(args, registry)
        engine = AgentEngine(
            router,
            registry=registry,
            quota=registry.quota,
            memory=build_memory(args),
            telemetry=build_telemetry(args),
            knowledge=build_knowledge(args),
            awareness=not getattr(args, "no_awareness", False),
        )
        result = await engine.run(build_request(args))
        logger.info("نتيجة التحقق: %s", result.summary())
        return result
    finally:
        for client in mcp_clients:
            client.close()


def main() -> int:
    args = build_parser().parse_args()

    # ---- فحص الجاهزية: لا يُجري نداءات شبكية ولا ينفّذ أي مهمة ----
    if args.health:
        import json as _json

        report = LLMRouter().health()
        if args.json:
            print(_json.dumps(report, ensure_ascii=False, indent=2))
        else:
            print("=== فحص جاهزية النظام ===")
            for name, info in report["providers"].items():
                state = "🟢 جاهز" if not info["circuit_open"] else "🔴 دائرة مفتوحة (تهدئة)"
                print(f"  {name}: {state}")
            print(f"  الحالة العامة: {'✅ سليم' if report['healthy'] else '⚠️ غير جاهز'}")
            print(f"  قواطع الدائرة: {report['circuit_breaker']}")
        return 0 if report["healthy"] else 2

    # ---- عرض مقارنة الخطط والحصص: بلا نداءات شبكية ولا تنفيذ مهمة ----
    if getattr(args, "plans", False):
        import json as _json

        from src.core.pricing import PLAN_CATALOG, render_pricing_table

        if args.json:
            payload = {
                tier.value: {
                    "name_ar": spec.name_ar,
                    "monthly_price_egp": spec.monthly_price_egp,
                    "monthly_price_usd": spec.monthly_price_usd,
                    "annual_discount_pct": spec.annual_discount_pct,
                    "included_seats": spec.included_seats,
                    "monthly_tasks_quota": spec.monthly_tasks_quota,
                    "max_model_calls_per_task": spec.max_model_calls_per_task,
                    "max_tool_calls_per_task": spec.max_tool_calls_per_task,
                    "watermark_reports": spec.watermark_reports,
                }
                for tier, spec in PLAN_CATALOG.items()
            }
            print(_json.dumps(payload, ensure_ascii=False, indent=2))
        else:
            print(render_pricing_table())
        return 0

    # ---- عرض النيشات العمودية ومقياس الـ 90 يوماً: بلا نداءات شبكية ----
    if getattr(args, "niches", False):
        import json as _json

        from src.core.niche_strategy import NICHE_CATALOG, render_niche_playbook

        if args.json:
            payload = {
                niche.value: spec.to_dict() for niche, spec in NICHE_CATALOG.items()
            }
            print(_json.dumps(payload, ensure_ascii=False, indent=2))
        else:
            print(render_niche_playbook())
        return 0

    # ---- التحقق اليدوي من --task (لأن --health و --plans و --niches تعفي منه) ----
    if not args.task or not args.task.strip():
        print("❌ الوسيط --task مطلوب (إلا مع --health).", file=sys.stderr)
        return 2

    try:
        output = asyncio.run(run_agent(args))
    except Exception as exc:  # noqa: BLE001 — مقصود: حاجز أخير لواجهة CLI
        logger.error("فشل تنفيذ المهمة: %s", exc)
        print(f"❌ {exc}", file=sys.stderr)
        return 1

    print("\n==================== نتيجة الوكيل ====================")
    # إفصاح المادة 50(1) من EU AI Act — ساري منذ 2 أغسطس 2026.
    # يُوضع بعد عنوان النتيجة ليظهر في كل مخرجات CLI وتعليقات Issues.
    print("🤖 هذا المحتوى مولّد بواسطة وكيل ذكاء اصطناعي (Gemini/Groq) — راجعه بشرياً.")
    if isinstance(output, TaskResult):
        print(output.answer)
        print(f"\n--- التحقق ---\n{output.summary()}")
        if output.confidence:
            print(f"\n--- منظور الوعي ---\n{output.awareness}")
        for check in output.checks:
            mark = "✅" if check.passed else ("❌" if check.required else "➖")
            print(f"  {mark} {check.name}" + (f" — {check.detail}" if check.detail else ""))
        if output.status is TaskStatus.BLOCKED:
            return 2
        if args.require_verified and not output.verified:
            return 2
        return 0

    print(output)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
